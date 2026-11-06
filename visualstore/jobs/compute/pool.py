"""Fixed-size pool of compute children fed over multiprocessing queues (design §3, §6.4)."""

from __future__ import annotations

import multiprocessing as mp
import queue

from visualstore.jobs.compute.protocol import ComputeMessage, ComputeRequest
from visualstore.jobs.compute.worker import worker_main


class ComputePool:
    def __init__(self, size: int) -> None:
        if size < 1:
            raise ValueError("pool size must be at least 1")
        self.size = size
        # spawn: children start from a clean interpreter, so no inherited database handles
        # or event loop state (the server's sqlite connections must never cross this line).
        self._ctx = mp.get_context("spawn")
        self._in = None
        self._out = None
        self._procs: list = []

    def start(self) -> None:
        self._in = self._ctx.Queue()
        self._out = self._ctx.Queue()
        self._procs = [
            self._ctx.Process(
                target=worker_main,
                args=(self._in, self._out),
                name=f"visualstore-compute-{i}",
                daemon=True,
            )
            for i in range(self.size)
        ]
        for proc in self._procs:
            proc.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._in is None:
            return
        for _ in self._procs:
            self._in.put(None)
        for proc in self._procs:
            proc.join(timeout)
            if proc.is_alive():
                proc.terminate()
                proc.join(1.0)
        self._in.close()
        self._out.close()
        self._in = None
        self._out = None

    def submit(self, request: ComputeRequest) -> None:
        if self._in is None:
            raise RuntimeError("pool is not running")
        self._in.put(request)

    def poll(self) -> list[ComputeMessage]:
        if self._out is None:
            return []
        messages: list[ComputeMessage] = []
        while True:
            try:
                messages.append(self._out.get_nowait())
            except queue.Empty:
                return messages

    def alive_count(self) -> int:
        return sum(1 for p in self._procs if p.is_alive())

    def pids(self) -> list[int]:
        return [p.pid for p in self._procs if p.pid is not None]
