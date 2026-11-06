import pytest

from visualstore.db import jobs
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir
from visualstore.jobs.types import KIND_OF, PRIORITY_MAINTENANCE, PRIORITY_USER


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "p.db")
    migrate(c, migrations_dir("profile"))
    return c


def test_enqueue_and_get(conn):
    job_id = jobs.enqueue(conn, type="backup", kind="io", params={"targets": ["metadata"]})
    row = jobs.get(conn, job_id)
    assert row["state"] == "queued"
    assert row["attempts"] == 0
    d = jobs.to_dict(row)
    assert d["params"] == {"targets": ["metadata"]}
    assert d["progress"] == {}
    assert isinstance(d["id"], str) and len(d["id"]) == 36


def test_next_queued_prefers_priority_then_age(conn):
    low_old = jobs.enqueue(conn, type="ping", kind="compute", priority=PRIORITY_MAINTENANCE)
    high = jobs.enqueue(conn, type="ping", kind="compute", priority=PRIORITY_USER)
    jobs.enqueue(conn, type="backup", kind="io", priority=PRIORITY_USER)
    assert jobs.next_queued(conn, "compute")["id"] == high
    jobs.mark_running(conn, high)
    assert jobs.next_queued(conn, "compute")["id"] == low_old
    jobs.mark_running(conn, low_old)
    assert jobs.next_queued(conn, "compute") is None


def test_same_priority_is_fifo(conn):
    first = jobs.enqueue(conn, type="ping", kind="compute")
    conn.execute("UPDATE job SET created_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (first,))
    second = jobs.enqueue(conn, type="ping", kind="compute")
    assert jobs.next_queued(conn, "compute")["id"] == first
    jobs.cancel(conn, first)
    assert jobs.next_queued(conn, "compute")["id"] == second


def test_lifecycle_running_progress_done(conn):
    job_id = jobs.enqueue(conn, type="ping", kind="compute")
    jobs.mark_running(conn, job_id)
    row = jobs.get(conn, job_id)
    assert row["state"] == "running" and row["started_at"] is not None
    jobs.set_progress(conn, job_id, {"done": 1, "total": 2, "message": "half"})
    assert jobs.to_dict(jobs.get(conn, job_id))["progress"]["done"] == 1
    jobs.mark_done(conn, job_id)
    row = jobs.get(conn, job_id)
    assert row["state"] == "done" and row["finished_at"] is not None


def test_record_failure_requeues_twice_then_fails(conn):
    job_id = jobs.enqueue(conn, type="ping", kind="compute")
    jobs.mark_running(conn, job_id)
    assert jobs.record_failure(conn, job_id, "boom 1") == "queued"
    assert jobs.get(conn, job_id)["attempts"] == 1
    jobs.mark_running(conn, job_id)
    assert jobs.record_failure(conn, job_id, "boom 2") == "queued"
    jobs.mark_running(conn, job_id)
    assert jobs.record_failure(conn, job_id, "boom 3") == "failed"
    row = jobs.get(conn, job_id)
    assert row["attempts"] == 3 and row["error"] == "boom 3" and row["finished_at"] is not None


def test_requeue_interrupted_handles_restart(conn):
    """Review Focus 2: running jobs found at start-up go back to queued with attempts + 1."""
    a = jobs.enqueue(conn, type="ping", kind="compute")
    b = jobs.enqueue(conn, type="backup", kind="io")
    c = jobs.enqueue(conn, type="ping", kind="compute")
    jobs.mark_running(conn, a)
    jobs.mark_running(conn, b)
    conn.execute("UPDATE job SET attempts = 2 WHERE id = ?", (b,))
    assert jobs.requeue_interrupted(conn) == 2
    assert jobs.get(conn, a)["state"] == "queued" and jobs.get(conn, a)["attempts"] == 1
    assert jobs.get(conn, b)["state"] == "failed" and jobs.get(conn, b)["attempts"] == 3
    assert jobs.get(conn, c)["state"] == "queued" and jobs.get(conn, c)["attempts"] == 0


def test_mark_failed_is_final(conn):
    job_id = jobs.enqueue(conn, type="nonsense", kind="io")
    jobs.mark_running(conn, job_id)
    jobs.mark_failed(conn, job_id, "unknown job type 'nonsense'")
    row = jobs.get(conn, job_id)
    assert row["state"] == "failed" and row["error"].startswith("unknown job type")


def test_retry_and_cancel(conn):
    job_id = jobs.enqueue(conn, type="ping", kind="compute")
    assert jobs.retry(conn, job_id) is False  # only failed jobs can be retried
    jobs.mark_running(conn, job_id)
    jobs.mark_failed(conn, job_id, "x")
    assert jobs.retry(conn, job_id) is True
    row = jobs.get(conn, job_id)
    assert row["state"] == "queued" and row["attempts"] == 0 and row["error"] is None
    assert jobs.cancel(conn, job_id) is True
    assert jobs.get(conn, job_id)["state"] == "cancelled"
    assert jobs.cancel(conn, job_id) is False


def test_count_by_state_and_has_active(conn):
    assert jobs.count_by_state(conn) == {
        "queued": 0,
        "running": 0,
        "done": 0,
        "failed": 0,
        "cancelled": 0,
    }
    assert jobs.has_active(conn, "backup") is False
    job_id = jobs.enqueue(conn, type="backup", kind="io")
    assert jobs.has_active(conn, "backup") is True
    jobs.mark_running(conn, job_id)
    assert jobs.has_active(conn, "backup") is True
    jobs.mark_done(conn, job_id)
    assert jobs.has_active(conn, "backup") is False
    assert jobs.count_by_state(conn)["done"] == 1


def test_list_jobs_newest_first_with_filter(conn):
    a = jobs.enqueue(conn, type="ping", kind="compute")
    conn.execute("UPDATE job SET created_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (a,))
    b = jobs.enqueue(conn, type="ping", kind="compute")
    jobs.cancel(conn, a)
    assert [r["id"] for r in jobs.list_jobs(conn)] == [b, a]
    assert [r["id"] for r in jobs.list_jobs(conn, state="cancelled")] == [a]
    assert len(jobs.list_jobs(conn, limit=1)) == 1


def test_every_known_type_has_a_kind():
    assert KIND_OF["backup"] == "io"
    assert KIND_OF["ping"] == "compute"
    assert set(KIND_OF.values()) <= {"io", "compute"}


def test_latest_of_type_returns_newest_or_none(conn):
    assert jobs.latest_of_type(conn, "backup") is None
    old = jobs.enqueue(conn, type="backup", kind="io")
    conn.execute("UPDATE job SET created_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (old,))
    new = jobs.enqueue(conn, type="backup", kind="io")
    jobs.enqueue(conn, type="ping", kind="compute")
    assert jobs.latest_of_type(conn, "backup")["id"] == new


def test_failed_attempt_backs_off_before_the_next_pick(conn):
    """ADR-0008 retry backoff: 30 s × attempts, cleared by a manual retry."""
    from datetime import UTC, datetime, timedelta

    job_id = jobs.enqueue(conn, type="ping", kind="compute")
    jobs.mark_running(conn, job_id)
    assert jobs.record_failure(conn, job_id, "boom") == "queued"
    assert jobs.next_queued(conn, "compute") is None  # not yet
    later = (datetime.now(UTC) + timedelta(seconds=31)).isoformat()
    assert jobs.next_queued(conn, "compute", now=later)["id"] == job_id
    assert jobs.to_dict(jobs.get(conn, job_id))["not_before"] is not None
    jobs.mark_running(conn, job_id)
    jobs.record_failure(conn, job_id, "boom")
    assert jobs.next_queued(conn, "compute", now=later) is None  # 60 s now
    jobs.mark_running(conn, job_id)
    assert jobs.record_failure(conn, job_id, "boom") == "failed"
    assert jobs.retry(conn, job_id) and jobs.next_queued(conn, "compute")["id"] == job_id


def test_interrupted_jobs_are_requeued_without_backoff(conn):
    job_id = jobs.enqueue(conn, type="ping", kind="compute")
    jobs.mark_running(conn, job_id)
    assert jobs.requeue_interrupted(conn) == 1
    assert jobs.next_queued(conn, "compute")["id"] == job_id
