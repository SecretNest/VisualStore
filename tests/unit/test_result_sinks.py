import pytest

from tests.unit.test_scheduler import FakePool
from visualstore.db import jobs as jobs_db
from visualstore.jobs import results
from visualstore.jobs.compute.protocol import ComputeMessage
from visualstore.jobs.scheduler import Scheduler
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def registry(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    yield reg
    reg.close()


async def test_sink_runs_before_done_and_sees_params_and_result(registry, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setitem(
        results.RESULT_SINKS,
        "ping",
        lambda conn, profile, params, result: seen.append((profile, params, result)),
    )
    pool = FakePool()
    sched = Scheduler(registry, pool, io_handlers={}, backup_dir=tmp_path / "b", plan_interval=1000)
    conn = registry.conn("a")
    job_id = jobs_db.enqueue(conn, type="ping", kind="compute", params={"echo": "hi"})
    await sched.tick()
    req = pool.submitted[0]
    pool.messages.append(ComputeMessage(req.job_id, "a", "result", {"pong": "hi"}))
    await sched.tick()
    assert seen == [("a", {"echo": "hi"}, {"pong": "hi"})]
    assert jobs_db.get(conn, job_id)["state"] == "done"


async def test_sink_failure_counts_as_a_failed_attempt(registry, tmp_path, monkeypatch):
    def explode(conn, profile, params, result):
        raise RuntimeError("sink broke")

    monkeypatch.setitem(results.RESULT_SINKS, "ping", explode)
    pool = FakePool()
    sched = Scheduler(registry, pool, io_handlers={}, backup_dir=tmp_path / "b", plan_interval=1000)
    conn = registry.conn("a")
    job_id = jobs_db.enqueue(conn, type="ping", kind="compute")
    await sched.tick()
    req = pool.submitted[0]
    pool.messages.append(ComputeMessage(req.job_id, "a", "result", {}))
    await sched.tick()
    row = jobs_db.get(conn, job_id)
    assert row["attempts"] == 1 and "sink broke" in row["error"]


async def test_io_context_opens_its_own_connection(registry, tmp_path):
    captured = {}

    def handler(ctx):
        from contextlib import closing

        with closing(ctx.open_metadata()) as c:
            captured["same_object"] = c is registry.conn("a")
            captured["count"] = c.execute("SELECT COUNT(*) FROM job").fetchone()[0]
        return {}

    sched = Scheduler(
        registry,
        FakePool(),
        io_handlers={"probe": handler},
        backup_dir=tmp_path / "b",
        plan_interval=1000,
    )
    jobs_db.enqueue(registry.conn("a"), type="probe", kind="io")
    await sched.tick()
    await sched.wait_io()
    assert captured == {"same_object": False, "count": 1}
