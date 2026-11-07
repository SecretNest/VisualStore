import json

import numpy as np
import pytest

from tests.fixtures.models import install_fake_model
from visualstore.db import files, fingerprints, folders, groups, pairs
from visualstore.db import jobs as jobs_db
from visualstore.db import settings as settings_db
from visualstore.db.groups import NewGroup
from visualstore.ids import id_to_str
from visualstore.jobs.io import IO_HANDLERS
from visualstore.jobs.io.model_switch import run_model_switch
from visualstore.jobs.scheduler import IoContext
from visualstore.models.registry import ModelError
from visualstore.profiles import ProfileRegistry

OLD, NEW = "sscd-resnet50-512-v1", "dinov2-vits14-384-v1"


@pytest.fixture
def world(tmp_path):
    reg = ProfileRegistry(tmp_path / "data")
    reg.open()
    reg.create("a")
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    (tmp_path / "lib").mkdir()
    folder = folders.create_folder(
        conn, name="L", path=str(tmp_path / "lib"), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    install_fake_model(model_dir, NEW, dim=384)  # local-only override: file present, no hash check

    def add(name, media="jpg", with_new=False):
        fid = files.insert_file(
            conn,
            folder_id=folder,
            directory="",
            name=name,
            extension_id=ext[media]["id"],
            size=1,
            mtime=1,
        )
        files.set_fingerprint_result(conn, fid, sha1=bytes([len(name)]) * 20, state="fingerprinted")
        fingerprints.replace_frames(vconn, fid, OLD, [(0, 0, np.zeros(512, np.float16).tobytes())])
        if with_new:
            fingerprints.replace_frames(
                vconn, fid, NEW, [(0, 0, np.zeros(384, np.float16).tobytes())]
            )
        return fid

    yield reg, conn, vconn, model_dir, add
    reg.close()


def ctx_for(reg, model_dir, params):
    conn = reg.conn("a")
    return IoContext(
        profile="a",
        paths=reg.paths("a"),
        backup_dir=reg.data_dir / "bk",
        global_db_path=reg.global_db_path,
        settings=settings_db.all(conn),
        params=params,
        report=lambda p: None,
        model_dir=model_dir,
    )


def test_switch_queues_only_missing_vectors_and_keeps_judgments(world):
    """Review Focus 3."""
    reg, conn, vconn, model_dir, add = world
    a, b = add("a.jpg"), add("b.jpg", with_new=True)
    c = add("c.mp4", media="mp4")  # videos get frame vectors too (milestone 5)
    pairs.upsert_sha1_pair(conn, a, b)
    pairs.set_judgment(conn, a, b, "related_keep", "user")
    groups.replace_groups(
        conn, [], [NewGroup(frozenset({a, b}), 0.0, True, "resolved", {a: "keep", b: "keep"})]
    )
    result = run_model_switch(ctx_for(reg, model_dir, {"model_id": NEW}))
    assert (
        result["model_id"] == NEW
        and result["queued_files"] == 2
        and result["rebuild_queued"] is True
    )
    assert settings_db.get(conn, "model.id") == NEW
    queued = jobs_db.list_jobs(conn, state="queued")
    fps = [j for j in queued if j["type"] == "fingerprint"]
    assert len(fps) == 1
    params = json.loads(fps[0]["params"])
    assert [f["id"] for f in params["files"]] == [id_to_str(a), id_to_str(c)]
    assert params["model"]["id"] == NEW and params["files"][1]["media_type"] == "video"
    assert any(j["type"] == "rebuild_index" for j in queued)
    assert pairs.pairs_of_file(conn, a)[0]["judgment"] == "related_keep"
    assert groups.group_of_file(conn, a)["status"] == "resolved"
    assert fingerprints.vector_count(vconn, OLD) == 3  # old vectors kept (design §7.7)
    assert IO_HANDLERS["model_switch"] is run_model_switch


def test_unknown_or_unavailable_model_changes_nothing(world):
    reg, conn, vconn, model_dir, add = world
    add("a.jpg")
    with pytest.raises(ModelError):
        run_model_switch(ctx_for(reg, model_dir, {"model_id": "nope"}))
    (model_dir / f"{NEW}.onnx").unlink()
    with pytest.raises(ModelError, match="not downloadable"):
        run_model_switch(ctx_for(reg, model_dir, {"model_id": NEW}))
    assert settings_db.get(conn, "model.id") == OLD
    assert jobs_db.list_jobs(conn) == []


def test_switch_cancels_queued_jobs_of_the_old_model(world):
    """Final review, Important 3."""
    reg, conn, vconn, model_dir, add = world
    add("a.jpg")
    old_fp = jobs_db.enqueue(
        conn,
        type="fingerprint",
        kind="compute",
        priority=50,
        params={"files": [], "model": {"id": OLD}},
    )
    old_match = jobs_db.enqueue(
        conn,
        type="match",
        kind="compute",
        priority=50,
        params={"model": {"id": OLD}, "entries": []},
    )
    unrelated = jobs_db.enqueue(conn, type="backup", kind="io", priority=50)
    result = run_model_switch(ctx_for(reg, model_dir, {"model_id": NEW}))
    assert result["cancelled_jobs"] == 2
    assert jobs_db.get(conn, old_fp)["state"] == "cancelled"
    assert jobs_db.get(conn, old_match)["state"] == "cancelled"
    assert jobs_db.get(conn, unrelated)["state"] == "queued"


def test_switch_requeues_files_that_were_still_new(world):
    """Audit 2026-11-07: cancelling the old model's fingerprint jobs must not park `new` files
    until the next scan."""
    reg, conn, vconn, model_dir, add = world
    ext = folders.extension_map(conn)["jpg"]["id"]
    folder = bytes(folders.list_folders(conn)[0]["id"])
    fresh = files.insert_file(
        conn, folder_id=folder, directory="", name="fresh.jpg", extension_id=ext, size=1, mtime=1
    )
    jobs_db.enqueue(
        conn,
        type="fingerprint",
        kind="compute",
        priority=50,
        params={"files": [{"id": id_to_str(fresh)}], "model": {"id": OLD}},
    )
    run_model_switch(ctx_for(reg, model_dir, {"model_id": NEW}))
    queued = [
        json.loads(j["params"])
        for j in jobs_db.list_jobs(conn, state="queued")
        if j["type"] == "fingerprint"
    ]
    assert len(queued) == 1 and queued[0]["model"]["id"] == NEW
    assert [f["id"] for f in queued[0]["files"]] == [id_to_str(fresh)]
