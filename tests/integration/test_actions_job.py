import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from visualstore.db import actions, files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.db.groups import NewGroup
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.actions import (
    ActionError,
    move_file,
    remove_empty_parents,
    run_apply_action,
    run_purge_trash,
    trash_path,
)
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    lib = tmp_path / "lib"
    (lib / "sub").mkdir(parents=True)
    folder = folders.create_folder(
        conn, name="Lib", path=str(lib), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(rel, content):
        directory, _, name = rel.rpartition("/")
        p = lib / rel
        p.write_bytes(content)
        st = p.stat()
        fid = files.insert_file(
            conn,
            folder_id=folder,
            directory=directory,
            name=name,
            extension_id=ext,
            size=st.st_size,
            mtime=st.st_mtime_ns,
        )
        files.set_fingerprint_result(
            conn, fid, sha1=hashlib.sha1(content).digest(), state="fingerprinted"
        )
        return fid

    yield reg, conn, folder, lib, add
    reg.close()


def ctx_for(reg, params, **overrides):
    conn = reg.conn("a")
    settings = settings_db.all(conn)
    settings.update(overrides)
    return IoContext(
        profile="a",
        paths=reg.paths("a"),
        backup_dir=reg.data_dir / "bk",
        global_db_path=reg.global_db_path,
        settings=settings,
        params=params,
        report=lambda p: None,
        model_dir=reg.data_dir / "models",
    )


def test_trash_moves_file_keeps_relative_path_and_records_action(world):
    reg, conn, folder, lib, add = world
    a = add("sub/a.jpg", b"aaa")
    b = add("sub/b.jpg", b"aaa")
    pairs.upsert_sha1_pair(conn, a, b)
    [gid] = groups.replace_groups(
        conn, [], [NewGroup(frozenset({a, b}), 0.0, True, "open", {a: "keep", b: "delete"})]
    )

    result = run_apply_action(ctx_for(reg, {"group_id": id_to_str(gid), "trash": [id_to_str(b)]}))
    assert result["trashed"] == 1 and result["skipped"] == []
    month = datetime.now(UTC).strftime("%Y-%m")
    moved = lib / ".visualstore-trash" / month / "sub" / "b.jpg"
    assert moved.read_bytes() == b"aaa" and not (lib / "sub" / "b.jpg").exists()
    assert files.get_file(conn, b) is None and pairs.pairs_of_file(conn, a) == []
    [act] = actions.list_trash(conn)
    d = actions.action_to_dict(act)
    assert d["before"]["relative_path"] == "sub/b.jpg" and d["after"]["path"] == str(moved)
    assert d["before"]["sha1"] == hashlib.sha1(b"aaa").hexdigest()
    assert groups.get_group(conn, gid)["status"] == "resolved"
    assert set(groups.pop_dirty(conn)) == {a}
    assert IO_HANDLERS["apply_action"] is run_apply_action
    assert IO_HANDLERS["purge_trash"] is run_purge_trash


def test_restore_brings_the_file_back_with_its_id_and_pairs(world):
    reg, conn, folder, lib, add = world
    a = add("sub/a.jpg", b"aaa")
    b = add("sub/b.jpg", b"aaa")
    pairs.upsert_sha1_pair(conn, a, b)
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(b)]}))
    [act] = actions.list_trash(conn)
    result = run_apply_action(ctx_for(reg, {"restore": [id_to_str(act["id"])]}))
    assert result["restored"] == 1
    row = files.find_file(conn, folder, "sub", "b.jpg")
    assert row["id"] == b and row["state"] == "fingerprinted"
    assert row["sha1"] == hashlib.sha1(b"aaa").digest()
    assert (lib / "sub" / "b.jpg").read_bytes() == b"aaa"
    assert len(pairs.pairs_of_file(conn, a)) == 1  # sha1 pair re-linked
    assert actions.list_trash(conn) == [] and actions.get(conn, act["id"])["superseded_at"]
    assert any(r["type"] == "restore" for r in conn.execute("SELECT type FROM action"))

    run_apply_action(ctx_for(reg, {"trash": [id_to_str(b)]}))  # trash again
    [act2] = actions.list_trash(conn)
    (lib / "sub" / "b.jpg").write_bytes(b"another")  # target taken: restore must refuse
    result = run_apply_action(ctx_for(reg, {"restore": [id_to_str(act2["id"])]}))
    assert result["restored"] == 0 and result["skipped"][0]["reason"] == "exists"
    assert actions.list_trash(conn) != []


def test_trash_name_collision_gets_a_suffix_and_disk_failure_leaves_db_intact(world, monkeypatch):
    """Review Focus 3."""
    reg, conn, folder, lib, add = world
    a = add("x.jpg", b"first")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(a)]}))
    b = add("x.jpg", b"second")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(b)]}))
    month = datetime.now(UTC).strftime("%Y-%m")
    names = sorted(p.name for p in (lib / ".visualstore-trash" / month).iterdir())
    assert names == ["x (1).jpg", "x.jpg"]

    c = add("y.jpg", b"third")

    def boom(src, dst):
        raise PermissionError("read-only")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(ActionError):
        run_apply_action(ctx_for(reg, {"trash": [id_to_str(c)]}))
    assert files.get_file(conn, c) is not None and (lib / "y.jpg").exists()
    assert len(actions.list_trash(conn)) == 2


def test_move_file_cross_device_falls_back_to_copy(world, monkeypatch, tmp_path):
    reg, conn, folder, lib, add = world
    src = lib / "cd.jpg"
    src.write_bytes(b"payload")

    def exdev(a, b):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "replace", exdev)
    dest = move_file(src, tmp_path / "elsewhere" / "cd.jpg")
    assert dest.read_bytes() == b"payload" and not src.exists()


def test_permanent_delete_when_trash_disabled(world):
    reg, conn, folder, lib, add = world
    a = add("gone.jpg", b"g")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(a)]}, **{"trash.enabled": False}))
    assert not (lib / "gone.jpg").exists() and not (lib / ".visualstore-trash").exists()
    [act] = conn.execute("SELECT * FROM action").fetchall()
    assert actions.action_to_dict(act)["after"] == {"path": None, "permanent": True}
    assert actions.list_trash(conn) == []  # permanent deletions are not restorable


def test_purge_removes_expired_trash_and_empty_dirs(world):
    reg, conn, folder, lib, add = world
    a = add("sub/old.jpg", b"o")
    b = add("sub/fresh.jpg", b"f")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(a), id_to_str(b)]}))
    old_act = next(
        r
        for r in actions.list_trash(conn)
        if actions.action_to_dict(r)["before"]["name"] == "old.jpg"
    )
    forty_days_ago = (datetime.now(UTC) - timedelta(days=40)).isoformat(timespec="seconds")
    with conn:
        conn.execute("UPDATE action SET at = ? WHERE id = ?", (forty_days_ago, old_act["id"]))
    result = run_purge_trash(ctx_for(reg, {}))
    assert result == {"purged": 1, "missing": 0}
    remaining = [actions.action_to_dict(r)["before"]["name"] for r in actions.list_trash(conn)]
    assert remaining == ["fresh.jpg"]
    assert not any(p.name == "old.jpg" for p in (lib / ".visualstore-trash").rglob("*"))
    assert not (lib / "sub").exists()  # emptied source directory cleaned up after trashing


def test_remove_empty_parents_stops_at_root_and_trash(tmp_path):
    root = tmp_path / "r"
    deep = root / "a" / "b" / "c"
    deep.mkdir(parents=True)
    remove_empty_parents(deep, stop_at=root)
    assert root.exists() and not (root / "a").exists()
    trash = root / ".visualstore-trash" / "2026-11" / "x"
    trash.mkdir(parents=True)
    remove_empty_parents(trash, stop_at=root)
    assert (root / ".visualstore-trash").exists()
    assert not (root / ".visualstore-trash" / "2026-11").exists()


def test_trash_path_layout():
    p = trash_path("/media/lib", "a/b", "c.jpg", datetime(2026, 11, 6, tzinfo=UTC))
    assert p == Path("/media/lib/.visualstore-trash/2026-11/a/b/c.jpg")


def test_trash_skips_files_changed_on_disk_or_no_longer_marked_delete(world):
    """Final review I3."""
    reg, conn, folder, lib, add = world
    a = add("a.jpg", b"aaa")
    b = add("b.jpg", b"aaa")
    [gid] = groups.replace_groups(
        conn, [], [NewGroup(frozenset({a, b}), 0.0, True, "open", {a: "keep", b: "delete"})]
    )
    (lib / "b.jpg").write_bytes(b"replaced content")  # changed after the decision
    result = run_apply_action(ctx_for(reg, {"group_id": id_to_str(gid), "trash": [id_to_str(b)]}))
    assert result["trashed"] == 0 and result["skipped"][0]["reason"] == "changed on disk"
    assert (lib / "b.jpg").exists() and groups.get_group(conn, gid)["status"] == "open"

    c = add("c.jpg", b"ccc")
    d = add("d.jpg", b"ccc")
    [gid2] = groups.replace_groups(
        conn, [], [NewGroup(frozenset({c, d}), 0.0, True, "open", {c: "keep", d: "keep"})]
    )
    result = run_apply_action(ctx_for(reg, {"group_id": id_to_str(gid2), "trash": [id_to_str(d)]}))
    assert result["trashed"] == 0 and result["skipped"][0]["reason"] == "decision changed"
    assert (lib / "d.jpg").exists()


def test_restored_image_is_fingerprinted_again_so_it_gets_vectors(world):
    """Final review, Important 4: trashing drops the vectors; restoring must bring them back."""
    reg, conn, folder, lib, add = world
    a = add("sub/a.jpg", b"aaa")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(a)]}))
    [act] = actions.list_trash(conn)
    run_apply_action(ctx_for(reg, {"restore": [id_to_str(act["id"])]}))
    queued = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "fingerprint"]
    assert len(queued) == 1
    params = json.loads(queued[0]["params"])
    assert [e["id"] for e in params["files"]] == [id_to_str(a)]
    assert params["files"][0]["path"] == str(lib / "sub" / "a.jpg")
    assert params["model"]["id"] == settings_db.get(conn, "model.id")


def test_rename_and_move_keep_the_row_id_hash_and_pairs(world):
    """Review Focus 3."""
    reg, conn, folder, lib, add = world
    a, b = add("sub/a.jpg", b"aaa"), add("sub/b.jpg", b"bbb")
    pairs.upsert_vector_pair(conn, a, b, 0.2, "m")
    result = run_apply_action(
        ctx_for(reg, {"rename": [{"file_id": id_to_str(a), "name": "renamed.jpg"}]})
    )
    assert result["renamed"] == 1 and result["skipped"] == []
    row = files.get_file(conn, a)
    assert (
        row["directory"] == "sub"
        and row["name"] == "renamed.jpg"
        and row["sha1"] == hashlib.sha1(b"aaa").digest()
    )
    assert (lib / "sub" / "renamed.jpg").exists() and not (lib / "sub" / "a.jpg").exists()
    assert len(pairs.pairs_of_file(conn, a)) == 1
    assert any(r["type"] == "rename" for r in conn.execute("SELECT type FROM action"))

    other_root = lib.parent / "arch"
    other_root.mkdir()
    arch = folders.create_folder(
        conn, name="Arch", path=str(other_root), role="archive", compare_scope="all"
    )
    result = run_apply_action(
        ctx_for(
            reg,
            {"move": [{"file_id": id_to_str(a), "folder_id": id_to_str(arch), "directory": "x/y"}]},
        )
    )
    assert result["moved"] == 1 and result["skipped"] == []
    row = files.get_file(conn, a)
    assert row["folder_id"] == arch and row["directory"] == "x/y" and row["name"] == "renamed.jpg"
    assert (other_root / "x" / "y" / "renamed.jpg").read_bytes() == b"aaa"
    assert not (lib / "sub" / "renamed.jpg").exists() and (lib / "sub" / "b.jpg").exists()
    assert set(groups.pop_dirty(conn)) == {a}  # the role may have changed: regroup
    assert len(pairs.pairs_of_file(conn, a)) == 1


def test_rename_and_move_never_overwrite(world):
    """Review Focus 3."""
    reg, conn, folder, lib, add = world
    a = add("a.jpg", b"aaa")
    add("b.jpg", b"bbb")
    result = run_apply_action(
        ctx_for(reg, {"rename": [{"file_id": id_to_str(a), "name": "b.jpg"}]})
    )
    assert result["renamed"] == 0 and result["skipped"] == [
        {"id": id_to_str(a), "reason": "exists"}
    ]
    assert (lib / "a.jpg").read_bytes() == b"aaa" and (lib / "b.jpg").read_bytes() == b"bbb"
    (lib / "sub" / "a.jpg").write_bytes(b"on disk only")
    result = run_apply_action(
        ctx_for(
            reg,
            {
                "move": [
                    {"file_id": id_to_str(a), "folder_id": id_to_str(folder), "directory": "sub"}
                ]
            },
        )
    )
    assert result["skipped"] == [{"id": id_to_str(a), "reason": "exists"}]
    assert (lib / "a.jpg").read_bytes() == b"aaa"
    result = run_apply_action(
        ctx_for(reg, {"rename": [{"file_id": id_to_str(a), "name": "../x.jpg"}]})
    )
    assert result["skipped"] == [{"id": id_to_str(a), "reason": "bad name"}]
    result = run_apply_action(
        ctx_for(
            reg,
            {
                "move": [
                    {"file_id": id_to_str(a), "folder_id": id_to_str(b"\x00" * 16), "directory": ""}
                ]
            },
        )
    )
    assert result["skipped"] == [{"id": id_to_str(a), "reason": "no such folder"}]


def test_move_into_the_trash_directory_is_refused(world):
    """Final review I3."""
    reg, conn, folder, lib, add = world
    a = add("a.jpg", b"aaa")
    result = run_apply_action(
        ctx_for(
            reg,
            {
                "move": [
                    {
                        "file_id": id_to_str(a),
                        "folder_id": id_to_str(folder),
                        "directory": ".visualstore-trash/2026-11",
                    }
                ]
            },
        )
    )
    assert result["skipped"] == [{"id": id_to_str(a), "reason": "bad name"}]
    assert (lib / "a.jpg").exists()
    result = run_apply_action(
        ctx_for(reg, {"rename": [{"file_id": id_to_str(a), "name": ".visualstore-trash"}]})
    )
    assert result["skipped"] == [{"id": id_to_str(a), "reason": "bad name"}]


def test_restored_video_is_fingerprinted_again_too(world):
    """Audit B11: trashing drops frames of every media type."""
    reg, conn, folder, lib, add = world
    ext = folders.extension_map(conn)["mp4"]["id"]
    (lib / "clip.mp4").write_bytes(b"video bytes")
    st = (lib / "clip.mp4").stat()
    clip = files.insert_file(
        conn,
        folder_id=folder,
        directory="",
        name="clip.mp4",
        extension_id=ext,
        size=st.st_size,
        mtime=st.st_mtime_ns,
    )
    files.set_fingerprint_result(conn, clip, sha1=b"\x07" * 20, state="fingerprinted")
    run_apply_action(ctx_for(reg, {"trash": [id_to_str(clip)]}))
    [act] = actions.list_trash(conn)
    run_apply_action(ctx_for(reg, {"restore": [id_to_str(act["id"])]}))
    queued = [j for j in jobs_db.list_jobs(conn, state="queued") if j["type"] == "fingerprint"]
    assert len(queued) == 1 and json.loads(queued[0]["params"])["files"][0]["media_type"] == "video"


def test_same_folder_rename_marks_the_file_dirty_and_ignored_directories_are_refused(world):
    reg, conn, folder, lib, add = world
    a = add("sub/a.jpg", b"aaa")
    run_apply_action(ctx_for(reg, {"rename": [{"file_id": id_to_str(a), "name": "zzz.jpg"}]}))
    assert set(groups.pop_dirty(conn)) == {a}  # the inbox "first by path" rule depends on names
    folders.add_ignored(conn, folder, "Thumbs")
    result = run_apply_action(
        ctx_for(
            reg,
            {
                "move": [
                    {"file_id": id_to_str(a), "folder_id": id_to_str(folder), "directory": "Thumbs"}
                ]
            },
        )
    )
    assert result["skipped"] == [{"id": id_to_str(a), "reason": "ignored directory"}]
