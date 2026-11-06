"""Entry point of one compute child: take requests, run handlers, send messages back."""

from __future__ import annotations

import os

from visualstore.jobs.compute.handlers import HANDLERS
from visualstore.jobs.compute.protocol import ComputeMessage, ComputeRequest


def worker_main(in_q, out_q) -> None:
    while True:
        request: ComputeRequest | None = in_q.get()
        if request is None:  # shutdown sentinel
            return
        # Announce which child holds the job, so the server can fail it if this child dies.
        out_q.put(ComputeMessage(request.job_id, request.profile, "started", {"pid": os.getpid()}))

        def report(progress: dict, _req: ComputeRequest = request) -> None:
            out_q.put(ComputeMessage(_req.job_id, _req.profile, "progress", progress))

        try:
            handler = HANDLERS.get(request.type)
            if handler is None:
                raise KeyError(f"unknown compute job type {request.type!r}")
            result = handler(request.params, report)
            out_q.put(ComputeMessage(request.job_id, request.profile, "result", result))
        except Exception as exc:  # noqa: BLE001 - every failure must reach the server
            out_q.put(
                ComputeMessage(
                    request.job_id,
                    request.profile,
                    "error",
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
            )
