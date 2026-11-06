"""UTC timestamps as ISO 8601 strings; the only time format stored in databases."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)
