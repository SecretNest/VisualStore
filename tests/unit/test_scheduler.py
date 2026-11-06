import asyncio

import pytest

from visualstore.db import jobs as jobs_db
from visualstore.jobs.compute.protocol import ComputeMessage
from visualstore.jobs.scheduler import IoContext, Scheduler
from visualstore.profiles import ProfileRegistry


class FakePool:
    def __init__(self, size=1):
        self.size = size
        self.submitted = []
        self.messages = []
        self.dead = []

    def reap(self):
        out, self.dead = self.dead, []
        return out

    def submit(self, request):
        self.submitted.append(request)

    def poll(self):
        out, self.messages = self.messages, []
        return out

    def alive_count(self):
        return self.size


def echo(ctx: IoContext) -> dict:
    ctx.report({"done": 1, "total": 1, "message": "echoing"})
    return {
        "echoed": ctx.params,
        "profile": ctx.profile,
        "keep": ctx.settings["backup.metadata.keep"],
    }


def boom(ctx: IoContext) -> dict:
    raise RuntimeError("io exploded")


@pytest.fixture
def registry(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    reg.create("b")
    yield reg
    reg.close()


@pytest.fixture
def pool():
    return FakePool(size=1)


@pytest.fixture
def scheduler(registry, pool, tmp_path):
    return Scheduler(
        registry,
        pool,
        io_handlers={"echo": echo, "boom": boom},
        backup_dir=tmp_path / "backups",
        plan_interval=1000.0,
    )


async def test_io_job_runs_in_thread_and_records_result(registry, scheduler):
    conn = registry.conn("a")
    job_id = jobs_db.enqueue(conn, type="echo", kind="io", params={"x": 1})
    await scheduler.tick()
    await scheduler.wait_io()
    row = jobs_db.to_dict(jobs_db.get(conn, job_id))
    assert row["state"] == "done"
    assert row["progress"]["result"] == {"echoed": {"x": 1}, "profile": "a", "keep": 14}
    assert row["progress"]["message"] == "echoing"


async def test_io_failure_is_retried_twice_then_failed(registry, scheduler):
    conn = registry.conn("a")
    job_id = jobs_db.enqueue(conn, type="boom", kind="io")
    for attempt in (1, 2):
        await scheduler.tick()
        await scheduler.wait_io()
        row = jobs_db.get(conn, job_id)
        assert row["state"] == "queued" and row["attempts"] == attempt
        assert "io exploded" in row["error"]
    await scheduler.tick()
    await scheduler.wait_io()
    assert jobs_db.get(conn, job_id)["state"] == "failed"


async def test_unknown_io_and_compute_types_fail_immediately(registry, scheduler, pool):
    """Review Focus 3: a type nobody handles is a configuration error, not a transient one."""
    conn = registry.conn("a")
    io_id = jobs_db.enqueue(conn, type="nope", kind="io")
    cp_id = jobs_db.enqueue(conn, type="nope2", kind="compute")
    await scheduler.tick()
    await scheduler.wait_io()
    for job_id in (io_id, cp_id):
        row = jobs_db.get(conn, job_id)
        assert row["state"] == "failed" and row["attempts"] == 0
        assert "unknown job type" in row["error"]
    assert pool.submitted == []
    assert scheduler.in_flight == {}


async def test_compute_dispatch_is_fair_across_profiles_and_bounded_by_pool(
    registry, scheduler, pool
):
    a_conn, b_conn = registry.conn("a"), registry.conn("b")
    a_id = jobs_db.enqueue(a_conn, type="ping", kind="compute", priority=0)
    a_conn.execute("UPDATE job SET created_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (a_id,))
    b_id = jobs_db.enqueue(b_conn, type="ping", kind="compute", priority=100, params={"count": 2})

    await scheduler.tick()
    assert [r.profile for r in pool.submitted] == ["b"]  # higher priority wins across profiles
    assert jobs_db.get(b_conn, b_id)["state"] == "running"
    assert jobs_db.get(a_conn, a_id)["state"] == "queued"
    await scheduler.tick()
    assert len(pool.submitted) == 1  # pool of 1 is full

    req = pool.submitted[0]
    assert req.type == "ping" and req.params == {"count": 2}
    pool.messages.append(ComputeMessage(req.job_id, "b", "progress", {"done": 1, "total": 2}))
    await scheduler.tick()
    assert jobs_db.to_dict(jobs_db.get(b_conn, b_id))["progress"]["done"] == 1
    assert len(pool.submitted) == 1

    pool.messages.append(ComputeMessage(req.job_id, "b", "result", {"pong": None}))
    await scheduler.tick()
    row = jobs_db.to_dict(jobs_db.get(b_conn, b_id))
    assert row["state"] == "done" and row["progress"]["result"] == {"pong": None}
    assert [r.profile for r in pool.submitted] == ["b", "a"]  # slot freed, a dispatched
    assert jobs_db.get(a_conn, a_id)["state"] == "running"
    assert set(scheduler.in_flight.values()) == {"a"}


async def test_compute_error_requeues_with_attempt(registry, scheduler, pool):
    conn = registry.conn("a")
    job_id = jobs_db.enqueue(conn, type="ping", kind="compute")
    await scheduler.tick()
    req = pool.submitted[0]
    pool.messages.append(ComputeMessage(req.job_id, "a", "error", {"error": "ValueError: bad"}))
    await scheduler.tick()
    row = jobs_db.get(conn, job_id)
    assert row["state"] == "running"  # re-dispatched in the same tick after being requeued
    assert row["attempts"] == 1 and len(pool.submitted) == 2


async def test_message_for_deleted_profile_is_ignored(registry, scheduler, pool):
    conn = registry.conn("b")
    jobs_db.enqueue(conn, type="ping", kind="compute")
    await scheduler.tick()
    req = pool.submitted[0]
    registry.delete("b", confirm_name="b")
    pool.messages.append(ComputeMessage(req.job_id, "b", "result", {}))
    await scheduler.tick()  # must not raise
    assert scheduler.in_flight == {}


async def test_requeue_interrupted_covers_every_profile(registry, scheduler):
    a_id = jobs_db.enqueue(registry.conn("a"), type="ping", kind="compute")
    b_id = jobs_db.enqueue(registry.conn("b"), type="echo", kind="io")
    jobs_db.mark_running(registry.conn("a"), a_id)
    jobs_db.mark_running(registry.conn("b"), b_id)
    assert scheduler.requeue_interrupted() == 2
    assert jobs_db.get(registry.conn("a"), a_id)["state"] == "queued"
    assert jobs_db.get(registry.conn("b"), b_id)["attempts"] == 1


async def test_planners_run_on_first_tick_and_then_per_interval(registry, pool, tmp_path):
    calls = []
    sched = Scheduler(
        registry,
        pool,
        io_handlers={},
        backup_dir=tmp_path / "b",
        planners=[lambda reg: calls.append(reg.names())],
        plan_interval=1000.0,
    )
    await sched.tick()
    await sched.tick()
    assert calls == [["a", "b"]]
    sched.plan_interval = 0.0
    await sched.tick()
    assert len(calls) == 2


async def test_start_and_stop_run_the_loop(registry, scheduler):
    conn = registry.conn("a")
    job_id = jobs_db.enqueue(conn, type="echo", kind="io")
    scheduler.poll_interval = 0.01
    await scheduler.start()
    for _ in range(200):
        if jobs_db.get(conn, job_id)["state"] == "done":
            break
        await asyncio.sleep(0.01)
    await scheduler.stop()
    assert jobs_db.get(conn, job_id)["state"] == "done"


async def test_job_held_by_a_dead_worker_is_requeued(registry, scheduler, pool):
    conn = registry.conn("a")
    job_id = jobs_db.enqueue(conn, type="ping", kind="compute")
    await scheduler.tick()
    req = pool.submitted[0]
    pool.messages.append(ComputeMessage(req.job_id, "a", "started", {"pid": 4242}))
    await scheduler.tick()
    assert scheduler.in_flight_pid[req.job_id] == 4242

    pool.dead = [4242]
    await scheduler.tick()
    row = jobs_db.get(conn, job_id)
    assert row["attempts"] == 1
    assert "compute worker died" in row["error"]
    assert row["state"] == "running" and len(pool.submitted) == 2  # requeued and re-dispatched
    assert req.job_id not in scheduler.in_flight_pid
