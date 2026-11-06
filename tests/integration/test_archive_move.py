import hashlib

import pytest

from visualstore.db import files, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.db.groups import NewGroup
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.archive_move import run_archive_move
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn = reg.conn("a")
    inbox, arch = tmp_path / "inbox", tmp_path / "arch"
    (inbox / "set1").mkdir(parents=True)
    (inbox / "set2").mkdir()
    arch.mkdir()
    src = folders.create_folder(
        conn, name="Inbox", path=str(inbox), role="inbox", compare_scope="all"
    )
    dst = folders.create_folder(
        conn, name="Arch", path=str(arch), role="archive", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]

    def add(folder_id, root, rel, content):
        directory, _, name = rel.rpartition("/")
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        fid = files.insert_file(
            conn,
            folder_id=folder_id,
            directory=directory,
            name=name,
            extension_id=ext,
            size=len(content),
            mtime=1,
        )
        files.set_fingerprint_result(
            conn, fid, sha1=hashlib.sha1(content).digest(), state="fingerprinted"
        )
        return fid

    yield reg, conn, src, dst, inbox, arch, add
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


def test_moves_selected_directories_keeping_ids_and_pairs(world):
    reg, conn, src, dst, inbox, arch, add = world
    a = add(src, inbox, "set1/a.jpg", b"a")
    b = add(src, inbox, "set2/b.jpg", b"b")
    kept = add(dst, arch, "old/k.jpg", b"a")
    pairs.upsert_sha1_pair(conn, a, kept)
    params = {
        "source_folder_id": id_to_str(src),
        "target_folder_id": id_to_str(dst),
        "directories": ["set1"],
    }
    result = run_archive_move(ctx_for(reg, params))
    assert result == {"moved": 1, "conflicts": []}
    assert (arch / "set1" / "a.jpg").read_bytes() == b"a" and not (inbox / "set1").exists()
    row = files.get_file(conn, a)
    assert row["folder_id"] == dst and row["directory"] == "set1"
    assert row["sha1"] == hashlib.sha1(b"a").digest()
    assert len(pairs.pairs_of_file(conn, a)) == 1
    assert files.get_file(conn, b)["folder_id"] == src
    assert set(groups.pop_dirty(conn)) == {a}
    assert jobs_db.has_active(conn, "regroup")
    assert IO_HANDLERS["archive_move"] is run_archive_move


def test_conflicts_are_reported_and_left_in_place(world):
    reg, conn, src, dst, inbox, arch, add = world
    a = add(src, inbox, "set1/a.jpg", b"a")
    add(dst, arch, "set1/a.jpg", b"other")
    params = {"source_folder_id": id_to_str(src), "target_folder_id": id_to_str(dst)}
    result = run_archive_move(ctx_for(reg, params))
    assert result["moved"] == 0 and result["conflicts"] == ["set1/a.jpg"]
    assert (inbox / "set1" / "a.jpg").exists() and files.get_file(conn, a)["folder_id"] == src


def test_refuses_when_open_groups_remain_or_roles_are_wrong(world):
    reg, conn, src, dst, inbox, arch, add = world
    a = add(src, inbox, "set1/a.jpg", b"a")
    k = add(dst, arch, "k.jpg", b"a")
    pairs.upsert_sha1_pair(conn, a, k)
    groups.replace_groups(conn, [], [NewGroup(frozenset({a, k}), 0.0, True, "open", {})])
    params = {"source_folder_id": id_to_str(src), "target_folder_id": id_to_str(dst)}
    result = run_archive_move(ctx_for(reg, params))
    assert result["skipped"].startswith("1 open group")
    assert (inbox / "set1" / "a.jpg").exists()
    swapped = run_archive_move(
        ctx_for(reg, {"source_folder_id": id_to_str(dst), "target_folder_id": id_to_str(src)})
    )
    assert "inbox" in swapped["skipped"]
