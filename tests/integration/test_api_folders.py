import pytest

from tests.integration.conftest import login


@pytest.fixture
def media(tmp_path):
    for d in ("images", "images/sub", "new"):
        (tmp_path / "media" / d).mkdir(parents=True)
    return tmp_path / "media"


def setup(client):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201


def folder_body(name, path, role="inbox", scope="all"):
    return {"name": name, "path": str(path), "role": role, "compare_scope": scope}


def test_folder_crud_ignored_and_validation(client, media):
    """Review Focus 1 at the HTTP boundary."""
    setup(client)
    body = folder_body("Images", media / "images", "archive", "other_directories")
    created = client.post("/api/p/a/folders", json=body)
    assert created.status_code == 201, created.text
    folder = created.json()
    assert folder["role"] == "archive" and folder["ignored"] == [] and folder["enabled"] is True

    nested = client.post("/api/p/a/folders", json=folder_body("Sub", media / "images" / "sub"))
    assert nested.status_code == 400 and "nest" in nested.json()["detail"]
    assert client.post("/api/p/a/folders", json=folder_body("Rel", "relative")).status_code == 400
    bad_role = folder_body("Bad", media / "new", role="sealed")
    assert client.post("/api/p/a/folders", json=bad_role).status_code == 400

    with_ignored = client.post(
        f"/api/p/a/folders/{folder['id']}/ignored",
        json={"directory": "Thumbs/", "recursive": False},
    )
    assert with_ignored.status_code == 201
    [ign] = with_ignored.json()["ignored"]
    assert ign["directory"] == "Thumbs" and ign["recursive"] is False
    removed = client.delete(f"/api/p/a/folders/{folder['id']}/ignored/{ign['id']}")
    assert removed.json()["ignored"] == []

    patched = client.patch(
        f"/api/p/a/folders/{folder['id']}",
        json={"enabled": False, "priority": 3, "name": "Images 2"},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False and patched.json()["priority"] == 3
    assert client.patch(f"/api/p/a/folders/{folder['id']}", json={"path": "/x"}).status_code == 422

    assert [f["name"] for f in client.get("/api/p/a/folders").json()] == ["Images 2"]
    from tests.integration.test_api_profile_scoped import wait_for

    removal = client.delete(f"/api/p/a/folders/{folder['id']}")
    assert removal.status_code == 202
    assert wait_for(client, "a", removal.json()["id"])["state"] == "done"
    assert client.get("/api/p/a/folders").json() == []
    assert client.delete("/api/p/a/folders/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.delete("/api/p/a/folders/not-an-id").status_code == 404


def test_scan_trigger_queues_once(client, media):
    setup(client)
    folder = client.post("/api/p/a/folders", json=folder_body("New", media / "new")).json()
    first = client.post(f"/api/p/a/folders/{folder['id']}/scan")
    assert first.status_code == 201
    job = first.json()
    assert job["type"] == "scan" and job["priority"] == 100
    assert job["params"]["folder_id"] == folder["id"]
    second = client.post(f"/api/p/a/folders/{folder['id']}/scan")
    assert second.status_code in (201, 409)  # 409 while queued; 201 only if it already finished
    if second.status_code == 409:
        assert second.json()["detail"].startswith("A scan")


def test_extensions(client):
    setup(client)
    listed = client.get("/api/p/a/extensions").json()
    jpg = next(e for e in listed if e["extension"] == "jpg")
    assert jpg["media_type"] == "image" and jpg["enabled"] is True
    updated = client.put(
        "/api/p/a/extensions", json={"extension": ".PSD", "media_type": "other"}
    ).json()
    psd = next(e for e in updated if e["extension"] == "psd")
    assert psd["media_type"] == "other"
    toggled = client.patch(f"/api/p/a/extensions/{psd['id']}", json={"enabled": False}).json()
    assert next(e for e in toggled if e["extension"] == "psd")["enabled"] is False
    bad = client.put("/api/p/a/extensions", json={"extension": "x", "media_type": "nope"})
    assert bad.status_code == 400


def test_bad_directory_strings_are_400_not_500(client, media):
    """Final review I6."""
    setup(client)
    folder = client.post("/api/p/a/folders", json=folder_body("New", media / "new")).json()
    bad = client.post(f"/api/p/a/folders/{folder['id']}/ignored", json={"directory": "../etc"})
    assert bad.status_code == 400
    assert (
        client.put("/api/p/a/settings/trash.retention_days", json={"value": 0}).status_code == 400
    )
    move = {
        "source_folder_id": folder["id"],
        "target_folder_id": folder["id"],
        "directories": ["../x"],
    }
    assert client.post("/api/p/a/archive-move", json=move).status_code == 400


def test_deleting_a_folder_runs_as_a_job_and_removes_derived_data(client, media):
    """Audit B12."""
    from tests.integration.test_api_profile_scoped import wait_for
    from visualstore.db import files, fingerprints, folders
    from visualstore.ids import id_to_str

    setup(client)
    reg = client.app.state.vs.registry
    conn, vconn = reg.conn("a"), reg.vectors_conn("a")
    folder = client.post(
        "/api/p/a/folders",
        json={"name": "L", "path": str(media / "images"), "role": "inbox", "compare_scope": "all"},
    ).json()
    fid = folders.get_folder(
        conn, __import__("visualstore.ids", fromlist=["id_from_str"]).id_from_str(folder["id"])
    )["id"]
    ext = folders.extension_map(conn)["jpg"]["id"]
    f1 = files.insert_file(
        conn, folder_id=bytes(fid), directory="", name="a.jpg", extension_id=ext, size=1, mtime=1
    )
    fingerprints.replace_frames(vconn, f1, "sscd-resnet50-512-v1", [(0, 0, b"\x00" * 1024)])
    fingerprints.allocate_keys(vconn, f1, "sscd-resnet50-512-v1", [0])
    thumb = reg.paths("a").thumbs / id_to_str(f1)[:2] / f"{id_to_str(f1)}.webp"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"t")
    r = client.delete(f"/api/p/a/folders/{folder['id']}")
    assert r.status_code == 202 and r.json()["type"] == "folder_delete"
    assert client.get("/api/p/a/folders").json()[0]["enabled"] is False  # disabled at once
    done = wait_for(client, "a", r.json()["id"])
    assert done["state"] == "done" and done["progress"]["result"]["files"] == 1
    assert client.get("/api/p/a/folders").json() == []
    assert files.get_file(conn, f1) is None and not thumb.exists()
    assert fingerprints.frames_of_file(vconn, f1, "sscd-resnet50-512-v1") == []
    assert fingerprints.pending_removals(
        vconn, "sscd-resnet50-512-v1"
    )  # keys retired for the next match


def test_changing_a_folder_role_marks_its_files_dirty(client, media):
    from visualstore.db import files, folders, groups
    from visualstore.ids import id_from_str

    setup(client)
    reg = client.app.state.vs.registry
    conn = reg.conn("a")
    folder = client.post(
        "/api/p/a/folders",
        json={"name": "L", "path": str(media / "images"), "role": "inbox", "compare_scope": "all"},
    ).json()
    fid = id_from_str(folder["id"])
    ext = folders.extension_map(conn)["jpg"]["id"]
    files.insert_file(
        conn, folder_id=fid, directory="", name="a.jpg", extension_id=ext, size=1, mtime=1
    )
    assert (
        client.patch(f"/api/p/a/folders/{folder['id']}", json={"role": "archive"}).status_code
        == 200
    )
    assert groups.dirty_count(conn) == 1
