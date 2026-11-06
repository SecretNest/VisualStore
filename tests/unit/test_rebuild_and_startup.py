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
from visualstore.jobs.results import sink_build_index, sink_fingerprint
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
    assert built["count"] == 3 and inspect(reg.paths("a").index) == {"dimensions": 512, "count": 3}
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
    assert result == {"exported": 0, "built": True}
    assert inspect(reg.paths("a").index) == {"dimensions": 512, "count": 0}
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
