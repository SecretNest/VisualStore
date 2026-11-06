"""Time-based planners for milestone 2 jobs: scheduled scans and trash purge (design §7.1, §8.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from visualstore.clock import parse_iso
from visualstore.db import folders as folders_db
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.types import PRIORITY_ARCHIVE, PRIORITY_INBOX, PRIORITY_MAINTENANCE
from visualstore.profiles import ProfileRegistry


def _due(last, interval: timedelta, now: datetime) -> bool:
    if last is None:
        return True
    if not last["finished_at"]:
        return False  # queued or running: not due again
    return now - parse_iso(last["finished_at"]) >= interval


def plan_scans(registry: ProfileRegistry, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    enqueued = 0
    for name in registry.names():
        conn = registry.conn(name)
        hours = int(settings_db.get(conn, "scan.interval_hours"))
        if hours <= 0:
            continue
        for folder in folders_db.list_folders(conn):
            if not folder["enabled"]:
                continue
            fid = id_to_str(bytes(folder["id"]))
            if jobs_db.has_active_with_param(conn, "scan", "folder_id", fid):
                continue
            last = jobs_db.latest_with_param(conn, "scan", "folder_id", fid)
            if not _due(last, timedelta(hours=hours), now):
                continue
            jobs_db.enqueue(
                conn,
                type="scan",
                kind="io",
                params={"folder_id": fid},
                priority=PRIORITY_INBOX if folder["role"] == "inbox" else PRIORITY_ARCHIVE,
            )
            enqueued += 1
    return enqueued


def plan_purge(registry: ProfileRegistry, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    enqueued = 0
    for name in registry.names():
        conn = registry.conn(name)
        if not settings_db.get(conn, "trash.enabled") or jobs_db.has_active(conn, "purge_trash"):
            continue
        if not _due(jobs_db.latest_of_type(conn, "purge_trash"), timedelta(hours=24), now):
            continue
        jobs_db.enqueue(conn, type="purge_trash", kind="io", priority=PRIORITY_MAINTENANCE)
        enqueued += 1
    return enqueued
