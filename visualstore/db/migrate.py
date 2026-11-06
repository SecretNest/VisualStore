"""Versioned schema migrations: numbered SQL files applied in order, one transaction each."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from visualstore.clock import now_iso

_FILE_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_ROOT = Path(__file__).parent / "migrations"


def migrations_dir(name: str) -> Path:
    """Directory of the shipped migrations for one database kind: global, profile, vectors."""
    directory = _ROOT / name
    if not directory.is_dir():
        raise ValueError(f"no migrations shipped for database kind {name!r}")
    return directory


def list_migrations(directory: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILE_NAME.match(path.name)
        if match is None:
            raise ValueError(f"migration file name not understood: {path.name}")
        found.append((int(match.group(1)), path))
    versions = [v for v, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(
            f"migrations in {directory} must be numbered 0001.. without gaps, got {versions}"
        )
    return found


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"] or 0)


def migrate(conn: sqlite3.Connection, directory: Path) -> int:
    version = current_version(conn)
    for number, path in list_migrations(directory):
        if number <= version:
            continue
        sql = path.read_text(encoding="utf-8")
        # executescript commits any pending transaction and then runs the script verbatim,
        # so the BEGIN/COMMIT pair below is what makes one migration atomic.
        script = (
            "BEGIN;\n"
            f"{sql}\n"
            f"INSERT INTO schema_version (version, applied_at) VALUES ({number}, '{now_iso()}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
        version = number
    return version
