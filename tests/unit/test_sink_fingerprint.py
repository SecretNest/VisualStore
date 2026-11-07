import pytest

from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.ids import id_to_str
from visualstore.jobs.results import RESULT_SINKS, sink_fingerprint
from visualstore.profiles import ProfileRegistry

SHA = "ab" * 20


@pytest.fixture
def db(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    (tmp_path / "lib").mkdir()
    folder = folders.create_folder(
        conn, name="L", path=str(tmp_path / "lib"), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]
    ids = [
        files.insert_file(
            conn, folder_id=folder, directory="", name=f"{i}.jpg", extension_id=ext, size=1, mtime=1
        )
        for i in range(3)
    ]
    yield reg, conn, ids
    reg.close()


def entry(fid, sha=SHA, state="fingerprinted"):
    return {"id": id_to_str(fid), "sha1": sha, "state": state, "size": 1}


def sink(reg, result):
    sink_fingerprint(make_sink_ctx(reg, "a", {}, result))


def test_states_pairs_dirty_and_regroup_job(db):
    reg, conn, (a, b, c) = db
    sink(reg, {"files": [entry(a), entry(b, state="unreadable", sha=None)]})
    assert files.get_file(conn, a)["sha1"] == bytes.fromhex(SHA)
    assert files.get_file(conn, a)["state"] == "fingerprinted"
    assert files.get_file(conn, b)["state"] == "unreadable"
    assert files.get_file(conn, b)["sha1"] is None
    assert pairs.pairs_of_file(conn, a) == [] and groups.dirty_count(conn) == 0
    assert not jobs_db.has_active(conn, "regroup")

    sink(reg, {"files": [entry(c)]})
    [p] = pairs.pairs_of_file(conn, c)
    assert {p["file_a"], p["file_b"]} == {a, c} and p["source"] == "sha1"
    assert set(groups.pop_dirty(conn)) == {a, c}
    assert jobs_db.has_active(conn, "regroup")
    assert jobs_db.next_queued(conn, "io")["priority"] == 50


def test_star_topology_and_idempotence(db):
    reg, conn, (a, b, c) = db
    sink(reg, {"files": [entry(a), entry(b), entry(c)]})
    assert len(pairs.edges_touching(conn, [a, b, c])) == 2  # a-b, a-c: a is the hub
    sink(reg, {"files": [entry(a), entry(b), entry(c)]})
    assert len(pairs.edges_touching(conn, [a, b, c])) == 2
    assert jobs_db.count_by_state(conn)["queued"] == 1  # only one regroup job queued


def test_missing_row_is_skipped(db):
    reg, conn, _ = db
    sink(
        reg,
        {
            "files": [
                {
                    "id": "00000000-0000-0000-0000-000000000000",
                    "sha1": SHA,
                    "state": "fingerprinted",
                    "size": 1,
                }
            ]
        },
    )


def test_stale_result_for_a_changed_file_is_ignored(db):
    """Final review C3 (milestone 2): a result computed before the content changed is dropped."""
    reg, conn, (a, *_) = db
    files.update_stat(conn, a, size=99, mtime=5)
    files.reset_for_recompute(conn, a)
    sink(
        reg,
        {
            "files": [
                {"id": id_to_str(a), "sha1": SHA, "state": "fingerprinted", "size": 1, "mtime": 1}
            ]
        },
    )
    row = files.get_file(conn, a)
    assert row["state"] == "new" and row["sha1"] is None
    sink(
        reg,
        {
            "files": [
                {"id": id_to_str(a), "sha1": SHA, "state": "fingerprinted", "size": 99, "mtime": 5}
            ]
        },
    )
    assert files.get_file(conn, a)["state"] == "fingerprinted"


def test_registered():
    assert RESULT_SINKS["fingerprint"] is sink_fingerprint


def test_dimensions_from_the_result_are_stored_on_the_file(db):
    """Design §8.3 lists the resolution per member; the handler reports it, the sink keeps it."""
    reg, conn, ids = db
    a = ids[0]
    sink(reg, {"files": [{**entry(a), "width": 640, "height": 480}]})
    row = files.get_file(conn, a)
    assert (row["width"], row["height"]) == (640, 480)


def test_retry_entries_leave_the_row_for_the_next_scan(db):
    reg, conn, ids = db
    sink(reg, {"files": [{"id": id_to_str(ids[0]), "sha1": None, "state": "retry", "reason": "x"}]})
    row = files.get_file(conn, ids[0])
    assert row["state"] == "new" and row["sha1"] is None
