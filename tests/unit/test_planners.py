import json
from datetime import UTC, datetime, timedelta

import pytest

from visualstore.db import folders
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io.planners import plan_purge, plan_scans
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def registry(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    (tmp_path / "arch").mkdir()
    (tmp_path / "inbox").mkdir()
    conn = reg.conn("a")
    folders.create_folder(
        conn, name="Arch", path=str(tmp_path / "arch"), role="archive", compare_scope="all"
    )
    folders.create_folder(
        conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all"
    )
    yield reg
    reg.close()


def test_plan_scans_once_per_folder_with_role_priority(registry):
    conn = registry.conn("a")
    assert plan_scans(registry) == 2
    assert plan_scans(registry) == 0  # still queued
    by_folder = {json.loads(j["params"])["folder_id"]: j for j in jobs_db.list_jobs(conn)}
    roles = {id_to_str(f["id"]): f["role"] for f in folders.list_folders(conn)}
    for fid, job in by_folder.items():
        assert job["priority"] == (50 if roles[fid] == "inbox" else 20)


def test_plan_scans_respects_interval_disabled_folders_and_setting_zero(registry):
    conn = registry.conn("a")
    plan_scans(registry)
    for job in jobs_db.list_jobs(conn):
        jobs_db.mark_running(conn, job["id"])
        jobs_db.mark_done(conn, job["id"])
    assert plan_scans(registry, now=datetime.now(UTC) + timedelta(hours=23)) == 0
    assert plan_scans(registry, now=datetime.now(UTC) + timedelta(hours=25)) == 2
    for job in jobs_db.list_jobs(conn, state="queued"):
        jobs_db.cancel(conn, job["id"])
    arch = next(f for f in folders.list_folders(conn) if f["role"] == "archive")
    folders.update_folder(conn, bytes(arch["id"]), enabled=False)
    assert plan_scans(registry, now=datetime.now(UTC) + timedelta(hours=50)) == 1
    settings_db.set(conn, "scan.interval_hours", 0)
    assert plan_scans(registry, now=datetime.now(UTC) + timedelta(hours=100)) == 0


def test_plan_purge_daily_when_trash_enabled(registry):
    conn = registry.conn("a")
    assert plan_purge(registry) == 1
    assert plan_purge(registry) == 0
    [job] = jobs_db.list_jobs(conn)
    assert job["type"] == "purge_trash" and job["priority"] == 0
    jobs_db.mark_running(conn, job["id"])
    jobs_db.mark_done(conn, job["id"])
    assert plan_purge(registry, now=datetime.now(UTC) + timedelta(hours=12)) == 0
    assert plan_purge(registry, now=datetime.now(UTC) + timedelta(hours=25)) == 1
    settings_db.set(conn, "trash.enabled", False)
    for j in jobs_db.list_jobs(conn, state="queued"):
        jobs_db.cancel(conn, j["id"])
    assert plan_purge(registry, now=datetime.now(UTC) + timedelta(hours=100)) == 0
