from tests.fixtures.images import garbage, png_bytes
from tests.fixtures.vips import require_pyvips
from tests.integration.conftest import login
from tests.unit.sinkctx import make_sink_ctx
from visualstore.db import files, folders
from visualstore.ids import id_to_str
from visualstore.jobs.results import sink_fingerprint

pyvips = require_pyvips()


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
        == "No preview for this media type yet."
    )
    assert (
        client.get("/api/p/a/files/00000000-0000-4000-8000-000000000000/preview").status_code == 404
    )
