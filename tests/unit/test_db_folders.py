import pytest

from visualstore.db import folders
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "p.db")
    migrate(c, migrations_dir("profile"))
    return c


@pytest.fixture
def media(tmp_path):
    root = tmp_path / "media"
    for sub in ("images", "images/sub", "photos", "inbox"):
        (root / sub).mkdir(parents=True)
    return root


def test_create_and_list_folder(conn, media):
    fid = folders.create_folder(
        conn,
        name="Images",
        path=str(media / "images"),
        role="archive",
        compare_scope="other_directories",
    )
    [row] = folders.list_folders(conn)
    assert row["id"] == fid and row["role"] == "archive" and row["enabled"] == 1
    d = folders.folder_to_dict(row)
    assert d["path"] == str(media / "images") and d["compare_scope"] == "other_directories"


@pytest.mark.parametrize("bad_role,bad_scope", [("sealed", "all"), ("archive", "everything")])
def test_invalid_enums_are_rejected(conn, media, bad_role, bad_scope):
    with pytest.raises(folders.FolderError):
        folders.create_folder(
            conn, name="x", path=str(media / "images"), role=bad_role, compare_scope=bad_scope
        )


def test_path_must_be_an_existing_absolute_directory(conn, media):
    with pytest.raises(folders.FolderError, match="absolute"):
        folders.create_folder(conn, name="rel", path="images", role="inbox", compare_scope="all")
    with pytest.raises(folders.FolderError, match="exist"):
        folders.create_folder(
            conn, name="ghost", path=str(media / "nope"), role="inbox", compare_scope="all"
        )


def test_folders_must_not_nest(conn, media):
    """Review Focus 1: parent, child, equal, and trailing-slash variants are all rejected."""
    folders.create_folder(
        conn, name="Images", path=str(media / "images"), role="archive", compare_scope="all"
    )
    for path, label in [
        (str(media / "images" / "sub"), "child"),
        (str(media), "parent"),
        (str(media / "images"), "same"),
        (str(media / "images") + "/", "trailing slash"),
    ]:
        with pytest.raises(folders.FolderError, match="nest|already"):
            folders.create_folder(conn, name=label, path=path, role="inbox", compare_scope="all")
    folders.create_folder(
        conn, name="Photos", path=str(media / "photos"), role="archive", compare_scope="all"
    )
    assert len(folders.list_folders(conn)) == 2


def test_name_is_unique_case_insensitively(conn, media):
    folders.create_folder(
        conn, name="Images", path=str(media / "images"), role="archive", compare_scope="all"
    )
    with pytest.raises(folders.FolderError, match="name"):
        folders.create_folder(
            conn, name="IMAGES", path=str(media / "photos"), role="archive", compare_scope="all"
        )


def test_update_and_delete(conn, media):
    fid = folders.create_folder(
        conn, name="Inbox", path=str(media / "inbox"), role="inbox", compare_scope="all"
    )
    folders.update_folder(conn, fid, role="archive", priority=5, enabled=False, name="Old inbox")
    row = folders.get_folder(conn, fid)
    assert (row["role"], row["priority"], row["enabled"], row["name"]) == (
        "archive",
        5,
        0,
        "Old inbox",
    )
    with pytest.raises(folders.FolderError):
        folders.update_folder(conn, fid, path="/elsewhere")
    folders.delete_folder(conn, fid)
    assert folders.get_folder(conn, fid) is None


def test_ignored_directories(conn, media):
    fid = folders.create_folder(
        conn, name="Images", path=str(media / "images"), role="archive", compare_scope="all"
    )
    iid = folders.add_ignored(conn, fid, "Thumbs/", recursive=False)
    [row] = folders.list_ignored(conn, fid)
    assert row["directory"] == "Thumbs" and row["recursive"] == 0  # normalised, no slashes
    with pytest.raises(folders.FolderError):
        folders.add_ignored(conn, fid, "thumbs")  # NOCASE duplicate
    folders.remove_ignored(conn, iid)
    assert folders.list_ignored(conn, fid) == []
    folders.add_ignored(conn, fid, "a/b")
    folders.delete_folder(conn, fid)
    assert conn.execute("SELECT COUNT(*) FROM ignored_directory").fetchone()[0] == 0  # cascade


def test_default_extensions_are_seeded_and_editable(conn):
    ext = folders.extension_map(conn)
    assert ext["jpg"]["media_type"] == "image" and ext["gif"]["media_type"] == "animated"
    assert ext["mp4"]["media_type"] == "video" and "zip" not in ext
    zid = folders.upsert_extension(conn, "ZIP", "other")
    assert folders.extension_map(conn)["zip"]["id"] == zid
    folders.set_extension_enabled(conn, zid, False)
    assert "zip" not in folders.extension_map(conn)
    assert any(r["extension"] == "zip" and r["enabled"] == 0 for r in folders.list_extensions(conn))
    with pytest.raises(folders.FolderError):
        folders.upsert_extension(conn, "psd", "layered")


def test_deleting_a_folder_marks_surviving_group_members_dirty(conn, media):
    """Final review I4: the co-members outside the Folder must be regrouped."""
    from visualstore.db import files, groups
    from visualstore.db.groups import NewGroup

    arch = folders.create_folder(
        conn, name="A", path=str(media / "images"), role="archive", compare_scope="all"
    )
    inbox = folders.create_folder(
        conn, name="I", path=str(media / "inbox"), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]
    a = files.insert_file(
        conn, folder_id=arch, directory="", name="a.jpg", extension_id=ext, size=1, mtime=1
    )
    b = files.insert_file(
        conn, folder_id=inbox, directory="", name="b.jpg", extension_id=ext, size=1, mtime=1
    )
    groups.replace_groups(conn, [], [NewGroup(frozenset({a, b}), 0.0, True, "open", {})])
    folders.delete_folder(conn, inbox)
    assert groups.pop_dirty(conn) == [a]
