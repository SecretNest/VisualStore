from tests.fixtures.images import garbage, png_bytes
from tests.fixtures.media import make_clip, make_gif, require_ffmpeg
from tests.fixtures.vips import require_pyvips
from tests.integration.conftest import login
from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, folders
from visualstore.ids import id_to_str
from visualstore.jobs.results import sink_fingerprint

pyvips = require_pyvips()
require_ffmpeg()


def seed(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    reg = client.app.state.vs.registry
    conn = reg.conn("a")
    lib = tmp_path / "lib"
    lib.mkdir()
    folder = folders.create_folder(conn, name="L", path=str(lib), role="inbox", compare_scope="all")
    ext = folders.extension_map(conn)

    def add(name, data, media="png"):
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

    return reg, lib, add


def test_preview_is_rendered_once_cached_and_served(client, tmp_path):
    reg, lib, add = seed(client, tmp_path)
    fid = id_to_str(add("big.png", png_bytes(1600, 1200, gradient=True)))
    got = client.get(f"/api/p/a/files/{fid}/preview")
    assert got.status_code == 200 and got.headers["content-type"] == "image/webp"
    img = pyvips.Image.new_from_buffer(got.content, "")
    assert (img.width, img.height) == (1024, 768)
    cache = reg.paths("a").previews / fid[:2] / f"{fid}.webp"
    assert cache.is_file() and not list(cache.parent.glob("*.tmp"))
    cache.write_bytes(b"RIFFcached")  # served from the cache from now on
    assert client.get(f"/api/p/a/files/{fid}/preview").content == b"RIFFcached"
    # a new fingerprint (changed file) drops the cached preview
    sink_fingerprint(
        make_sink_ctx(
            reg,
            "a",
            {},
            {
                "files": [
                    {"id": fid, "sha1": "00" * 20, "state": "fingerprinted", "thumbnail": "AAAA"}
                ]
            },
        )
    )
    assert not cache.exists()


def test_preview_failures_are_404_with_a_reason(client, tmp_path):
    reg, lib, add = seed(client, tmp_path)
    gone = id_to_str(add("gone.png", png_bytes(40, 30)))
    (lib / "gone.png").unlink()
    assert (
        client.get(f"/api/p/a/files/{gone}/preview").json()["detail"] == "The file is not on disk."
    )
    bad = id_to_str(add("bad.png", garbage()))
    r = client.get(f"/api/p/a/files/{bad}/preview")
    assert r.status_code == 404 and r.json()["detail"].startswith("Cannot decode the file")
    video = id_to_str(add("clip.mp4", b"not decoded yet", media="mp4"))
    assert (
        client.get(f"/api/p/a/files/{video}/preview").json()["detail"]
        == "No preview yet."
    )
    assert (
        client.get("/api/p/a/files/00000000-0000-4000-8000-000000000000/preview").status_code == 404
    )


def test_concurrent_requests_render_an_uncached_preview_once(client, tmp_path, monkeypatch):
    """Final review I4: the second request waits for the first render and serves the cache."""
    import threading
    import time

    import visualstore.media.decode as decode

    reg, lib, add = seed(client, tmp_path)
    fid = id_to_str(add("big.png", png_bytes(800, 600, gradient=True)))
    calls = []
    real = decode.preview_webp

    def slow(path, longest, max_pixels):
        calls.append(path)
        time.sleep(0.5)
        return real(path, longest, max_pixels)

    monkeypatch.setattr(decode, "preview_webp", slow)
    results = []

    def fetch():
        results.append(client.get(f"/api/p/a/files/{fid}/preview").status_code)

    threads = [threading.Thread(target=fetch) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200, 200, 200] and len(calls) == 1


def fingerprinted(reg, lib, fid, name, media_type):
    """Run the real handler + sink for one file so vectors.db has its frames."""
    from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
    from visualstore.db import settings as settings_db
    from visualstore.jobs.compute.handlers import fingerprint
    from visualstore.models.registry import job_params

    conn = reg.conn("a")
    model_dir = reg.data_dir / "models"
    model_dir.mkdir(exist_ok=True)
    spec = tiny_spec(8)
    build_tiny_model(model_dir / spec.file_name(), dim=8)
    settings_db.set(conn, "model.id", spec.id)
    params = {
        "model": job_params(spec, model_dir),
        "max_pixels": 10_000_000,
        "frames": {"max_per_file": 8, "ffmpeg_timeout_s": 60},
    }
    result = fingerprint(
        {
            **params,
            "files": [{"id": id_to_str(fid), "path": str(lib / name), "media_type": media_type}],
        },
        lambda p: None,
    )
    sink_fingerprint(make_sink_ctx(reg, "a", params, result, model_dir))


def test_frames_listing_and_frame_images(client, tmp_path):
    reg, lib, add = seed(client, tmp_path)
    make_clip(lib / "clip.mp4", 3)
    clip = add("clip.mp4", (lib / "clip.mp4").read_bytes(), media="mp4")
    make_gif(lib / "anim.gif", 10)
    gif = add("anim.gif", (lib / "anim.gif").read_bytes(), media="gif")
    still = add("still.png", png_bytes(30, 20))
    assert client.get(f"/api/p/a/files/{id_to_str(clip)}/frames").json() == {
        "media_type": "video",
        "frames": [],
    }
    for fid, name, media in (
        (clip, "clip.mp4", "video"),
        (gif, "anim.gif", "animated"),
        (still, "still.png", "image"),
    ):
        fingerprinted(reg, lib, fid, name, media)
    listed = client.get(f"/api/p/a/files/{id_to_str(clip)}/frames").json()
    assert listed["media_type"] == "video" and [f["time_ms"] for f in listed["frames"]] == [
        0,
        1000,
        2000,
    ]
    gif_frames = client.get(f"/api/p/a/files/{id_to_str(gif)}/frames").json()["frames"]
    assert [f["time_ms"] for f in gif_frames] == [i * 200 for i in (0, 1, 3, 4, 5, 6, 8, 9)]
    assert client.get(f"/api/p/a/files/{id_to_str(still)}/frames").json()["frames"] == [
        {"index": 0, "time_ms": 0}
    ]
    got = client.get(f"/api/p/a/files/{id_to_str(clip)}/frame/1")
    assert got.status_code == 200 and got.headers["content-type"] == "image/webp"
    assert pyvips.Image.new_from_buffer(got.content, "").width == 160
    cache = reg.paths("a").previews / "frames" / id_to_str(clip)[:2] / f"{id_to_str(clip)}-1.webp"
    assert cache.is_file()
    page = client.get(f"/api/p/a/files/{id_to_str(gif)}/frame/2")  # source page 3
    assert page.status_code == 200 and pyvips.Image.new_from_buffer(page.content, "").width == 64
    assert (
        client.get(f"/api/p/a/files/{id_to_str(clip)}/frame/9").json()["detail"] == "No such frame."
    )
    (lib / "clip.mp4").unlink()
    assert (
        client.get(f"/api/p/a/files/{id_to_str(clip)}/frame/2").json()["detail"]
        == "The file is not on disk."
    )
    # a new fingerprint drops the frame cache with the preview
    sink_fingerprint(
        make_sink_ctx(
            reg,
            "a",
            {},
            {
                "files": [
                    {
                        "id": id_to_str(clip),
                        "sha1": "00" * 20,
                        "state": "fingerprinted",
                        "thumbnail": "AAAA",
                    }
                ]
            },
        )
    )
    assert not cache.exists()


def test_preview_of_a_video_is_its_thumbnail_frame_once_fingerprinted(client, tmp_path):
    """Final review I1: multi-frame members get a real preview, not the fallback note."""
    reg, lib, add = seed(client, tmp_path)
    make_clip(lib / "clip.mp4", 3)
    clip = add("clip.mp4", (lib / "clip.mp4").read_bytes(), media="mp4")
    assert (
        client.get(f"/api/p/a/files/{id_to_str(clip)}/preview").json()["detail"]
        == "No preview yet."
    )
    fingerprinted(reg, lib, clip, "clip.mp4", "video")
    got = client.get(f"/api/p/a/files/{id_to_str(clip)}/preview")
    assert got.status_code == 200 and pyvips.Image.new_from_buffer(got.content, "").width == 160
    middle = client.get(f"/api/p/a/files/{id_to_str(clip)}/frame/1")
    assert got.content == middle.content  # the middle frame, like the thumbnail
