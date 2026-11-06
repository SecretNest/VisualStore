"""Design §10 multi-profile isolation: two profiles, the same Folder name on the same media
directory; scan, actions and backup in one leave the other untouched."""

import hashlib

from tests.fixtures.models import install_fake_model
from visualstore.db import actions, files, folders
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.ids import id_to_str
from visualstore.jobs.io.actions import run_apply_action
from visualstore.jobs.io.backup import run_backup
from visualstore.jobs.io.scan import run_scan
from visualstore.jobs.scheduler import IoContext
from visualstore.profiles import ProfileRegistry


def ctx(reg, profile, params, tmp_path):
    conn = reg.conn(profile)
    return IoContext(
        profile=profile,
        paths=reg.paths(profile),
        backup_dir=tmp_path / "bk",
        global_db_path=reg.global_db_path,
        settings=settings_db.all(conn),
        params=params,
        report=lambda p: None,
        model_dir=reg.data_dir / "models",
    )


def test_two_profiles_sharing_a_media_directory_do_not_see_each_other(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    install_fake_model(tmp_path / "data" / "models")
    reg.open()
    reg.create("a")
    reg.create("b")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "x.jpg").write_bytes(b"xx")
    (lib / "y.jpg").write_bytes(b"yy")
    fa = folders.create_folder(
        reg.conn("a"), name="Lib", path=str(lib), role="inbox", compare_scope="all"
    )
    fb = folders.create_folder(
        reg.conn("b"), name="Lib", path=str(lib), role="archive", compare_scope="all"
    )
    run_scan(ctx(reg, "a", {"folder_id": id_to_str(fa)}, tmp_path))
    assert len(files.list_files_in_folder(reg.conn("a"), fa)) == 2
    assert files.list_files_in_folder(reg.conn("b"), fb) == []  # b has not scanned
    assert jobs_db.list_jobs(reg.conn("b")) == []
    run_scan(ctx(reg, "b", {"folder_id": id_to_str(fb)}, tmp_path))
    ids_a = {bytes(r["id"]) for r in files.list_files_in_folder(reg.conn("a"), fa)}
    ids_b = {bytes(r["id"]) for r in files.list_files_in_folder(reg.conn("b"), fb)}
    assert ids_a and ids_b and ids_a.isdisjoint(ids_b)
    # trashing in a moves the file on disk; b's row still exists until b rescans
    xa = files.find_file(reg.conn("a"), fa, "", "x.jpg")
    files.set_fingerprint_result(
        reg.conn("a"), bytes(xa["id"]), sha1=hashlib.sha1(b"xx").digest(), state="fingerprinted"
    )
    result = run_apply_action(ctx(reg, "a", {"trash": [id_to_str(bytes(xa["id"]))]}, tmp_path))
    assert result["trashed"] == 1 and actions.list_trash(reg.conn("b")) == []
    assert files.find_file(reg.conn("b"), fb, "", "x.jpg") is not None
    assert files.find_file(reg.conn("a"), fa, "", "x.jpg") is None
    # backup of a writes only under a's directory
    run_backup(ctx(reg, "a", {"targets": ["metadata"]}, tmp_path))
    assert (tmp_path / "bk" / "a").is_dir() and not (tmp_path / "bk" / "b").exists()
    reg.close()
