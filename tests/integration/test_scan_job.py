import hashlib
import json
import os
import time

import pytest

from visualstore.db import files, folders, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.scan import run_scan
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
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
    conn.execute("DELETE FROM job")  # drop the first scan's fingerprint job; both files are hashed

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
