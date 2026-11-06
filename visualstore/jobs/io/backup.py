"""Backup job (design §9.1): SQLite online backup, integrity check, retention, scheduling.

Runs in the scheduler's io worker thread, so it opens its own connections from paths and
never touches the registry's connections.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.jobs.scheduler import IoContext
from visualstore.jobs.types import PRIORITY_MAINTENANCE
from visualstore.profiles import ProfileRegistry

TARGETS = ("metadata", "vectors")
PREFIX = {"metadata": "visualstore", "vectors": "vectors"}
_STAMP = "%Y%m%dT%H%M%SZ"


class BackupError(Exception):
    pass


# -- file names -----------------------------------------------------------------------


def backup_file_name(target: str, when: datetime) -> str:
    return f"{PREFIX[target]}-{when.astimezone(UTC).strftime(_STAMP)}.db"


def parse_backup_time(path: Path) -> datetime | None:
    stem = path.name
    if not stem.endswith(".db"):
        return None
    for prefix in PREFIX.values():
        head = f"{prefix}-"
        if stem.startswith(head):
            try:
                return datetime.strptime(stem[len(head) : -3], _STAMP).replace(tzinfo=UTC)
            except ValueError:
                return None
    return None


def _target_of(path: Path) -> str | None:
    for target, prefix in PREFIX.items():
        if path.name.startswith(f"{prefix}-"):
            return target
    return None


def list_backups(backup_dir: Path, profile: str) -> list[dict]:
    directory = backup_dir / profile
    if not directory.is_dir():
        return []
    found = []
    for path in directory.iterdir():
        when = parse_backup_time(path)
        target = _target_of(path)
        if when is None or target is None:
            continue
        found.append(
            {
                "target": target,
                "file": path.name,
                "size": path.stat().st_size,
                "time": when.isoformat(),
            }
        )
    found.sort(key=lambda d: d["time"], reverse=True)
    return found


def latest_backup_time(backup_dir: Path, profile: str, target: str) -> datetime | None:
    times = [
        datetime.fromisoformat(d["time"])
        for d in list_backups(backup_dir, profile)
        if d["target"] == target
    ]
    return max(times) if times else None


# -- the job ---------------------------------------------------------------------------


def _copy_sqlite(src: Path, dest: Path) -> None:
    """Online backup into dest via a temp file; the result includes everything in the WAL."""
    tmp = dest.with_suffix(".tmp")
    source = sqlite3.connect(src)
    try:
        target = sqlite3.connect(tmp)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    tmp.replace(dest)


def _verify(path: Path) -> None:
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        ok = row is not None and row[0] == "ok"
    except sqlite3.DatabaseError:
        ok = False
    if not ok:
        path.unlink(missing_ok=True)
        raise BackupError(f"integrity check failed on backup copy {path.name}; copy deleted")


def _prune(directory: Path, target: str, keep: int) -> list[Path]:
    files = sorted(directory.glob(f"{PREFIX[target]}-*.db"), key=lambda p: p.name)
    doomed = files[: max(0, len(files) - keep)]
    for path in doomed:
        path.unlink()
    return doomed


def run_backup(ctx: IoContext) -> dict:
    targets = list(ctx.params.get("targets") or TARGETS)
    for target in targets:
        if target not in TARGETS:
            raise BackupError(f"unknown backup target {target!r}")
    sources = {"metadata": ctx.paths.metadata_db, "vectors": ctx.paths.vectors_db}
    dest_dir = ctx.backup_dir / ctx.profile
    dest_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    total = len(targets) + 1
    written: list[str] = []
    for i, target in enumerate(targets, start=1):
        dest = dest_dir / backup_file_name(target, now)
        _copy_sqlite(sources[target], dest)
        _verify(dest)
        _prune(dest_dir, target, int(ctx.settings[f"backup.{target}.keep"]))
        written.append(str(dest))
        ctx.report({"done": i, "total": total, "message": f"backed up {target}"})
    ctx.backup_dir.mkdir(parents=True, exist_ok=True)
    _copy_sqlite(ctx.global_db_path, ctx.backup_dir / "global.db")
    ctx.report({"done": total, "total": total, "message": "finished"})
    return {"files": written}


# -- scheduling ------------------------------------------------------------------------


def due_targets(backup_dir: Path, profile: str, settings: dict, now: datetime) -> list[str]:
    due = []
    for target in TARGETS:
        latest = latest_backup_time(backup_dir, profile, target)
        interval = timedelta(hours=int(settings[f"backup.{target}.interval_hours"]))
        if latest is None or now - latest >= interval:
            due.append(target)
    return due


def plan_backups(registry: ProfileRegistry, backup_dir: Path, now: datetime | None = None) -> int:
    """Enqueue a backup job for every profile with an overdue target. Returns jobs enqueued."""
    now = now or datetime.now(UTC)
    enqueued = 0
    for name in registry.names():
        conn = registry.conn(name)
        if jobs_db.has_active(conn, "backup"):
            continue
        targets = due_targets(backup_dir, name, settings_db.all(conn), now)
        if not targets:
            continue
        jobs_db.enqueue(
            conn,
            type="backup",
            kind="io",
            params={"targets": targets},
            priority=PRIORITY_MAINTENANCE,
        )
        enqueued += 1
    return enqueued
