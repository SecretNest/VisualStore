import os
import signal
import time

import pytest

from visualstore.jobs.compute.pool import ComputePool
from visualstore.jobs.compute.protocol import ComputeMessage, ComputeRequest


@pytest.fixture
def pool():
    p = ComputePool(size=2)
    p.start()
    yield p
    p.stop()


def collect(pool: ComputePool, job_id: str, timeout: float = 15.0) -> list[ComputeMessage]:
    """Drain messages for one job until a result or error arrives."""
    deadline = time.monotonic() + timeout
    got: list[ComputeMessage] = []
    while time.monotonic() < deadline:
        for msg in pool.poll():
            if msg.job_id == job_id:
                got.append(msg)
                if msg.kind in ("result", "error"):
                    return got
        time.sleep(0.02)
    raise AssertionError(f"no final message for {job_id} within {timeout}s; got {got}")


def test_ping_round_trip_reports_progress_then_result(pool):
    pool.submit(
        ComputeRequest(job_id="j1", profile="a", type="ping", params={"count": 3, "echo": "hi"})
    )
    msgs = collect(pool, "j1")
    kinds = [m.kind for m in msgs]
    assert kinds == ["started", "progress", "progress", "progress", "result"]
    assert msgs[1].payload == {"done": 1, "total": 3, "message": "ping"}
    assert msgs[-1].payload["pong"] == "hi"
    assert msgs[-1].payload["pid"] != os.getpid()  # ran in a child, not in this process
    assert msgs[-1].profile == "a"


def test_handler_exception_becomes_error_message(pool):
    pool.submit(ComputeRequest(job_id="j2", profile="a", type="fail", params={"message": "kaboom"}))
    msgs = collect(pool, "j2")
    assert msgs[-1].kind == "error"
    assert "RuntimeError: kaboom" in msgs[-1].payload["error"]


def test_unknown_type_becomes_error_message(pool):
    pool.submit(ComputeRequest(job_id="j3", profile="a", type="teleport", params={}))
    msgs = collect(pool, "j3")
    assert msgs[-1].kind == "error"
    assert "teleport" in msgs[-1].payload["error"]


def test_two_jobs_run_on_two_children(pool):
    # each ping sleeps long enough that one child cannot finish the first before the second
    # is taken, so both children are exercised
    slow = {"count": 1, "sleep_ms": 400}
    pool.submit(ComputeRequest(job_id="a1", profile="a", type="ping", params=slow))
    pool.submit(ComputeRequest(job_id="a2", profile="a", type="ping", params=slow))
    pids = set()
    deadline = time.monotonic() + 15
    while len(pids) < 2 and time.monotonic() < deadline:
        for msg in pool.poll():
            if msg.kind == "result":
                pids.add(msg.payload["pid"])
        time.sleep(0.02)
    assert len(pids) == 2
    assert set(pids) <= set(pool.pids())


def test_alive_count_reflects_a_killed_child(pool):
    assert pool.alive_count() == 2
    os.kill(pool.pids()[0], signal.SIGKILL)
    deadline = time.monotonic() + 5
    while pool.alive_count() == 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pool.alive_count() == 1


def test_stop_terminates_children():
    p = ComputePool(size=1)
    p.start()
    assert p.alive_count() == 1
    p.stop()
    assert p.alive_count() == 0


def test_reap_replaces_a_killed_child_and_reports_its_pid(pool):
    victim = pool.pids()[0]
    os.kill(victim, signal.SIGKILL)
    deadline = time.monotonic() + 5
    dead: list[int] = []
    while not dead and time.monotonic() < deadline:
        dead = pool.reap()
        time.sleep(0.05)
    assert dead == [victim]
    assert pool.alive_count() == 2
    assert victim not in pool.pids()
    assert pool.reap() == []
    # the replacement child does real work
    pool.submit(ComputeRequest(job_id="r1", profile="a", type="ping", params={"count": 1}))
    assert collect(pool, "r1")[-1].kind == "result"


def test_worker_announces_start_with_its_pid(pool):
    pool.submit(ComputeRequest(job_id="s1", profile="a", type="ping", params={"count": 1}))
    msgs = collect(pool, "s1")
    assert msgs[0].kind == "started"
    assert msgs[0].payload["pid"] in pool.pids()
