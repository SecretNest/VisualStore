import hashlib
import json
import os
import time

import pytest

from tests.fixtures.models import install_fake_model
from visualstore.db import files, folders, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.scan import ScanError, run_scan
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    install_fake_model(reg.data_dir / "models")
    lib = tmp_path / "lib"
    (lib / "sub").mkdir(parents=True)
    (lib / "Thumbs").mkdir()
    (lib / ".visualstore-trash" / "2026-11").mkdir(parents=True)
    (lib / "sub" / "one.jpg").write_bytes(b"one")
    (lib / "two.PNG").write_bytes(b"two")
    (lib / "notes.txt").write_bytes(b"no")
    (lib / "Thumbs" / "t.jpg").write_bytes(b"t")
    (lib / ".visualstore-trash" / "2026-11" / "old.jpg").write_bytes(b"old")
    conn = reg.conn("a")
    folder = folders.create_folder(
        conn, name="Lib", path=str(lib), role="inbox", compare_scope="all"
    )
    folders.add_ignored(conn, folder, "Thumbs")
    yield reg, conn, folder, lib
    reg.close()


def ctx_for(reg, folder, priority=None):
    conn = reg.conn("a")
    params = {"folder_id": id_to_str(folder)}
    if priority is not None:
        params["priority"] = priority
    return IoContext(
        profile="a",
        paths=reg.paths("a"),
        backup_dir=reg.data_dir / "bk",
        global_db_path=reg.global_db_path,
        settings=settings_db.all(conn),
        params=params,
        report=lambda p: None,
        model_dir=reg.data_dir / "models",
    )


def rel_paths(conn, folder):
    rows = files.list_files_in_folder(conn, folder)
    return sorted(files.relative_path(r["directory"], r["name"]) for r in rows)


def test_first_scan_inserts_media_files_only_and_queues_fingerprints(world):
    reg, conn, folder, lib = world
    result = run_scan(ctx_for(reg, folder))
    assert result["inserted"] == 2 and result["deleted"] == 0 and result["fingerprint_jobs"] == 1
    assert rel_paths(conn, folder) == ["sub/one.jpg", "two.PNG"]
    [job] = jobs_db.list_jobs(conn, state="queued")
    assert job["type"] == "fingerprint" and job["kind"] == "compute" and job["priority"] == 50
    params = json.loads(job["params"])
    assert sorted(f["path"] for f in params["files"]) == [
        str(lib / "sub" / "one.jpg"),
        str(lib / "two.PNG"),
    ]
    assert all(f["media_type"] == "image" for f in params["files"])
    assert IO_HANDLERS["scan"] is run_scan


def test_rescan_is_a_no_op_and_user_priority_propagates(world):
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    result = run_scan(ctx_for(reg, folder, priority=100))
    assert (result["inserted"], result["changed"], result["deleted"]) == (0, 0, 0)
    assert result["unchanged"] == 2
    assert jobs_db.list_jobs(conn, state="queued")[0]["priority"] == 100


def test_moved_file_keeps_id_sha1_and_pairs(world):
    """Review Focus 2."""
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    one = files.find_file(conn, folder, "sub", "one.jpg")
    two = files.find_file(conn, folder, "", "two.PNG")
    files.set_fingerprint_result(
        conn, one["id"], sha1=hashlib.sha1(b"one").digest(), state="fingerprinted"
    )
    files.set_fingerprint_result(
        conn, two["id"], sha1=hashlib.sha1(b"two").digest(), state="fingerprinted"
    )
    pairs.upsert_sha1_pair(conn, one["id"], two["id"])  # pretend they were related
    with conn:  # drop the first scan's fingerprint job; both files are hashed already
        conn.execute("DELETE FROM job")

    (lib / "moved").mkdir()
    (lib / "sub" / "one.jpg").rename(lib / "moved" / "renamed.jpg")
    result = run_scan(ctx_for(reg, folder))
    assert result["moved"] == 1 and result["inserted"] == 0 and result["deleted"] == 0
    moved = files.find_file(conn, folder, "moved", "renamed.jpg")
    assert moved["id"] == one["id"] and moved["sha1"] == hashlib.sha1(b"one").digest()
    assert moved["state"] == "fingerprinted"
    assert len(pairs.pairs_of_file(conn, one["id"])) == 1
    assert files.find_file(conn, folder, "sub", "one.jpg") is None
    for job in jobs_db.list_jobs(conn, state="queued"):
        queued_ids = [f["id"] for f in json.loads(job["params"]).get("files", [])]
        assert id_to_str(one["id"]) not in queued_ids  # a moved file is not re-fingerprinted


def test_changed_content_resets_hash_and_pairs(world):
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    one = files.find_file(conn, folder, "sub", "one.jpg")
    two = files.find_file(conn, folder, "", "two.PNG")
    files.set_fingerprint_result(conn, one["id"], sha1=b"\x01" * 20, state="fingerprinted")
    files.set_fingerprint_result(conn, two["id"], sha1=b"\x01" * 20, state="fingerprinted")
    pairs.upsert_sha1_pair(conn, one["id"], two["id"])
    (lib / "sub" / "one.jpg").write_bytes(b"one-changed")
    os.utime(lib / "sub" / "one.jpg", ns=(time.time_ns(), time.time_ns() + 10**9))
    result = run_scan(ctx_for(reg, folder))
    assert result["changed"] == 1
    row = files.get_file(conn, one["id"])
    assert row["state"] == "new" and row["sha1"] is None and row["size"] == len(b"one-changed")
    assert pairs.pairs_of_file(conn, one["id"]) == []


def test_deleted_file_is_removed_and_ambiguous_copies_are_not_moves(world):
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    one = files.find_file(conn, folder, "sub", "one.jpg")
    files.set_fingerprint_result(
        conn, one["id"], sha1=hashlib.sha1(b"one").digest(), state="fingerprinted"
    )
    (lib / "sub" / "one.jpg").unlink()
    (lib / "copy1.jpg").write_bytes(b"one")
    (lib / "copy2.jpg").write_bytes(b"one")
    (lib / "two.PNG").unlink()
    result = run_scan(ctx_for(reg, folder))
    assert result["moved"] == 0 and result["inserted"] == 2 and result["deleted"] == 2
    assert files.get_file(conn, one["id"]) is None
    assert rel_paths(conn, folder) == ["copy1.jpg", "copy2.jpg"]


def test_disabled_folder_is_skipped(world):
    reg, conn, folder, lib = world
    folders.update_folder(conn, folder, enabled=False)
    assert run_scan(ctx_for(reg, folder)) == {"skipped": "folder disabled"}


def test_unavailable_or_empty_root_refuses_to_wipe_the_folder(world):
    """Final review C1: an offline share must not delete every row (hashes, judgments)."""
    import shutil

    from visualstore.jobs.io.scan import ScanError

    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    assert len(files.list_files_in_folder(conn, folder)) == 2
    shutil.rmtree(lib)
    with pytest.raises(ScanError, match="not available"):
        run_scan(ctx_for(reg, folder))
    assert len(files.list_files_in_folder(conn, folder)) == 2
    lib.mkdir()  # the mount point exists but the share is not mounted: empty root
    with pytest.raises(ScanError, match="refusing"):
        run_scan(ctx_for(reg, folder))
    assert len(files.list_files_in_folder(conn, folder)) == 2


def test_case_only_rename_updates_the_stored_spelling(world):
    """Final review I1: on a case-sensitive filesystem the stored name must match the disk."""
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    (lib / "two.PNG").rename(lib / "TWO.png")
    result = run_scan(ctx_for(reg, folder))
    assert result["unchanged"] == 2 and result["renamed"] == 1
    assert files.find_file(conn, folder, "", "TWO.png")["name"] == "TWO.png"


def test_case_colliding_names_are_skipped_not_fatal(world):
    reg, conn, folder, lib = world
    (lib / "Dup.jpg").write_bytes(b"1")
    (lib / "dup.jpg").write_bytes(b"2")
    result = run_scan(ctx_for(reg, folder))
    assert result["collisions"] == 1
    assert len([p for p in rel_paths(conn, folder) if p.lower() == "dup.jpg"]) == 1


def test_deletions_queue_a_regroup(world):
    """Final review I4."""
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    (lib / "two.PNG").unlink()
    run_scan(ctx_for(reg, folder))
    assert jobs_db.has_active(conn, "regroup")


def test_fingerprint_jobs_carry_model_and_limits(world):
    reg, conn, folder, lib = world
    run_scan(ctx_for(reg, folder))
    [job] = jobs_db.list_jobs(conn, state="queued")
    params = json.loads(job["params"])
    assert params["model"]["id"] == "sscd-resnet50-512-v1" and params["model"]["dim"] == 512
    assert params["model"]["path"].endswith("sscd-resnet50-512-v1.onnx")
    assert params["max_pixels"] == 80_000_000 and params["priority"] == 50


def test_scan_fails_before_recording_anything_when_the_model_is_unavailable(world):
    reg, conn, folder, lib = world
    # the fixture registers the model as local-only, so without its file it is not downloadable
    (reg.data_dir / "models" / "sscd-resnet50-512-v1.onnx").unlink()
    with pytest.raises(ScanError, match="model"):
        run_scan(ctx_for(reg, folder))
    assert rel_paths(conn, folder) == [] and jobs_db.list_jobs(conn) == []
