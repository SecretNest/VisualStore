"""Compute job handlers, run inside child processes.

A handler receives the job params and a report callback for progress and returns a
JSON-serialisable result dict. Milestone 3 adds fingerprint, match, rebuild_index, eval.
"""

from __future__ import annotations

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


HANDLERS: dict[str, Handler] = {
    "ping": ping,
    "fail": fail,
}
