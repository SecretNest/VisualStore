import hashlib

import pytest

from tests.fixtures.v1db import build
from tests.integration.conftest import login
from tests.integration.test_api_profile_scoped import wait_for
from visualstore.db import files, folders
from visualstore.ids import id_to_str


@pytest.fixture
def seeded(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    conn = client.app.state.vs.registry.conn("a")
    inbox, arch = tmp_path / "inbox", tmp_path / "arch"
    (inbox / "set").mkdir(parents=True)
    arch.mkdir()
    fi = folders.create_folder(
        conn, name="Inbox", path=str(inbox), role="inbox", compare_scope="all"
    )
    fa = folders.create_folder(
        conn, name="Arch", path=str(arch), role="archive", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]
    (inbox / "set" / "x.jpg").write_bytes(b"x")
    x = files.insert_file(
        conn, folder_id=fi, directory="set", name="x.jpg", extension_id=ext, size=1, mtime=1
    )
    files.set_fingerprint_result(conn, x, sha1=hashlib.sha1(b"x").digest(), state="fingerprinted")
    return {"conn": conn, "inbox": fi, "arch": fa, "inbox_dir": inbox, "arch_dir": arch, "x": x}


def test_trash_listing_and_restore(client, seeded):
    s = seeded
    job = client.post(
        "/api/p/a/jobs", json={"type": "apply_action", "params": {"trash": [id_to_str(s["x"])]}}
    ).json()
    assert wait_for(client, "a", job["id"])["state"] == "done"
    [entry] = client.get("/api/p/a/trash").json()
    assert entry["before"]["relative_path"] == "set/x.jpg" and entry["type"] == "trash"
    restore = client.post("/api/p/a/trash/restore", json={"action_ids": [entry["id"]]})
    assert restore.status_code == 201
    assert wait_for(client, "a", restore.json()["id"])["progress"]["result"]["restored"] == 1
    assert (s["inbox_dir"] / "set" / "x.jpg").read_bytes() == b"x"
    assert client.get("/api/p/a/trash").json() == []


def test_archive_move_endpoint(client, seeded):
    s = seeded
    body = {
        "source_folder_id": id_to_str(s["inbox"]),
        "target_folder_id": id_to_str(s["arch"]),
        "directories": ["set"],
    }
    resp = client.post("/api/p/a/archive-move", json=body)
    assert resp.status_code == 201, resp.text
    job = wait_for(client, "a", resp.json()["id"])
    assert job["state"] == "done" and job["progress"]["result"]["moved"] == 1
    assert (s["arch_dir"] / "set" / "x.jpg").exists()
    ghost = {
        "source_folder_id": "00000000-0000-0000-0000-000000000000",
        "target_folder_id": id_to_str(s["arch"]),
    }
    assert client.post("/api/p/a/archive-move", json=ghost).status_code == 404


def test_import_v1_endpoint(client, tmp_path):
    login(client)
    assert client.post("/api/profiles", json={"name": "fresh"}).status_code == 201
    (tmp_path / "m" / "images").mkdir(parents=True)
    (tmp_path / "m" / "new").mkdir()
    build(tmp_path / "v1.db")
    body = {
        "db_path": str(tmp_path / "v1.db"),
        "folder_paths": {
            "Archive": str(tmp_path / "m" / "images"),
            "Inbox": str(tmp_path / "m" / "new"),
        },
    }
    resp = client.post("/api/p/fresh/import-v1", json=body)
    assert resp.status_code == 201, resp.text
    job = wait_for(client, "fresh", resp.json()["id"])
    assert job["state"] == "done", job
    assert job["progress"]["result"]["files"] == 4
    assert len(client.get("/api/p/fresh/folders").json()) == 2
    assert client.post("/api/p/fresh/import-v1", json=body).status_code == 400
