import json

from tests.fixtures.media import make_clip, require_ffmpeg
from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
from tests.fixtures.vips import require_pyvips
from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, folders, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.index.usearch_index import VectorIndex
from visualstore.jobs.compute.handlers import fingerprint, match
from visualstore.jobs.results import sink_fingerprint, sink_match
from visualstore.models.registry import job_params
from visualstore.profiles import ProfileRegistry

require_ffmpeg()
pyvips = require_pyvips()


def test_a_still_cut_from_a_video_lands_in_the_near_duplicate_band(tmp_path):
    """Review Focus 3: one vector space for stills and frames (design §7.3)."""
    import subprocess

    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    lib = tmp_path / "lib"
    lib.mkdir()
    folder = folders.create_folder(conn, name="L", path=str(lib), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    spec = tiny_spec(8)
    build_tiny_model(model_dir / spec.file_name(), dim=8)
    settings_db.set(conn, "model.id", spec.id)
    VectorIndex.empty(8).save(reg.paths("a").index)
    from visualstore.db import fingerprints as fp

    fp.set_meta(vconn, "model_id", spec.id)
    make_clip(lib / "clip.mp4", 3, colour="0x336699")
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "0",
            "-i",
            str(lib / "clip.mp4"),
            "-frames:v",
            "1",
            str(lib / "still.png"),
        ],
        check=True,
    )
    (lib / "other.png").write_bytes(
        __import__("tests.fixtures.images", fromlist=["png_bytes"]).png_bytes(
            64, 64, rgb=(250, 20, 20)
        )
    )

    def add(name, media):
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

    clip, still, other = add("clip.mp4", "mp4"), add("still.png", "png"), add("other.png", "png")
    params = {
        "model": job_params(spec, model_dir),
        "max_pixels": 10_000_000,
        "frames": {"max_per_file": 8, "ffmpeg_timeout_s": 60},
    }
    entries = [
        {"id": id_to_str(clip), "path": str(lib / "clip.mp4"), "media_type": "video"},
        {"id": id_to_str(still), "path": str(lib / "still.png"), "media_type": "image"},
        {"id": id_to_str(other), "path": str(lib / "other.png"), "media_type": "image"},
    ]
    result = fingerprint({**params, "files": entries}, lambda p: None)
    sink_fingerprint(make_sink_ctx(reg, "a", params, result, model_dir))
    [job] = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "match"]
    match_params = json.loads(job["params"])
    hits = match(match_params, lambda p: None)
    sink_match(make_sink_ctx(reg, "a", match_params, hits, model_dir))
    [pair] = [
        p
        for p in pairs.pairs_of_file(conn, still)
        if bytes(p["file_a"]) == clip or bytes(p["file_b"]) == clip
    ]
    assert pair["distance"] < 0.15, pair["distance"]
    assert pair["model_id"] == spec.id and pair["source"] == "vector"
    reg.close()
