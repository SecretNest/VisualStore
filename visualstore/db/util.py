"""Small helpers shared by the query modules."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

SQL_CHUNK = 900  # SQLite's default variable limit is 999 on older builds; stay well under


def chunked(items: Iterable, size: int = SQL_CHUNK) -> Iterator[list]:
    batch: list = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
