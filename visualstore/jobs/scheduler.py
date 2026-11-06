"""Table-driven job scheduler (design §6).

One coroutine polls every profile's job table, hands compute jobs to the process pool and
io jobs to a single worker thread, and persists whatever the pool reports. It is the only
code that moves a job between states while the server is running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_from_str, id_to_str
from visualstore.jobs.compute.handlers import HANDLERS as COMPUTE_HANDLERS
from visualstore.jobs.compute.protocol import ComputeMessage, ComputeRequest
from visualstore.jobs.results import RESULT_SINKS, SinkContext
from visualstore.profiles import ProfileNotFound, ProfilePaths, ProfileRegistry

log = logging.getLogger(__name__)

EXCLUSIVE_COMPUTE = {"match", "build_index"}  # one index writer per profile (design §6.4)


@dataclass
class IoContext:
    profile: str
    paths: ProfilePaths
    backup_dir: Path
    global_db_path: Path
    settings: dict
    params: dict
    report: Callable[[dict], None]  # thread-safe; may be called from the handler's thread
    model_dir: Path = Path("/data/models")

    def open_metadata(self) -> sqlite3.Connection:
        """A connection of the handler's own, for use inside the worker thread. Close it."""
        from visualstore.db.connection import connect

        return connect(self.paths.metadata_db)

    def open_vectors(self) -> sqlite3.Connection:
        from visualstore.db.connection import connect

        return connect(self.paths.vectors_db)


IoHandler = Callable[[IoContext], dict]
Planner = Callable[[ProfileRegistry], None]


class ComputeBackend(Protocol):
    size: int

    def submit(self, request: ComputeRequest) -> None: ...
    def poll(self) -> list[ComputeMessage]: ...
    def reap(self) -> list[int]: ...
    def alive_count(self) -> int: ...


class Scheduler:
    def __init__(
        self,
        registry: ProfileRegistry,
        pool: ComputeBackend,
        io_handlers: dict[str, IoHandler],
        backup_dir: Path,
        model_dir: Path = Path("/data/models"),
        planners: Sequence[Planner] = (),
        poll_interval: float = 0.5,
        plan_interval: float = 60.0,
    ) -> None:
        self.registry = registry
        self.pool = pool
        self.io_handlers = io_handlers
        self.backup_dir = backup_dir
        self.model_dir = model_dir
        self.planners = list(planners)
        self.poll_interval = poll_interval
        self.plan_interval = plan_interval
        self.in_flight: dict[str, str] = {}  # compute job id (str) -> profile name
        self.in_flight_pid: dict[str, int] = {}  # compute job id (str) -> pid of its child
        self.in_flight_types: dict[str, str] = {}  # compute job id (str) -> job type
        self._io_task: asyncio.Task | None = None
        self._loop_task: asyncio.Task | None = None
        self._last_plan: float | None = None

    # -- lifecycle ---------------------------------------------------------------------

    def requeue_interrupted(self) -> int:
        total = 0
        for name in self.registry.names():
            total += jobs_db.requeue_interrupted(self.registry.conn(name))
        if total:
            log.info("requeued %d job(s) interrupted by the previous run", total)
        return total

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._run(), name="visualstore-scheduler")

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        await self.wait_io()

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the loop must survive any single bad tick
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.poll_interval)

    async def tick(self) -> None:
        self._drain_pool()
        self._dispatch_compute()
        self._dispatch_io()
        self._plan()

    async def wait_io(self) -> None:
        if self._io_task is not None:
            await self._io_task

    # -- picking -----------------------------------------------------------------------

    def _pick(self, kind: str) -> tuple[str, sqlite3.Row] | None:
        best: tuple[tuple[int, str], str, sqlite3.Row] | None = None
        for name in self.registry.names():
            row = jobs_db.next_queued(self.registry.conn(name), kind)
            if row is None:
                continue
            key = (-int(row["priority"]), str(row["created_at"]))
            if best is None or key < best[0]:
                best = (key, name, row)
        return None if best is None else (best[1], best[2])

    # -- compute -----------------------------------------------------------------------

    def _busy_exclusive(self) -> set[str]:
        return {
            self.in_flight[j] for j, t in self.in_flight_types.items() if t in EXCLUSIVE_COMPUTE
        }

    def _pick_compute(self) -> tuple[str, sqlite3.Row] | None:
        busy = self._busy_exclusive()
        best: tuple[tuple[int, str], str, sqlite3.Row] | None = None
        for name in self.registry.names():
            exclude = tuple(EXCLUSIVE_COMPUTE) if name in busy else ()
            row = jobs_db.next_queued(self.registry.conn(name), "compute", exclude_types=exclude)
            if row is None:
                continue
            key = (-int(row["priority"]), str(row["created_at"]))
            if best is None or key < best[0]:
                best = (key, name, row)
        return None if best is None else (best[1], best[2])

    def _dispatch_compute(self) -> None:
        while len(self.in_flight) < self.pool.size:
            picked = self._pick_compute()
            if picked is None:
                return
            profile, row = picked
            conn = self.registry.conn(profile)
            if row["type"] not in COMPUTE_HANDLERS:
                jobs_db.mark_failed(conn, row["id"], f"unknown job type {row['type']!r}")
                continue
            jobs_db.mark_running(conn, row["id"])
            job_id = id_to_str(row["id"])
            self.in_flight[job_id] = profile
            self.in_flight_types[job_id] = row["type"]
            self.pool.submit(
                ComputeRequest(
                    job_id=job_id,
                    profile=profile,
                    type=row["type"],
                    params=json.loads(row["params"]),
                )
            )

    def _drain_pool(self) -> None:
        self._fail_jobs_of_dead_children()
        for msg in self.pool.poll():
            profile = self.in_flight.get(msg.job_id)
            if profile is None:
                continue
            if msg.kind == "started":
                self.in_flight_pid[msg.job_id] = int(msg.payload.get("pid", 0))
                continue
            if msg.kind != "progress":
                self._forget(msg.job_id)
            try:
                conn = self.registry.conn(profile)
            except ProfileNotFound:
                continue  # profile deleted while the job was running
            job_id = id_from_str(msg.job_id)
            if msg.kind == "progress":
                jobs_db.set_progress(conn, job_id, msg.payload)
            elif msg.kind == "result":
                self._finish(conn, job_id, msg.payload, profile)
            else:
                jobs_db.record_failure(conn, job_id, msg.payload.get("error", "unknown error"))

    def _forget(self, job_id: str) -> None:
        self.in_flight.pop(job_id, None)
        self.in_flight_pid.pop(job_id, None)
        self.in_flight_types.pop(job_id, None)

    def _fail_jobs_of_dead_children(self) -> None:
        """A child that died (OOM, crash) never answers; count the attempt and move on.
        The pool has already respawned the child, so the slot is usable again."""
        dead = set(self.pool.reap())
        if not dead:
            return
        for job_id, pid in list(self.in_flight_pid.items()):
            if pid not in dead:
                continue
            profile = self.in_flight.get(job_id)
            self._forget(job_id)
            log.warning("compute worker %d died while running job %s", pid, job_id)
            if profile is None:
                continue
            try:
                conn = self.registry.conn(profile)
            except ProfileNotFound:
                continue
            jobs_db.record_failure(conn, id_from_str(job_id), f"compute worker died (pid {pid})")

    def _finish(self, conn: sqlite3.Connection, job_id: bytes, result: dict, profile: str) -> None:
        row = jobs_db.get(conn, job_id)
        if row is None:
            return
        sink = RESULT_SINKS.get(row["type"])
        if sink is not None:
            try:
                sink(
                    SinkContext(
                        conn=conn,
                        vconn=self.registry.vectors_conn(profile),
                        registry=self.registry,
                        profile=profile,
                        paths=self.registry.paths(profile),
                        settings=settings_db.all(conn),
                        model_dir=self.model_dir,
                        params=json.loads(row["params"]),
                        result=result,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - the attempt failed; compute is idempotent
                log.exception("result sink for %s failed", row["type"])
                jobs_db.record_failure(conn, job_id, f"result sink: {type(exc).__name__}: {exc}")
                return
        progress = json.loads(row["progress"])
        progress["result"] = result
        jobs_db.set_progress(conn, job_id, progress)
        jobs_db.mark_done(conn, job_id)

    # -- io ----------------------------------------------------------------------------

    def _dispatch_io(self) -> None:
        if self._io_task is not None and not self._io_task.done():
            return
        self._io_task = None
        while True:
            picked = self._pick("io")
            if picked is None:
                return
            profile, row = picked
            conn = self.registry.conn(profile)
            handler = self.io_handlers.get(row["type"])
            if handler is None:
                jobs_db.mark_failed(conn, row["id"], f"unknown job type {row['type']!r}")
                continue
            jobs_db.mark_running(conn, row["id"])
            self._io_task = asyncio.create_task(
                self._run_io(profile, row["id"], row["type"], json.loads(row["params"]), handler)
            )
            return

    async def _run_io(
        self, profile: str, job_id: bytes, type_: str, params: dict, handler: IoHandler
    ) -> None:
        conn = self.registry.conn(profile)
        loop = asyncio.get_running_loop()

        def report(progress: dict) -> None:
            loop.call_soon_threadsafe(jobs_db.set_progress, conn, job_id, progress)

        ctx = IoContext(
            profile=profile,
            paths=self.registry.paths(profile),
            backup_dir=self.backup_dir,
            global_db_path=self.registry.global_db_path,
            settings=settings_db.all(conn),
            params=params,
            report=report,
            model_dir=self.model_dir,
        )
        try:
            result = await asyncio.to_thread(handler, ctx)
        except Exception as exc:  # noqa: BLE001 - recorded on the job, never propagated
            log.warning("io job %s (%s) failed: %s", id_to_str(job_id), type_, exc)
            if self.registry.exists(profile):
                jobs_db.record_failure(conn, job_id, f"{type(exc).__name__}: {exc}")
            return
        if self.registry.exists(profile):
            self._finish(conn, job_id, result, profile)

    # -- planners ----------------------------------------------------------------------

    def _plan(self) -> None:
        now = time.monotonic()
        if self._last_plan is not None and now - self._last_plan < self.plan_interval:
            return
        self._last_plan = now
        for planner in self.planners:
            try:
                planner(self.registry)
            except Exception:  # noqa: BLE001
                log.exception("planner %r failed", planner)
