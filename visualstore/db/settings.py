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
    "trash.enabled": True,
    "trash.retention_days": 30,
    "resolution.auto_execute_identical": True,
    "scan.interval_hours": 24,  # 0 disables scheduled scans
    "grouping.group_distance": 0.3,  # provisional until the milestone 3 evaluation
    "model.id": "sscd-resnet50-512-v1",
    "similarity.candidate_distance": 0.5,
    "similarity.near_duplicate": 0.15,
    "similarity.variant": 0.3,
    "similarity.top_k": 64,
    "media.max_pixels": 80_000_000,
}

_AT_LEAST_ONE = {
    "backup.metadata.keep",
    "backup.vectors.keep",
    "trash.retention_days",
    "similarity.top_k",
    "media.max_pixels",
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
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"setting {key!r} must not be empty")
    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = 1 if key in _AT_LEAST_ONE else 0
        if value < minimum:
            raise ValueError(f"setting {key!r} must be >= {minimum}")
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
