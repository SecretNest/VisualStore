import base64

import numpy as np
import pytest

from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, fingerprints, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.index.usearch_index import IndexError_, VectorIndex, inspect
from visualstore.jobs.compute.handlers import match
from visualstore.jobs.results import RESULT_SINKS, sink_match
from visualstore.profiles import ProfileRegistry

M = {
    "id": "tiny-test-4-v1",
    "dim": 4,
    "path": "",
    "input_size": 16,
    "mean": [0, 0, 0],
    "std": [1, 1, 1],
}


def b64(v):
    v = np.asarray(v, dtype=np.float32)
    return base64.b64encode((v / np.linalg.norm(v)).astype(np.float16).tobytes()).decode()


def test_match_handler_adds_searches_and_filters_by_distance(tmp_path):
    index_path = tmp_path / "index.usearch"
    params = {
        "model": M,
        "index_path": str(index_path),
        "remove": [],
        "entries": [
            {"seq": 1, "vector": b64([1, 0, 0, 0])},
            {"seq": 2, "vector": b64([0.95, 0.05, 0, 0])},
            {"seq": 3, "vector": b64([0, 0, 1, 0])},
        ],
        "top_k": 10,
        "candidate_distance": 0.5,
    }
    result = match(params, lambda p: None)
    assert result["added"] == 3 and result["removed"] == 0 and result["count"] == 3
    hits = {(q, h) for q, h, _ in result["hits"]}
    assert (1, 2) in hits and (2, 1) in hits and (1, 3) not in hits  # 3 is at distance 1.0
    assert all(q != h for q, h, _ in result["hits"])
    assert inspect(index_path)["count"] == 3

    again = match(
        {**params, "entries": [{"seq": 4, "vector": b64([0, 0, 0.98, 0.02])}], "remove": [2]},
        lambda p: None,
    )
    assert again["removed"] == 1 and again["count"] == 3
    assert {(q, h) for q, h, _ in again["hits"]} == {(4, 3)}


def test_match_handler_refuses_a_foreign_index(tmp_path):
    index_path = tmp_path / "index.usearch"
    other = VectorIndex.empty(8)
    other.add([1], np.ones((1, 8), dtype=np.float32) / np.sqrt(8))
    other.save(index_path)
    with pytest.raises(IndexError_):
        match(
            {
                "model": M,
                "index_path": str(index_path),
                "entries": [],
                "remove": [],
                "top_k": 5,
                "candidate_distance": 0.5,
            },
            lambda p: None,
        )


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    for d in ("arch", "inbox"):
        (tmp_path / d).mkdir()
    arch = folders.create_folder(
        conn,
        name="Arch",
        path=str(tmp_path / "arch"),
        role="archive",
        compare_scope="other_directories",
    )
    inbox = folders.create_folder(
        conn, name="Inbox", path=str(tmp_path / "inbox"), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(folder, directory, name):
        fid = files.insert_file(
            conn,
            folder_id=folder,
            directory=directory,
            name=name,
            extension_id=ext,
            size=1,
            mtime=1,
        )
        files.set_fingerprint_result(conn, fid, sha1=bytes([len(name)]) * 20, state="fingerprinted")
        fingerprints.replace_frames(vconn, fid, M["id"], [(0, 0, b"\x00" * 8)])
        [seq] = fingerprints.allocate_keys(vconn, fid, M["id"], [0])
        return fid, seq

    yield reg, conn, vconn, arch, inbox, add
    reg.close()


def test_sink_match_applies_scope_and_writes_pairs(world):
    """Review Focus 2."""
    reg, conn, vconn, arch, inbox, add = world
    a1, s1 = add(arch, "set", "a1.jpg")
    a2, s2 = add(arch, "set", "a2.jpg")  # same Folder + directory as a1: filtered
    a3, s3 = add(arch, "other", "a3.jpg")  # same Folder, other directory: allowed
    i1, s4 = add(inbox, "set", "i1.jpg")  # different Folder: allowed
    result = {
        "hits": [
            [s1, s2, 0.05],
            [s1, s3, 0.1],
            [s1, s4, 0.2],
            [s4, s1, 0.19],
            [s1, 999, 0.01],
            [s1, s1, 0.0],
        ],
        "added": 4,
        "removed": 0,
        "count": 4,
    }
    params = {
        "model": M,
        "entries": [{"seq": s, "vector": ""} for s in (s1, s2, s3, s4)],
        "remove": [],
        "top_k": 10,
        "candidate_distance": 0.5,
    }
    sink_match(make_sink_ctx(reg, "a", params, result))
    got = {
        tuple(sorted((bytes(p["file_a"]), bytes(p["file_b"])))): p
        for p in pairs.edges_touching(conn, [a1])
    }
    assert set(got) == {tuple(sorted((a1, a3))), tuple(sorted((a1, i1)))}
    assert got[tuple(sorted((a1, i1)))]["distance"] == pytest.approx(
        0.19
    )  # min over both directions
    assert all(p["source"] == "vector" and p["model_id"] == M["id"] for p in got.values())
    assert set(groups.pop_dirty(conn)) == {a1, a3, i1}
    assert jobs_db.has_active(conn, "regroup")
    assert (
        fingerprints.get_meta(vconn, "model_id") == M["id"]
        and fingerprints.get_meta(vconn, "dim") == "4"
    )
    assert RESULT_SINKS["match"] is sink_match


def test_sink_match_keeps_sha1_pairs_and_judgments_and_drops_removed_keys(world):
    reg, conn, vconn, arch, inbox, add = world
    a, sa = add(arch, "x", "a.jpg")
    b, sb = add(inbox, "x", "b.jpg")
    pairs.upsert_sha1_pair(conn, a, b)
    pairs.set_judgment(conn, a, b, "unrelated", "user")
    gone = fingerprints.allocate_keys(
        vconn, b"\x09" * 16, M["id"], [0]
    )  # a key for a file that then vanishes
    fingerprints.delete_for_files(vconn, [b"\x09" * 16])
    params = {"model": M, "entries": [], "remove": gone, "top_k": 10, "candidate_distance": 0.5}
    sink_match(
        make_sink_ctx(
            reg, "a", params, {"hits": [[sa, sb, 0.3]], "added": 0, "removed": 1, "count": 2}
        )
    )
    [p] = pairs.pairs_of_file(conn, a)
    assert p["source"] == "sha1" and p["distance"] == 0.0 and p["judgment"] == "unrelated"
    assert fingerprints.pending_removals(vconn, M["id"]) == []
