import base64
import json

import numpy as np
import pytest

from tests.fixtures.images import garbage, png_bytes
from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
from tests.fixtures.vips import require_pyvips
from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, fingerprints, folders
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.compute.handlers import fingerprint
from visualstore.jobs.results import sink_fingerprint
from visualstore.models.registry import ModelError, job_params
from visualstore.profiles import ProfileRegistry

pyvips = require_pyvips()


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    lib = tmp_path / "lib"
    lib.mkdir()
    folder = folders.create_folder(conn, name="L", path=str(lib), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    spec = tiny_spec(8)
    build_tiny_model(model_dir / spec.file_name(), dim=8)
    settings_db.set(conn, "model.id", spec.id)  # the sinks only accept the active model

    def add(name, data, media="jpg"):
        (lib / name).write_bytes(data)
        st = (lib / name).stat()
        return files.insert_file(
            conn,
            folder_id=folder,
            directory="",
            name=name,
            extension_id=ext[media]["id"],
            size=st.st_size,
            mtime=st.st_mtime_ns,
        )

    yield reg, conn, lib, model_dir, spec, add
    reg.close()


def entry(fid, lib, name, media_type="image"):
    return {"id": id_to_str(fid), "path": str(lib / name), "media_type": media_type}


def test_handler_adds_vectors_thumbnails_and_states(world):
    reg, conn, lib, model_dir, spec, add = world
    good = add("good.png", png_bytes(40, 30, gradient=True))
    bad = add("zip.jpg", garbage())
    other = add("doc.mp4", b"not decoded in milestone 3", media="mp4")
    result = fingerprint(
        {
            "files": [
                entry(good, lib, "good.png"),
                entry(bad, lib, "zip.jpg"),
                entry(other, lib, "doc.mp4", "video"),
            ],
            "model": job_params(spec, model_dir),
            "max_pixels": 10_000_000,
        },
        lambda p: None,
    )
    by_id = {e["id"]: e for e in result["files"]}
    g = by_id[id_to_str(good)]
    assert g["state"] == "fingerprinted" and g["model_id"] == spec.id and len(g["frames"]) == 1
    vec = np.frombuffer(base64.b64decode(g["frames"][0]["vector"]), dtype=np.float16)
    assert vec.shape == (8,) and abs(float(np.linalg.norm(vec.astype(np.float32))) - 1.0) < 0.01
    assert base64.b64decode(g["thumbnail"])[:4] == b"RIFF"
    b = by_id[id_to_str(bad)]
    assert b["state"] == "not_media" and b["sha1"] and "frames" not in b
    o = by_id[id_to_str(other)]
    assert o["state"] == "fingerprinted" and "frames" not in o  # sha1 only until milestone 5


def test_missing_model_file_fails_the_job_not_the_files(world):
    reg, conn, lib, model_dir, spec, add = world
    good = add("good.png", png_bytes(8, 8))
    params = job_params(spec, model_dir)
    params["path"] = str(model_dir / "absent.onnx")
    with pytest.raises(ModelError):
        fingerprint(
            {"files": [entry(good, lib, "good.png")], "model": params, "max_pixels": 10_000_000},
            lambda p: None,
        )


def test_sink_stores_frames_thumbnail_and_queues_match(world):
    reg, conn, lib, model_dir, spec, add = world
    good = add("good.png", png_bytes(40, 30, gradient=True))
    params = {
        "files": [entry(good, lib, "good.png")],
        "model": job_params(spec, model_dir),
        "max_pixels": 10_000_000,
        "priority": 50,
    }
    result = fingerprint(params, lambda p: None)
    vconn = reg.vectors_conn("a")
    # an index already exists for this model, so the sink must queue a match (not a rebuild)
    from visualstore.index.usearch_index import VectorIndex

    VectorIndex.empty(8).save(reg.paths("a").index)
    fingerprints.set_meta(vconn, "model_id", spec.id)
    sink_fingerprint(make_sink_ctx(reg, "a", params, result, model_dir))
    assert len(fingerprints.frames_of_file(vconn, good, spec.id)) == 1
    [seq] = fingerprints.allocate_keys(vconn, good, spec.id, [0])
    thumb = reg.paths("a").thumbs / id_to_str(good)[:2] / f"{id_to_str(good)}.webp"
    assert thumb.read_bytes()[:4] == b"RIFF"
    [job] = [j for j in jobs_db.list_jobs(conn) if j["type"] == "match"]
    p = json.loads(job["params"])
    assert p["index_path"] == str(reg.paths("a").index) and p["model"]["id"] == spec.id
    assert p["entries"][0]["seq"] == seq and p["top_k"] == 64 and p["candidate_distance"] == 0.5
    assert job["priority"] == 50 and job["kind"] == "compute"


def test_unexpected_embedding_errors_mark_the_file_unreadable_not_the_batch(world, monkeypatch):
    """Final review, Important 7 (Review Focus 4): one odd file must not cost the batch."""
    import visualstore.jobs.compute.handlers as handlers

    reg, conn, lib, model_dir, spec, add = world
    good = add("good.png", png_bytes(40, 30, gradient=True))
    odd = add("odd.png", png_bytes(20, 20))
    real = handlers._embed_image

    def flaky(path, model, max_pixels):
        if path.endswith("odd.png"):
            raise RuntimeError("onnxruntime exploded")
        return real(path, model, max_pixels)

    monkeypatch.setattr(handlers, "_embed_image", flaky)
    result = fingerprint(
        {
            "files": [entry(good, lib, "good.png"), entry(odd, lib, "odd.png")],
            "model": job_params(spec, model_dir),
            "max_pixels": 10_000_000,
        },
        lambda p: None,
    )
    by_id = {e["id"]: e for e in result["files"]}
    assert by_id[id_to_str(good)]["state"] == "fingerprinted" and by_id[id_to_str(good)]["frames"]
    assert (
        by_id[id_to_str(odd)]["state"] == "unreadable"
        and "exploded" in by_id[id_to_str(odd)]["reason"]
    )
    assert by_id[id_to_str(odd)]["sha1"]  # the hash is still recorded
