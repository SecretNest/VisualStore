import sqlite3

import pytest

from visualstore.db.connection import connect
from visualstore.db.migrate import current_version, migrate, migrations_dir


def test_connect_applies_pragmas(tmp_path):
    conn = connect(tmp_path / "sub" / "x.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    assert isinstance(conn.execute("SELECT 1 AS one").fetchone(), sqlite3.Row)


@pytest.mark.parametrize(
    "name,tables",
    [
        ("global", {"profile", "secret"}),
        (
            "profile",
            {
                "job",
                "setting",
                "folder",
                "ignored_directory",
                "extension",
                "file",
                "pair",
                "file_group",
                "file_group_member",
                "regroup_queue",
                "action",
            },
        ),
        ("vectors", {"fingerprint", "index_key", "index_meta"}),
    ],
)
def test_shipped_migrations_create_expected_tables(tmp_path, name, tables):
    conn = connect(tmp_path / f"{name}.db")
    version = migrate(conn, migrations_dir(name))
    assert version == {"global": 1, "profile": 2, "vectors": 2}[name]
    names = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if r["name"] != "schema_version"
    }
    assert names == tables


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "p.db")
    assert migrate(conn, migrations_dir("profile")) == 2
    assert migrate(conn, migrations_dir("profile")) == 2
    assert current_version(conn) == 2
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 2


def test_fresh_database_reports_version_zero(tmp_path):
    conn = connect(tmp_path / "empty.db")
    assert current_version(conn) == 0


def test_gap_in_numbering_is_rejected(tmp_path):
    mig = tmp_path / "mig"
    mig.mkdir()
    (mig / "0001_a.sql").write_text("CREATE TABLE a (x);")
    (mig / "0003_c.sql").write_text("CREATE TABLE c (x);")
    conn = connect(tmp_path / "g.db")
    with pytest.raises(ValueError, match="without gaps"):
        migrate(conn, mig)


def test_failed_migration_leaves_version_and_schema_unchanged(tmp_path):
    mig = tmp_path / "mig"
    mig.mkdir()
    (mig / "0001_a.sql").write_text("CREATE TABLE a (x);")
    (mig / "0002_bad.sql").write_text("CREATE TABLE b (x);\nTHIS IS NOT SQL;")
    conn = connect(tmp_path / "f.db")
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, mig)
    assert current_version(conn) == 1
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "b" not in names
