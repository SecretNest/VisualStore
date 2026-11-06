import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.jobs.io import IO_HANDLERS, backup
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def registry(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    yield reg
    reg.close()


def make_ctx(registry, backup_dir, params=None, reports=None):
    conn = registry.conn("a")
    return IoContext(
        profile="a",
        paths=registry.paths("a"),
        backup_dir=backup_dir,
        global_db_path=registry.global_db_path,
        settings=settings_db.all(conn),
        params=params or {},
        report=(reports.append if reports is not None else lambda p: None),
    )


def test_run_backup_writes_verified_copies_and_global_db(registry, tmp_path):
    settings_db.set(registry.conn("a"), "backup.metadata.keep", 7)
    reports = []
    result = backup.run_backup(make_ctx(registry, tmp_path / "bk", reports=reports))

    files = sorted(p.name for p in (tmp_path / "bk" / "a").iterdir())
    assert len(files) == 2
    assert files[0].startswith("vectors-") and files[1].startswith("visualstore-")
    assert (tmp_path / "bk" / "global.db").is_file()
    assert set(result["files"]) == {str(tmp_path / "bk" / "a" / f) for f in files}

    copy = sqlite3.connect(tmp_path / "bk" / "a" / files[1])
    copy.row_factory = sqlite3.Row
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert settings_db.get(copy, "backup.metadata.keep") == 7
    gcopy = sqlite3.connect(tmp_path / "bk" / "global.db")
    assert gcopy.execute("SELECT name FROM profile").fetchone()[0] == "a"
    assert reports[-1]["message"] == "finished"
    assert IO_HANDLERS["backup"] is backup.run_backup


def test_backup_taken_during_live_writes_is_complete_and_consistent(registry, tmp_path):
    """Review Focus 4: rows still in the WAL must be in the copy, and the copy must verify."""
    conn = registry.conn("a")
    for i in range(300):
        jobs_db.enqueue(conn, type="ping", kind="compute", params={"i": i})
    assert conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()[1] >= 0  # WAL in use
    backup.run_backup(make_ctx(registry, tmp_path / "bk", params={"targets": ["metadata"]}))
    [copy_path] = list((tmp_path / "bk" / "a").glob("visualstore-*.db"))
    copy = sqlite3.connect(copy_path)
    assert copy.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 300
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not list((tmp_path / "bk" / "a").glob("vectors-*"))


def test_corrupt_copy_is_deleted_and_reported(registry, tmp_path, monkeypatch):
    def write_garbage(src, dest):
        dest.write_bytes(b"this is not a database" * 100)

    monkeypatch.setattr(backup, "_copy_sqlite", write_garbage)
    with pytest.raises(backup.BackupError, match="integrity"):
        backup.run_backup(make_ctx(registry, tmp_path / "bk", params={"targets": ["metadata"]}))
    assert list((tmp_path / "bk" / "a").glob("*.db")) == []


def test_retention_keeps_newest_n_per_target(registry, tmp_path):
    dest = tmp_path / "bk" / "a"
    dest.mkdir(parents=True)
    old_names = [
        backup.backup_file_name("metadata", datetime(2026, 1, d, tzinfo=UTC)) for d in range(1, 16)
    ]
    for name in old_names:
        (dest / name).write_bytes(b"x")
    (dest / backup.backup_file_name("vectors", datetime(2026, 1, 1, tzinfo=UTC))).write_bytes(b"x")

    backup.run_backup(make_ctx(registry, tmp_path / "bk", params={"targets": ["metadata"]}))

    metadata_files = sorted(p.name for p in dest.glob("visualstore-*.db"))
    assert len(metadata_files) == 14  # default keep
    assert old_names[0] not in metadata_files and old_names[1] not in metadata_files
    assert old_names[2] in metadata_files
    assert len(list(dest.glob("vectors-*.db"))) == 1  # other target untouched


def test_file_name_round_trip_and_listing(tmp_path):
    when = datetime(2026, 11, 6, 14, 30, 0, tzinfo=UTC)
    name = backup.backup_file_name("metadata", when)
    assert name == "visualstore-20261106T143000Z.db"
    dest = tmp_path / "bk" / "a"
    dest.mkdir(parents=True)
    (dest / name).write_bytes(b"12345")
    (dest / "junk.txt").write_bytes(b"")
    assert backup.parse_backup_time(dest / name) == when
    assert backup.parse_backup_time(dest / "junk.txt") is None
    listed = backup.list_backups(tmp_path / "bk", "a")
    assert listed == [{"target": "metadata", "file": name, "size": 5, "time": when.isoformat()}]
    assert backup.latest_backup_time(tmp_path / "bk", "a", "metadata") == when
    assert backup.latest_backup_time(tmp_path / "bk", "a", "vectors") is None
    assert backup.list_backups(tmp_path / "bk", "nope") == []


def test_due_targets_follow_interval_settings(tmp_path):
    settings = dict(settings_db.DEFAULTS)
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    assert backup.due_targets(tmp_path / "bk", "a", settings, now) == ["metadata", "vectors"]

    dest = tmp_path / "bk" / "a"
    dest.mkdir(parents=True)
    (dest / backup.backup_file_name("metadata", now - timedelta(hours=23))).write_bytes(b"")
    (dest / backup.backup_file_name("vectors", now - timedelta(hours=100))).write_bytes(b"")
    assert backup.due_targets(tmp_path / "bk", "a", settings, now) == []

    for stale in dest.glob("visualstore-*"):
        stale.unlink()
    (dest / backup.backup_file_name("metadata", now - timedelta(hours=25))).write_bytes(b"")
    assert backup.due_targets(tmp_path / "bk", "a", settings, now) == ["metadata"]


def test_plan_backups_enqueues_one_maintenance_job_per_profile(registry, tmp_path):
    registry.create("b")
    assert backup.plan_backups(registry, tmp_path / "bk") == 2
    assert backup.plan_backups(registry, tmp_path / "bk") == 0  # already queued
    for name in ("a", "b"):
        [row] = jobs_db.list_jobs(registry.conn(name))
        assert row["type"] == "backup" and row["kind"] == "io" and row["priority"] == 0
        assert jobs_db.to_dict(row)["params"] == {"targets": ["metadata", "vectors"]}
