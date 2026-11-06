import base64
import json

import numpy as np
import pytest

from tests.fixtures.images import garbage, png_bytes
from tests.fixtures.media import make_clip, make_gif, make_webp_anim, require_ffmpeg
from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
from tests.fixtures.vips import require_pyvips
from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, fingerprints, folders
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.index.usearch_index import VectorIndex
from visualstore.jobs.compute.handlers import fingerprint
from visualstore.jobs.results import sink_fingerprint
from visualstore.models.registry import ModelError, job_params
from visualstore.profiles import ProfileRegistry

pyvips = require_pyvips()
require_ffmpeg()


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
    other = add("doc.mp4", b"a text file wearing a video extension", media="mp4")
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
    assert o["state"] == "not_media" and o["sha1"] and "frames" not in o  # ffprobe: no stream


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
    real = handlers._frames_for

    def flaky(path, media_type, max_pixels, sampling):
        if path.endswith("odd.png"):
            raise RuntimeError("onnxruntime exploded")
        return real(path, media_type, max_pixels, sampling)

    monkeypatch.setattr(handlers, "_frames_for", flaky)
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


def test_handler_samples_frames_of_gif_webp_by_content_and_video(world):
    reg, conn, lib, model_dir, spec, add = world
    make_gif(lib / "anim.gif", 10)
    gif = add("anim.gif", (lib / "anim.gif").read_bytes(), media="gif")
    make_clip(lib / "clip.mp4", 3)
    clip = add("clip.mp4", (lib / "clip.mp4").read_bytes(), media="mp4")
    make_webp_anim(lib / "moving.webp", 4)
    webp = add("moving.webp", (lib / "moving.webp").read_bytes(), media="webp")  # extension: image
    result = fingerprint(
        {
            "files": [
                entry(gif, lib, "anim.gif", "animated"),
                entry(clip, lib, "clip.mp4", "video"),
                entry(webp, lib, "moving.webp", "image"),
            ],
            "model": job_params(spec, model_dir),
            "max_pixels": 10_000_000,
            "frames": {"max_per_file": 3, "ffmpeg_timeout_s": 60},
        },
        lambda p: None,
    )
    by_id = {e["id"]: e for e in result["files"]}
    g, c, w = by_id[id_to_str(gif)], by_id[id_to_str(clip)], by_id[id_to_str(webp)]
    assert [f["time_ms"] for f in g["frames"]] == [0, 800, 1800] and g["state"] == "fingerprinted"
    assert [f["time_ms"] for f in c["frames"]] == [0, 1000, 2000] and c["thumbnail"]
    assert [f["index"] for f in w["frames"]] == [0, 1, 2] and w["frames"][1][
        "time_ms"
    ] == 400  # page 2
    assert all(len(base64.b64decode(f["vector"])) == 16 for f in g["frames"] + c["frames"])
    thumb = pyvips.Image.new_from_buffer(base64.b64decode(c["thumbnail"]), "")
    assert thumb.width == 160  # middle frame, 256 px cap not reached


def test_sink_replaces_frames_and_retires_keys_when_fewer_frames_come_back(world):
    """Review Focus 4 through the sink."""
    reg, conn, lib, model_dir, spec, add = world
    vconn = reg.vectors_conn("a")
    VectorIndex.empty(8).save(reg.paths("a").index)
    fingerprints.set_meta(vconn, "model_id", spec.id)
    make_gif(lib / "anim.gif", 10)
    gif = add("anim.gif", (lib / "anim.gif").read_bytes(), media="gif")
    params = {"model": job_params(spec, model_dir), "max_pixels": 10_000_000}

    def run(max_frames):
        result = fingerprint(
            {
                **params,
                "files": [entry(gif, lib, "anim.gif", "animated")],
                "frames": {"max_per_file": max_frames, "ffmpeg_timeout_s": 60},
            },
            lambda p: None,
        )
        sink_fingerprint(make_sink_ctx(reg, "a", params, result, model_dir))

    run(8)
    assert len(fingerprints.frames_of_file(vconn, gif, spec.id)) == 8
    keys = fingerprints.allocate_keys(vconn, gif, spec.id, list(range(8)))
    run(2)
    assert len(fingerprints.frames_of_file(vconn, gif, spec.id)) == 2
    assert fingerprints.pending_removals(vconn, spec.id) == keys[2:]
    jobs = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "match"]
    assert json.loads(jobs[0]["params"])["remove"] == keys[2:]  # newest first
