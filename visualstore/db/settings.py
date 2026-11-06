"""Per-profile key-value settings with code-defined defaults (design §5.1 Setting)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

DEFAULTS: dict[str, Any] = {
    "backup.metadata.interval_hours": 24,
    "backup.metadata.keep": 14,
    "backup.vectors.interval_hours": 168,
    "backup.vectors.keep": 4,
}


def get(conn: sqlite3.Connection, key: str) -> Any:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    return DEFAULTS[key] if row is None else json.loads(row["value"])


def set(conn: sqlite3.Connection, key: str, value: Any) -> None:  # noqa: A001 - mirrors get()
    if key not in DEFAULTS:
        raise KeyError(key)
    if type(value) is not type(DEFAULTS[key]):
        raise TypeError(
            f"setting {key!r} expects {type(DEFAULTS[key]).__name__}, got {type(value).__name__}"
        )
    with conn:
        conn.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def all(conn: sqlite3.Connection) -> dict[str, Any]:  # noqa: A001 - mirrors get()
    effective = dict(DEFAULTS)
    for row in conn.execute("SELECT key, value FROM setting"):
        if row["key"] in effective:
            effective[row["key"]] = json.loads(row["value"])
    return effective
