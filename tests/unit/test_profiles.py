import pytest

from visualstore.db import settings
from visualstore.profiles import ProfileError, ProfileNotFound, ProfileRegistry


@pytest.fixture
def registry(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    yield reg
    reg.close()


def test_open_creates_data_dir_and_global_db(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    assert (tmp_path / "data" / "global.db").is_file()
    assert (tmp_path / "data" / "profiles").is_dir()
    assert reg.list() == []
    reg.close()


def test_create_profile_builds_directory_and_databases(registry):
    info = registry.create("cg", "CG library")
    assert info.name == "cg" and info.display_name == "CG library"
    paths = registry.paths("cg")
    assert paths.metadata_db.is_file() and paths.vectors_db.is_file()
    assert paths.thumbs.is_dir()
    assert paths.root == registry.data_dir / "profiles" / "cg"
    assert [p.name for p in registry.list()] == ["cg"]
    assert registry.exists("cg") and not registry.exists("other")
    # the profile database is migrated and usable
    assert settings.get(registry.conn("cg"), "backup.metadata.keep") == 14


def test_empty_display_name_falls_back_to_name(registry):
    assert registry.create("photos").display_name == "photos"


@pytest.mark.parametrize(
    "bad", ["", "CG", "a b", "a/b", "../x", "..", "-a", "a-", "a--b", "é", "cg\n", "cg\x00"]
)
def test_invalid_names_are_rejected_before_touching_disk(registry, bad):
    """Review Focus 1: a name is a directory and a URL segment; nothing may escape /profiles."""
    with pytest.raises(ProfileError):
        registry.create(bad)
    with pytest.raises(ProfileError):
        registry.paths(bad)
    assert list((registry.data_dir / "profiles").iterdir()) == []


def test_duplicate_name_is_rejected(registry):
    registry.create("cg")
    with pytest.raises(ProfileError):
        registry.create("cg")


def test_unknown_profile_raises_not_found(registry):
    with pytest.raises(ProfileNotFound):
        registry.get("ghost")
    with pytest.raises(ProfileNotFound):
        registry.conn("ghost")
    with pytest.raises(ProfileNotFound):
        registry.rename("ghost", "x")


def test_rename_changes_display_name_only(registry):
    registry.create("cg", "old")
    registry.rename("cg", "new")
    assert registry.get("cg").display_name == "new"
    assert registry.get("cg").name == "cg"


def test_delete_requires_matching_confirmation_and_removes_directory(registry):
    registry.create("cg")
    root = registry.paths("cg").root
    with pytest.raises(ProfileError):
        registry.delete("cg", confirm_name="c")
    assert root.is_dir() and registry.exists("cg")
    registry.delete("cg", confirm_name="cg")
    assert not root.exists()
    assert not registry.exists("cg")
    with pytest.raises(ProfileNotFound):
        registry.delete("cg", confirm_name="cg")


def test_profiles_and_secret_survive_reopen(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a", "A")
    reg.create("b", "B")
    key1 = reg.secret("session_key")
    reg.close()

    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    assert [p.name for p in reg.list()] == ["a", "b"]
    assert reg.secret("session_key") == key1
    assert len(key1) == 32
    assert reg.conn("b").execute("SELECT 1").fetchone()[0] == 1
    reg.close()


def test_two_profiles_are_isolated(registry):
    registry.create("a")
    registry.create("b")
    settings.set(registry.conn("a"), "backup.metadata.keep", 99)
    assert settings.get(registry.conn("b"), "backup.metadata.keep") == 14
    registry.delete("a", confirm_name="a")
    assert settings.get(registry.conn("b"), "backup.metadata.keep") == 14
    assert registry.paths("b").metadata_db.is_file()


def test_usage_bytes_counts_files_under_root(registry):
    registry.create("a")
    paths = registry.paths("a")
    (paths.thumbs / "x.webp").write_bytes(b"0" * 1000)
    assert registry.usage_bytes("a") >= 1000


def test_failed_profile_creation_leaves_no_row_and_no_directory(registry, monkeypatch):
    import visualstore.profiles as profiles_module

    def explode(conn, directory):
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(profiles_module, "migrate", explode)
    with pytest.raises(RuntimeError):
        registry.create("broken")
    assert not registry.exists("broken") and not (registry.profiles_dir / "broken").exists()


def test_usage_bytes_is_cached_briefly_and_invalidated_on_delete(registry):
    registry.create("u")
    first = registry.usage_bytes("u")
    (registry.paths("u").root / "extra.bin").write_bytes(b"x" * 4096)
    assert registry.usage_bytes("u") == first  # cached
    registry.invalidate_usage("u")
    assert registry.usage_bytes("u") >= first + 4096


def test_delete_is_refused_while_the_profile_has_running_jobs(registry):
    from visualstore.db import jobs as jobs_db

    registry.create("busy")
    conn = registry.conn("busy")
    job = jobs_db.enqueue(conn, type="ping", kind="compute")
    jobs_db.mark_running(conn, job)
    with pytest.raises(ProfileError, match="running"):
        registry.delete("busy", confirm_name="busy")
    assert registry.exists("busy")
