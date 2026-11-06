import numpy as np
import pytest

from visualstore.db import files, fingerprints, folders
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.maintenance import run_maintenance
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry

MODEL = "sscd-resnet50-512-v1"


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    (tmp_path / "lib").mkdir()
    folder = folders.create_folder(
        conn, name="L", path=str(tmp_path / "lib"), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(name, state="fingerprinted"):
        fid = files.insert_file(
            conn, folder_id=folder, directory="", name=name, extension_id=ext, size=1, mtime=1
        )
        files.set_fingerprint_result(conn, fid, sha1=b"\x01" * 20, state=state)
        return fid

    yield reg, conn, add
    reg.close()


def ctx_for(reg, **overrides):
    conn = reg.conn("a")
    settings = settings_db.all(conn)
    settings.update(overrides)
    return IoContext(
        profile="a",
        paths=reg.paths("a"),
        backup_dir=reg.data_dir / "bk",
        global_db_path=reg.global_db_path,
        settings=settings,
        params={},
        report=lambda p: None,
        model_dir=reg.data_dir / "models",
    )


def thumb(reg, text):
    p = reg.paths("a").thumbs / text[:2] / f"{text}.webp"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_maintenance_removes_orphans_but_keeps_files_that_exist_in_any_state(world):
    """Review Focus 2."""
    reg, conn, add = world
    vconn = reg.vectors_conn("a")
    live, missing = add("live.jpg"), add("gone.jpg", state="missing")
    ghost = "00000000-0000-4000-8000-00000000abcd"
    kept = [thumb(reg, id_to_str(live)), thumb(reg, id_to_str(missing))]
    orphan = thumb(reg, ghost)
    preview = reg.paths("a").previews / ghost[:2] / f"{ghost}.webp"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"p")
    frame = reg.paths("a").previews / "frames" / ghost[:2] / f"{ghost}-1.webp"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"f")
    vec = np.zeros(512, np.float16).tobytes()
    fingerprints.replace_frames(vconn, live, MODEL, [(0, 0, vec)])
    fingerprints.allocate_keys(vconn, live, MODEL, [0])
    unknown = b"u" * 16
    fingerprints.replace_frames(vconn, unknown, MODEL, [(0, 0, vec), (1, 100, vec)])
    keys = fingerprints.allocate_keys(vconn, unknown, MODEL, [0, 1])
    result = run_maintenance(ctx_for(reg))
    assert (
        result["orphan_thumbnails"] == 1
        and result["orphan_previews"] == 1
        and result["orphan_frame_images"] == 1
    )
    assert result["unknown_file_frames"] == 2 and result["analyzed"] is True
    assert (
        all(p.exists() for p in kept)
        and not orphan.exists()
        and not preview.exists()
        and not frame.exists()
    )
    assert fingerprints.frames_of_file(vconn, unknown, MODEL) == [] and fingerprints.frames_of_file(
        vconn, live, MODEL
    )
    assert fingerprints.pending_removals(vconn, MODEL) == keys
    assert any(j["type"] == "rebuild_index" for j in jobs_db.list_jobs(conn, state="queued"))
    assert IO_HANDLERS["maintenance"] is run_maintenance
    again = run_maintenance(ctx_for(reg))  # idempotent
    assert again["orphan_thumbnails"] == 0 and again["unknown_file_frames"] == 0


def test_vacuum_only_above_the_free_page_ratio(world):
    reg, conn, add = world
    ids = [add(f"f{i}.jpg") for i in range(300)]
    files.delete_files(conn, ids)  # leaves free pages behind
    low = run_maintenance(ctx_for(reg, **{"maintenance.vacuum_free_ratio": 0.99}))
    assert low["vacuumed"] is False
    high = run_maintenance(ctx_for(reg, **{"maintenance.vacuum_free_ratio": 0.0}))
    assert high["vacuumed"] is True and high["free_ratio"] >= 0.0


def test_vacuum_waits_for_an_idle_job_table(world):
    """Final review I4: VACUUM blocks every writer; never while other jobs are active."""
    reg, conn, add = world
    ids = [add(f"g{i}.jpg") for i in range(300)]
    files.delete_files(conn, ids)
    jobs_db.enqueue(conn, type="scan", kind="io", priority=50)
    busy = run_maintenance(ctx_for(reg, **{"maintenance.vacuum_free_ratio": 0.0}))
    assert busy["vacuumed"] is False and busy["vacuum_skipped"] == "busy"
