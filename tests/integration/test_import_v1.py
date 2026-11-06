import pytest

from tests.fixtures.v1db import build
from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.import_v1 import ImportError_, run_import_v1
from visualstore.jobs.io.scan import run_scan
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
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
