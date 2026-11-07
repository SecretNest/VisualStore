import json

import numpy as np

from tests.unit.sinkctx import make_sink_ctx
from tests.unit.test_rebuild_and_startup import ctx_for, seed_vectors, world  # noqa: F401
from visualstore.db import fingerprints
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.index.usearch_index import inspect
from visualstore.jobs.compute.handlers import build_index, match
from visualstore.jobs.io.rebuild import run_rebuild_index
from visualstore.jobs.results import sink_build_index
from visualstore.jobs.startup import index_is_stale

MODEL = "sscd-resnet50-512-v1"


def test_i8_and_pca_rebuild_record_options_and_keep_stored_vectors(world):  # noqa: F811
    """Review Focus 1."""
    reg = world
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    seed_vectors(reg, 40)
    before = [bytes(r["vector"]) for rows in fingerprints.iter_vectors(vconn, MODEL) for r in rows]
    settings_db.set(conn, "index.quantization", "i8")
    settings_db.set(conn, "index.pca_dims", 32)
    run_rebuild_index(ctx_for(reg))
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "build_index"]
    params = json.loads(job["params"])
    assert params["index"] == {
        "quantization": "i8",
        "pca_dims": 32,
        "pca_path": str(reg.paths("a").root / "pca.npz"),
        "index_dim": 32,
    }
    result = build_index(params, lambda p: None)
    sink_build_index(make_sink_ctx(reg, "a", params, result))
    meta = inspect(reg.paths("a").index)
    assert meta["dimensions"] == 32 and meta["dtype"] == "i8" and meta["count"] == 40
    assert (
        fingerprints.get_meta(vconn, "quantization") == "i8"
        and fingerprints.get_meta(vconn, "pca_dims") == "32"
    )
    after = [bytes(r["vector"]) for rows in fingerprints.iter_vectors(vconn, MODEL) for r in rows]
    assert after == before  # stored vectors untouched
    assert not index_is_stale(reg, "a")
    settings_db.set(conn, "index.pca_dims", 0)
    assert index_is_stale(reg, "a")  # options differ from the index: rebuild needed


def test_match_projects_entries_and_queries_with_the_same_file(world):  # noqa: F811
    reg = world
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    ids = seed_vectors(reg, 30)
    settings_db.set(conn, "index.pca_dims", 16)
    run_rebuild_index(ctx_for(reg))
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "build_index"]
    params = json.loads(job["params"])
    sink_build_index(make_sink_ctx(reg, "a", params, build_index(params, lambda p: None)))
    # a new vector equal to file 0's vector must find file 0's key at distance ~0
    row = fingerprints.frames_of_file(vconn, ids[0], MODEL)[0]
    vec = np.frombuffer(bytes(row["vector"]), dtype=np.float16).astype(np.float32)
    [new_key] = fingerprints.allocate_keys(vconn, b"z" * 16, MODEL, [0])
    import base64

    hits = match(
        {
            "model": {
                "id": MODEL,
                "dim": 512,
                "path": "",
                "input_size": 288,
                "mean": [0, 0, 0],
                "std": [1, 1, 1],
            },
            "index_path": str(reg.paths("a").index),
            "index": params["index"],
            "entries": [
                {
                    "seq": new_key,
                    "vector": base64.b64encode(vec.astype(np.float16).tobytes()).decode(),
                }
            ],
            "remove": [],
            "top_k": 5,
            "candidate_distance": 0.5,
        },
        lambda p: None,
    )
    key0 = fingerprints.allocate_keys(vconn, ids[0], MODEL, [0])[0]
    assert any(h == key0 and d < 0.01 for _, h, d in hits["hits"])


def test_build_sink_requeues_when_the_options_changed_during_the_build(world):  # noqa: F811
    """Final review I1: a change while a rebuild runs is not lost."""
    reg = world
    conn = reg.conn("a")
    seed_vectors(reg, 10)
    run_rebuild_index(ctx_for(reg))
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "build_index"]
    params = json.loads(job["params"])
    jobs_db.mark_running(conn, bytes(job["id"]))
    settings_db.set(conn, "index.quantization", "i8")  # changed while the child builds
    sink_build_index(make_sink_ctx(reg, "a", params, build_index(params, lambda p: None)))
    assert fingerprints.get_meta(reg.vectors_conn("a"), "quantization") == "f16"  # what was built
    assert any(j["type"] == "rebuild_index" for j in jobs_db.list_jobs(conn, state="queued"))


def test_match_skips_instead_of_failing_when_the_index_was_rebuilt_with_other_options(world):  # noqa: F811
    """Final review I2."""
    reg = world
    seed_vectors(reg, 5)
    run_rebuild_index(ctx_for(reg))
    conn = reg.conn("a")
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "build_index"]
    params = json.loads(job["params"])
    sink_build_index(make_sink_ctx(reg, "a", params, build_index(params, lambda p: None)))
    stale = {**params["index"], "quantization": "i8"}  # a match queued before the change
    result = match(
        {
            "model": {
                "id": MODEL,
                "dim": 512,
                "path": "",
                "input_size": 288,
                "mean": [0, 0, 0],
                "std": [1, 1, 1],
            },
            "index_path": str(reg.paths("a").index),
            "index": stale,
            "entries": [],
            "remove": [],
            "top_k": 5,
            "candidate_distance": 0.5,
        },
        lambda p: None,
    )
    assert result["hits"] == [] and result["skipped"]


def test_too_few_vectors_for_pca_builds_without_it_and_is_not_stale(world):  # noqa: F811
    """Final review I3: an impossible projection must not wedge the profile."""
    reg = world
    conn = reg.conn("a")
    seed_vectors(reg, 5)
    settings_db.set(conn, "index.pca_dims", 32)
    run_rebuild_index(ctx_for(reg))
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "build_index"]
    params = json.loads(job["params"])
    assert params["index"]["pca_dims"] == 0 and params["index"]["index_dim"] == 512
    sink_build_index(make_sink_ctx(reg, "a", params, build_index(params, lambda p: None)))
    assert not index_is_stale(reg, "a")
    assert not (reg.paths("a").root / "pca.npz").exists()


def test_match_params_describe_the_index_on_disk_not_the_settings(world):  # noqa: F811
    """Final review I2 (forward direction): enqueue_match reads index_meta."""
    import base64

    from visualstore.jobs.results import enqueue_match

    reg = world
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    seed_vectors(reg, 40)
    settings_db.set(conn, "index.pca_dims", 16)
    run_rebuild_index(ctx_for(reg))
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "build_index"]
    params = json.loads(job["params"])
    sink_build_index(make_sink_ctx(reg, "a", params, build_index(params, lambda p: None)))
    settings_db.set(conn, "index.pca_dims", 0)  # changed again, rebuild not run yet
    model = {"id": MODEL, "dim": 512}
    vec = base64.b64encode(np.zeros(512, np.float16).tobytes()).decode()
    enqueue_match(
        conn, vconn, settings_db.all(conn), reg.paths("a"), model, [{"seq": 1, "vector": vec}], 50
    )
    [m] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "match"]
    assert json.loads(m["params"])["index"]["pca_dims"] == 16  # what the index on disk is


def test_i8_rebuild_returns_the_same_nearest_neighbours_as_f16(world):  # noqa: F811
    """Review Focus 1: quantisation must not change which files are near each other."""
    from visualstore.index.usearch_index import VectorIndex

    reg = world
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    rng = np.random.default_rng(1)
    clusters: dict[int, list[bytes]] = {}
    for c in range(20):
        base = rng.normal(size=512).astype(np.float32)
        for j in range(3):
            fid = bytes([c + 1, j + 1]) * 8
            vec = base + rng.normal(scale=0.05, size=512).astype(np.float32)
            vec /= np.linalg.norm(vec)
            fingerprints.replace_frames(
                vconn, fid, MODEL, [(0, 0, vec.astype(np.float16).tobytes())]
            )
            fingerprints.allocate_keys(vconn, fid, MODEL, [0])
            clusters.setdefault(c, []).append(fid)
    stored: dict[bytes, np.ndarray] = {}
    key_of: dict[bytes, int] = {}
    for rows in fingerprints.iter_vectors(vconn, MODEL):
        for r in rows:
            fid = bytes(r["file_id"])
            stored[fid] = np.frombuffer(bytes(r["vector"]), dtype=np.float16).astype(np.float32)
            key_of[fid] = int(r["seq"])

    def neighbours(quantization: str) -> dict[bytes, set[int]]:
        run_rebuild_index(ctx_for(reg))
        [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "build_index"]
        params = json.loads(job["params"])
        sink_build_index(make_sink_ctx(reg, "a", params, build_index(params, lambda p: None)))
        jobs_db.mark_done(conn, job["id"])
        index = VectorIndex.open_or_create(reg.paths("a").index, 512, quantization)
        return {fid: {k for k, _ in index.search(vec, 3)} for fid, vec in stored.items()}

    exact = neighbours("f16")
    settings_db.set(conn, "index.quantization", "i8")
    quantised = neighbours("i8")
    assert inspect(reg.paths("a").index)["dtype"] == "i8"
    for members in clusters.values():
        expected = {key_of[fid] for fid in members}
        for fid in members:
            assert exact[fid] == expected == quantised[fid]
