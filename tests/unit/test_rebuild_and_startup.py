import base64
import json

import numpy as np
import pytest

from tests.unit.sinkctx import make_sink_ctx
from tests.unit.test_scheduler import FakePool
from visualstore.db import files, fingerprints, folders
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.index.usearch_index import VectorIndex, inspect
from visualstore.jobs.compute.handlers import build_index
from visualstore.jobs.io.rebuild import run_rebuild_index
from visualstore.jobs.results import sink_build_index, sink_fingerprint, sink_match
from visualstore.jobs.scheduler import IoContext, Scheduler
from visualstore.jobs.startup import check_indexes
from visualstore.profiles import ProfileRegistry

MODEL = "sscd-resnet50-512-v1"


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    yield reg
    reg.close()


def ctx_for(reg, params=None):
    conn = reg.conn("a")
    return IoContext(
        profile="a",
        paths=reg.paths("a"),
        backup_dir=reg.data_dir / "bk",
        global_db_path=reg.global_db_path,
        settings=settings_db.all(conn),
        params=params or {},
        report=lambda p: None,
        model_dir=reg.data_dir / "models",
    )


def seed_vectors(reg, n=3, dim=512):
    vconn = reg.vectors_conn("a")
    rng = np.random.default_rng(0)
    ids = []
    for i in range(n):
        fid = bytes([i + 1]) * 16
        vec = rng.normal(size=dim).astype(np.float32)
        vec /= np.linalg.norm(vec)
        fingerprints.replace_frames(vconn, fid, MODEL, [(0, 0, vec.astype(np.float16).tobytes())])
        fingerprints.allocate_keys(vconn, fid, MODEL, [0])
        ids.append(fid)
    return ids


def test_rebuild_exports_then_build_writes_index_and_meta(world):
    reg = world
    seed_vectors(reg, n=3)
    result = run_rebuild_index(ctx_for(reg))
    assert result["exported"] == 3
    conn = reg.conn("a")
    [job] = jobs_db.list_jobs(conn, state="queued")
    params = json.loads(job["params"])
    assert job["type"] == "build_index" and params["dim"] == 512 and params["model_id"] == MODEL
    built = build_index(params, lambda p: None)
    assert built["count"] == 3 and inspect(reg.paths("a").index) == {
        "dimensions": 512,
        "count": 3,
        "dtype": "f16",
    }
    assert not (reg.paths("a").root / "index.export.npz").exists()
    sink_build_index(make_sink_ctx(reg, "a", params, built))
    vconn = reg.vectors_conn("a")
    assert (
        fingerprints.get_meta(vconn, "model_id") == MODEL
        and fingerprints.get_meta(vconn, "dim") == "512"
    )
    assert fingerprints.get_meta(vconn, "built_at")


def test_rebuild_with_no_vectors_writes_an_empty_index(world):
    reg = world
    result = run_rebuild_index(ctx_for(reg))
    assert result == {"exported": 0, "built": True, "match_jobs": 0}
    assert inspect(reg.paths("a").index) == {"dimensions": 512, "count": 0, "dtype": "f16"}
    assert jobs_db.list_jobs(reg.conn("a"), state="queued") == []


def test_startup_check_enqueues_rebuild_when_index_is_stale(world):
    """Review Focus 1."""
    reg = world
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    assert check_indexes(reg) == 0  # nothing to index yet
    seed_vectors(reg, n=2)
    assert check_indexes(reg) == 1  # vectors but no index file
    assert check_indexes(reg) == 0  # already queued
    for j in jobs_db.list_jobs(conn):
        jobs_db.cancel(conn, j["id"])
    VectorIndex.empty(512).save(reg.paths("a").index)
    fingerprints.set_meta(vconn, "model_id", "some-other-model")
    assert check_indexes(reg) == 1  # meta names another model
    for j in jobs_db.list_jobs(conn, state="queued"):
        jobs_db.cancel(conn, j["id"])
    fingerprints.set_meta(vconn, "model_id", MODEL)
    assert check_indexes(reg) == 0
    reg.paths("a").index.write_bytes(b"corrupt")
    assert check_indexes(reg) == 1


def test_fingerprint_sink_routes_to_rebuild_when_index_is_missing(world):
    reg = world
    conn = reg.conn("a")
    (reg.data_dir / "lib").mkdir()
    folder = folders.create_folder(
        conn, name="L", path=str(reg.data_dir / "lib"), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]
    fid = files.insert_file(
        conn, folder_id=folder, directory="", name="a.jpg", extension_id=ext, size=1, mtime=0
    )
    vec = base64.b64encode(
        (np.ones(512, dtype=np.float32) / np.sqrt(512)).astype(np.float16).tobytes()
    ).decode()
    model = {
        "id": MODEL,
        "dim": 512,
        "path": "",
        "input_size": 288,
        "mean": [0, 0, 0],
        "std": [1, 1, 1],
    }
    result = {
        "files": [
            {
                "id": id_to_str(fid),
                "sha1": "aa" * 20,
                "state": "fingerprinted",
                "size": 1,
                "frames": [{"index": 0, "time_ms": 0, "vector": vec}],
                "model_id": MODEL,
            }
        ]
    }
    sink_fingerprint(make_sink_ctx(reg, "a", {"model": model, "priority": 50}, result))
    types = sorted(j["type"] for j in jobs_db.list_jobs(conn, state="queued"))
    assert types == ["rebuild_index"]  # not "match": there is no index yet


async def test_scheduler_runs_one_exclusive_compute_job_per_profile(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    reg.create("b")
    pool = FakePool(size=4)
    sched = Scheduler(
        reg,
        pool,
        io_handlers={},
        backup_dir=tmp_path / "bk",
        model_dir=tmp_path / "models",
        plan_interval=1000,
    )
    a, b = reg.conn("a"), reg.conn("b")
    jobs_db.enqueue(a, type="match", kind="compute", priority=100, params={})
    jobs_db.enqueue(a, type="match", kind="compute", priority=90, params={})
    jobs_db.enqueue(a, type="ping", kind="compute", priority=80, params={})
    jobs_db.enqueue(b, type="build_index", kind="compute", priority=70, params={})
    await sched.tick()
    submitted = [(r.profile, r.type) for r in pool.submitted]
    assert submitted.count(("a", "match")) == 1 and ("a", "ping") in submitted
    assert ("b", "build_index") in submitted
    assert len(submitted) == 3  # the second match for a waits
    reg.close()


def test_vectors_written_during_a_rebuild_are_matched_afterwards(world):
    """Final review, Critical 2: batches landing between the export and the build must not be
    silently left out of the index."""
    reg = world
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    seed_vectors(reg, 3)
    run_rebuild_index(ctx_for(reg))
    [job] = jobs_db.list_jobs(conn, state="queued")
    params = json.loads(job["params"])
    assert job["type"] == "build_index" and params["max_seq"] == 3
    [fourth] = seed_vectors(reg, 4)[3:]  # fingerprinted while the child builds the index
    late_keys = [
        r["seq"] for r in vconn.execute("SELECT seq FROM index_key WHERE seq > 3 AND deleted = 0")
    ]
    assert fingerprints.resolve_keys(vconn, late_keys) == {late_keys[0]: (fourth, 0)}
    result = build_index(params, lambda p: None)
    jobs_db.mark_done(conn, bytes(job["id"]))
    sink_build_index(make_sink_ctx(reg, "a", params, result))
    queued = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "match"]
    assert len(queued) == 1
    match_params = json.loads(queued[0]["params"])
    assert [e["seq"] for e in match_params["entries"]] == late_keys
    assert match_params["model"]["id"] == MODEL and match_params["index_path"] == str(
        reg.paths("a").index
    )
    assert fingerprints.get_meta(vconn, "model_id") == MODEL


def test_sinks_ignore_results_computed_with_a_model_that_is_no_longer_active(world):
    """Final review, Important 3: a job queued before a model switch must not feed the new index."""
    reg = world
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    folder = folders.create_folder(
        conn, name="L", path=str(reg.data_dir), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]
    fid = files.insert_file(
        conn, folder_id=folder, directory="", name="a.jpg", extension_id=ext, size=1, mtime=1
    )
    foreign = {"id": "dinov2-vits14-384-v1", "dim": 384}
    vector = base64.b64encode(np.zeros(384, np.float16).tobytes()).decode()
    result = {
        "files": [
            {
                "id": id_to_str(fid),
                "sha1": "00" * 20,
                "state": "fingerprinted",
                "size": 1,
                "mtime": 1,
                "frames": [{"index": 0, "time_ms": 0, "vector": vector}],
                "model_id": foreign["id"],
            }
        ]
    }
    sink_fingerprint(make_sink_ctx(reg, "a", {"model": foreign}, result))
    assert files.get_file(conn, fid)["state"] == "fingerprinted"  # the hash is still valid
    assert fingerprints.vector_count(vconn, foreign["id"]) == 0
    assert jobs_db.list_jobs(conn) == []
    sink_match(make_sink_ctx(reg, "a", {"model": foreign, "remove": []}, {"hits": [[1, 2, 0.1]]}))
    assert conn.execute("SELECT COUNT(*) FROM pair").fetchone()[0] == 0


def test_startup_cleans_frames_of_unknown_files_and_requeues_files_without_vectors(world):
    """Review Focus 3: a metadata restore older than vectors.db heals itself."""
    from tests.fixtures.models import install_fake_model
    from visualstore.jobs.startup import check_consistency

    reg = world
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    install_fake_model(reg.data_dir / "models")
    folder = folders.create_folder(
        conn, name="L", path=str(reg.data_dir), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]
    kept = files.insert_file(
        conn, folder_id=folder, directory="", name="kept.jpg", extension_id=ext, size=1, mtime=1
    )
    files.set_fingerprint_result(conn, kept, sha1=b"\x02" * 20, state="fingerprinted")
    lost = files.insert_file(
        conn, folder_id=folder, directory="", name="lost.jpg", extension_id=ext, size=1, mtime=1
    )
    files.set_fingerprint_result(
        conn, lost, sha1=b"\x03" * 20, state="fingerprinted"
    )  # no vectors: restored row
    vec = np.zeros(512, np.float16).tobytes()
    fingerprints.replace_frames(vconn, kept, MODEL, [(0, 0, vec)])
    fingerprints.allocate_keys(vconn, kept, MODEL, [0])
    unknown = b"n" * 16  # a file newer than the restored metadata
    fingerprints.replace_frames(vconn, unknown, MODEL, [(0, 0, vec)])
    [ghost_key] = fingerprints.allocate_keys(vconn, unknown, MODEL, [0])
    report = check_consistency(reg, reg.data_dir / "models")
    assert report == {"unknown_file_frames": 1, "files_without_vectors": 1, "rebuilds": 1}
    assert fingerprints.frames_of_file(vconn, unknown, MODEL) == []
    assert fingerprints.pending_removals(vconn, MODEL) == [ghost_key]
    queued = jobs_db.list_jobs(conn, state="queued")
    assert {j["type"] for j in queued} == {"rebuild_index", "fingerprint"}
    [fpj] = [j for j in queued if j["type"] == "fingerprint"]
    assert [e["id"] for e in json.loads(fpj["params"])["files"]] == [id_to_str(lost)]
    assert check_consistency(reg, reg.data_dir / "models") == {
        "unknown_file_frames": 0,
        "files_without_vectors": 0,
        "rebuilds": 0,
    }
