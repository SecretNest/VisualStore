import sqlite3

import pytest

from tests.fixtures.models import install_fake_model
from tests.fixtures.v1db import build
from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.import_v1 import ImportError_, gid, run_import_v1
from visualstore.jobs.io.scan import run_scan
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    install_fake_model(tmp_path / "data" / "models")
    reg.open()
    reg.create("a")
    (tmp_path / "media" / "images").mkdir(parents=True)
    (tmp_path / "media" / "new").mkdir()
    ids = build(tmp_path / "v1.db")
    yield reg, reg.conn("a"), tmp_path, ids
    reg.close()


def ctx_for(reg, params):
    conn = reg.conn("a")
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


def params_for(tmp_path, **extra):
    base = {
        "db_path": str(tmp_path / "v1.db"),
        "folder_paths": {
            "Archive": str(tmp_path / "media" / "images"),
            "Inbox": str(tmp_path / "media" / "new"),
        },
    }
    base.update(extra)
    return base


def test_import_preserves_ids_and_maps_everything(world):
    """Review Focus 5."""
    reg, conn, tmp_path, ids = world
    result = run_import_v1(ctx_for(reg, params_for(tmp_path)))
    assert result["folders"] == 2 and result["files"] == 4 and result["sha1_pairs"] == 1
    assert result["related_keep"] == 1 and result["similar_skipped"] == 2
    assert result["similar_orphans"] == 1
    assert result["scan_jobs"] == 2 and result["extensions_added"] >= 1  # zip was not a default

    by_name = {r["name"]: r for r in folders.list_folders(conn)}
    assert id_to_str(by_name["Archive"]["id"]) == str(ids["archive"])  # bytes_le read correctly
    assert by_name["Archive"]["role"] == "archive"
    assert by_name["Archive"]["compare_scope"] == "other_directories"
    assert by_name["Inbox"]["role"] == "inbox"
    assert by_name["Inbox"]["path"] == str(tmp_path / "media" / "new")
    [ign] = folders.list_ignored(conn, by_name["Archive"]["id"])
    assert ign["directory"] == "Thumbs" and ign["recursive"] == 1

    ext = {r["extension"]: r for r in folders.list_extensions(conn)}
    assert ext["mp4"]["media_type"] == "video" and ext["zip"]["media_type"] == "other"
    assert ext["zip"]["enabled"] == 0
    assert id_to_str(ext["zip"]["id"]) == str(ids["zip"])
    assert id_to_str(ext["jpg"]["id"]) != str(ids["jpg"])  # default row kept, v1 id not needed

    a = files.get_file(conn, ids["f_a"].bytes)
    assert a["directory"] == "Set A/Sub" and a["name"] == "a.jpg" and a["size"] == 100
    assert a["mtime"] == 0 and a["state"] == "fingerprinted" and a["sha1"] == b"\x11" * 20
    d = files.get_file(conn, ids["f_d"].bytes)
    assert d["name"] == "d.mp4" and d["state"] == "unreadable" and d["size"] == 0

    [p] = pairs.pairs_of_file(conn, ids["f_a"].bytes)
    assert p["source"] == "sha1" and p["judgment"] == "related_keep"
    assert p["judgment_origin"] == "import_v1"
    assert groups.dirty_count(conn) == 2
    queued = jobs_db.list_jobs(conn, state="queued")
    assert sorted(j["type"] for j in queued) == ["regroup", "scan", "scan"]
    assert all(j["priority"] == 100 for j in queued if j["type"] == "scan")
    assert IO_HANDLERS["import_v1"] is run_import_v1


def test_refuses_non_empty_profile_and_missing_mapping_without_writing(world):
    reg, conn, tmp_path, ids = world
    only_archive = {"Archive": str(tmp_path / "media" / "images")}
    with pytest.raises(ImportError_, match="Inbox"):
        run_import_v1(ctx_for(reg, params_for(tmp_path, folder_paths=only_archive)))
    assert folders.list_folders(conn) == []
    bad_path = {**only_archive, "Inbox": str(tmp_path / "nope")}
    with pytest.raises(ImportError_, match="exist"):
        run_import_v1(ctx_for(reg, params_for(tmp_path, folder_paths=bad_path)))
    assert folders.list_folders(conn) == []
    run_import_v1(ctx_for(reg, params_for(tmp_path)))
    with pytest.raises(ImportError_, match="empty"):
        run_import_v1(ctx_for(reg, params_for(tmp_path)))
    with pytest.raises(ImportError_):
        run_import_v1(ctx_for(reg, params_for(tmp_path, db_path=str(tmp_path / "missing.db"))))


def test_scan_after_import_keeps_hashes_when_only_mtime_was_unknown(world):
    reg, conn, tmp_path, ids = world
    (tmp_path / "media" / "new" / "b.jpg").write_bytes(b"x" * 100)
    run_import_v1(ctx_for(reg, params_for(tmp_path)))
    inbox = next(r for r in folders.list_folders(conn) if r["name"] == "Inbox")
    result = run_scan(ctx_for(reg, {"folder_id": id_to_str(inbox["id"])}))
    b = files.get_file(conn, ids["f_b"].bytes)
    assert b["sha1"] == b"\x11" * 20 and b["state"] == "fingerprinted" and b["mtime"] > 0
    assert result["changed"] == 0 and result["deleted"] == 2  # c.png and clips/d.mp4 not on disk


def test_failed_import_leaves_the_profile_empty_and_retryable(world, monkeypatch):
    """Final review I7."""
    from visualstore.jobs.io import import_v1 as mod

    reg, conn, tmp_path, ids = world

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(mod, "_link_identical", boom)
    with pytest.raises(RuntimeError):
        run_import_v1(ctx_for(reg, params_for(tmp_path)))
    assert folders.list_folders(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM file").fetchone()[0] == 0
    assert not any(r["extension"] == "zip" for r in folders.list_extensions(conn))
    monkeypatch.undo()
    result = run_import_v1(ctx_for(reg, params_for(tmp_path)))
    assert result["files"] == 4 and result["files_skipped"] == 0


def test_scan_after_import_queues_vectors_for_imported_files(world):
    """Audit B2: imported files are fingerprinted with SHA-1 but have no vectors yet."""
    import json

    reg, conn, tmp_path, ids = world
    (tmp_path / "media" / "new" / "b.jpg").write_bytes(b"x" * 100)
    run_import_v1(ctx_for(reg, params_for(tmp_path)))
    inbox = next(r for r in folders.list_folders(conn) if r["name"] == "Inbox")
    result = run_scan(ctx_for(reg, {"folder_id": id_to_str(inbox["id"])}))
    queued = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "fingerprint"]
    queued_ids = {f["id"] for j in queued for f in json.loads(j["params"])["files"]}
    assert id_to_str(ids["f_b"].bytes) in queued_ids and result["vector_files_queued"] >= 1


def test_interrupted_import_is_undone_and_redone_on_the_retried_attempt(world):
    """Audit 2026-11-07: a restart mid-import must not leave a profile that can neither be
    imported into nor completed."""
    reg, conn, tmp_path, ids = world
    params = params_for(tmp_path)
    job = jobs_db.enqueue(conn, type="import_v1", kind="io", params=params)
    jobs_db.mark_running(conn, job)
    # First attempt: Folders committed, then the process died.
    v1 = sqlite3.connect(tmp_path / "v1.db")
    v1.row_factory = sqlite3.Row
    v1_folder = v1.execute("SELECT * FROM Folder").fetchone()
    folders.create_folder(
        conn,
        name=v1_folder["Name"],
        path=params["folder_paths"][v1_folder["Name"]],
        role="inbox",
        compare_scope="all",
        folder_id=gid(v1_folder["Id"]),
    )
    jobs_db.record_failure(conn, job, "interrupted by restart", backoff=False)
    jobs_db.mark_running(conn, job)  # the retried attempt
    result = run_import_v1(ctx_for(reg, params))
    assert result["resumed"] == 1
    assert {f["name"] for f in folders.list_folders(conn)} == {"Archive", "Inbox"}
    assert result["files"] == 4 and result["folders"] == 2


def test_a_finished_import_is_not_undone_by_a_manual_rerun(world):
    reg, conn, tmp_path, ids = world
    run_import_v1(ctx_for(reg, params_for(tmp_path)))
    before = conn.execute("SELECT COUNT(*) FROM file").fetchone()[0]
    job = jobs_db.enqueue(conn, type="import_v1", kind="io", params=params_for(tmp_path))
    jobs_db.mark_running(conn, job)  # attempts = 0: a fresh job, not a retry
    with pytest.raises(ImportError_, match="empty"):
        run_import_v1(ctx_for(reg, params_for(tmp_path)))
    assert conn.execute("SELECT COUNT(*) FROM file").fetchone()[0] == before
