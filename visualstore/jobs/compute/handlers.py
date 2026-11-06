"""Compute job handlers, run inside child processes.

A handler receives the job params and a report callback for progress and returns a
JSON-serialisable result dict. Milestone 3 adds fingerprint, match, rebuild_index, eval.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable

Report = Callable[[dict], None]
Handler = Callable[[dict, Report], dict]


def ping(params: dict, report: Report) -> dict:
    count = int(params.get("count", 1))
    sleep_ms = int(params.get("sleep_ms", 0))  # lets tests simulate long-running compute
    for i in range(count):
        if sleep_ms:
            time.sleep(sleep_ms / 1000)
        report({"done": i + 1, "total": count, "message": "ping"})
    return {"pong": params.get("echo"), "pid": os.getpid()}


def fail(params: dict, report: Report) -> dict:
    raise RuntimeError(str(params.get("message", "requested failure")))


_CHUNK = 1024 * 1024


def sha1_of(path: str) -> bytes:
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.digest()


def fingerprint(params: dict, report: Report) -> dict:
    """Milestone 2: SHA-1 only. Milestone 3 adds decode, vectors and thumbnails per media type."""
    entries = params.get("files", [])
    out = []
    for i, entry in enumerate(entries, start=1):
        path = entry["path"]
        try:
            size = os.stat(path).st_size
            digest = sha1_of(path)
            out.append(
                {"id": entry["id"], "sha1": digest.hex(), "state": "fingerprinted", "size": size}
            )
        except FileNotFoundError:
            out.append({"id": entry["id"], "sha1": None, "state": "missing", "size": 0})
        except OSError:
            out.append({"id": entry["id"], "sha1": None, "state": "unreadable", "size": 0})
        if i % 8 == 0 or i == len(entries):
            report({"done": i, "total": len(entries), "message": os.path.basename(path)})
    return {"files": out}


HANDLERS: dict[str, Handler] = {
    "ping": ping,
    "fail": fail,
    "fingerprint": fingerprint,
}
