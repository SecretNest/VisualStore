"""Open SQLite files with the pragmas every VisualStore database uses (design §5.3)."""

import sqlite3
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Registry connections are used only on the event-loop thread (async API handlers and
    # the scheduler). Io job handlers open their own connections in their worker thread, so
    # two writers may briefly wait for each other on the WAL; 30 s is far above any
    # transaction here. check_same_thread=False because connections are created off-loop.
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn
