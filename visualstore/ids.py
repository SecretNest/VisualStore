"""UUID primary keys stored as 16-byte BLOBs (design §5.3)."""

import uuid


def new_id() -> bytes:
    return uuid.uuid4().bytes


def id_to_str(raw: bytes) -> str:
    return str(uuid.UUID(bytes=raw))


def id_from_str(text: str) -> bytes:
    return uuid.UUID(text).bytes
