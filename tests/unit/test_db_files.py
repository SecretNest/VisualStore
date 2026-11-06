import sqlite3

import pytest

from visualstore.db import files, folders
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir
from visualstore.ids import id_to_str


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "p.db")
    migrate(c, migrations_dir("profile"))
    return c


@pytest.fixture
def folder(conn, tmp_path):
    (tmp_path / "lib").mkdir()
    return folders.create_folder(
        conn, name="Lib", path=str(tmp_path / "lib"), role="inbox", compare_scope="all"
    )


def test_insert_find_and_unique_nocase(conn, folder):
    ext = folders.extension_map(conn)["jpg"]["id"]
    fid = files.insert_file(
        conn, folder_id=folder, directory="A/B", name="Pic.JPG", extension_id=ext, size=10, mtime=5
    )
    assert files.find_file(conn, folder, "a/b", "pic.jpg")["id"] == fid
    with pytest.raises(sqlite3.IntegrityError):
        files.insert_file(
            conn,
            folder_id=folder,
            directory="a/B",
            name="PIC.jpg",
            extension_id=ext,
            size=1,
            mtime=1,
        )
    d = files.file_to_dict(files.get_file(conn, fid), folder_path="/media/lib")
    assert d["relative_path"] == "A/B/Pic.JPG" and d["absolute_path"] == "/media/lib/A/B/Pic.JPG"
    root = files.insert_file(
        conn, folder_id=folder, directory="", name="r.jpg", extension_id=ext, size=1, mtime=1
    )
    assert files.file_to_dict(files.get_file(conn, root), "/m")["relative_path"] == "r.jpg"


def test_state_transitions_and_sha1_bucket(conn, folder):
    ext = folders.extension_map(conn)["png"]["id"]
    a = files.insert_file(
        conn, folder_id=folder, directory="", name="a.png", extension_id=ext, size=3, mtime=1
    )
    b = files.insert_file(
        conn, folder_id=folder, directory="", name="b.png", extension_id=ext, size=3, mtime=1
    )
    files.set_fingerprint_result(conn, a, sha1=b"\x01" * 20, state="fingerprinted")
    files.set_fingerprint_result(conn, b, sha1=b"\x01" * 20, state="fingerprinted")
    assert {r["id"] for r in files.files_by_sha1(conn, b"\x01" * 20)} == {a, b}
    files.reset_for_recompute(conn, a)
    row = files.get_file(conn, a)
    assert row["state"] == "new" and row["sha1"] is None
    files.set_missing(conn, [b])
    assert files.get_file(conn, b)["state"] == "missing"
    files.update_path(conn, b, "moved", "b2.png")
    assert files.find_file(conn, folder, "moved", "b2.png")["id"] == b
    files.delete_files(conn, [a, b])
    assert files.list_files_in_folder(conn, folder) == []


def test_count_by_folder_and_state(conn, folder):
    ext = folders.extension_map(conn)["jpg"]["id"]
    files.insert_file(
        conn, folder_id=folder, directory="", name="1.jpg", extension_id=ext, size=1, mtime=1
    )
    f2 = files.insert_file(
        conn, folder_id=folder, directory="", name="2.jpg", extension_id=ext, size=1, mtime=1
    )
    files.set_fingerprint_result(conn, f2, sha1=b"\x02" * 20, state="fingerprinted")
    counts = files.count_by_folder_and_state(conn)
    assert counts[id_to_str(folder)] == {"new": 1, "fingerprinted": 1}


def test_deleting_a_folder_cascades_to_files(conn, folder):
    ext = folders.extension_map(conn)["jpg"]["id"]
    files.insert_file(
        conn, folder_id=folder, directory="", name="1.jpg", extension_id=ext, size=1, mtime=1
    )
    folders.delete_folder(conn, folder)
    assert conn.execute("SELECT COUNT(*) FROM file").fetchone()[0] == 0
