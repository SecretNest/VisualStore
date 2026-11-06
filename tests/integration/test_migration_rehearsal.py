"""Design §10 "migration test": run the pipeline, copy /data elsewhere, start from the copy,
compare every query, then scan incrementally."""

import json
import shutil
import time

from fastapi.testclient import TestClient

from tests.fixtures.images import png_bytes
from tests.fixtures.tiny_model import build_tiny_model, tiny_spec
from tests.fixtures.vips import require_pyvips
from tests.integration.conftest import login, make_config
from visualstore.app import create_app

pyvips = require_pyvips()


def wait_idle(client, name, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = client.get(f"/api/p/{name}/jobs?limit=200").json()
        if jobs and all(j["state"] in ("done", "failed", "cancelled") for j in jobs):
            failed = [j for j in jobs if j["state"] == "failed"]
            assert not failed, failed
            return jobs
        time.sleep(0.2)
    raise AssertionError("jobs did not settle")


def snapshot(client, name):
    groups = client.get(f"/api/p/{name}/groups?limit=200").json()
    detail = {g["id"]: client.get(f"/api/p/{name}/groups/{g['id']}").json() for g in groups}
    files = {}
    for g in groups:
        for m in g["members"]:
            files[m["file_id"]] = client.get(f"/api/p/{name}/files/{m['file_id']}").json()
    counts = client.get(f"/api/p/{name}/groups/counts").json()
    return {"groups": groups, "detail": detail, "files": files, "counts": counts}


def tiny_model_dir(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    spec = tiny_spec(8)
    build_tiny_model(model_dir / spec.file_name(), dim=8)
    entry = {"dim": 8, "input_size": 16, "mean": [0, 0, 0], "std": [1, 1, 1]}
    (model_dir / "models.json").write_text(json.dumps({spec.id: entry}))
    return model_dir, spec


def test_data_directory_moves_without_repair(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.png").write_bytes(png_bytes(64, 48, gradient=True))
    (media / "b.png").write_bytes(png_bytes(64, 48, gradient=True))  # identical
    (media / "c.png").write_bytes(png_bytes(64, 48, rgb=(0, 200, 0)))
    model_dir, spec = tiny_model_dir(tmp_path)

    first_root = tmp_path / "first"
    with TestClient(create_app(make_config(first_root, model_dir))) as client:
        login(client)
        assert client.post("/api/profiles", json={"name": "demo"}).status_code == 201
        client.put("/api/p/demo/settings/model.id", json={"value": spec.id})
        body = {"name": "Lib", "path": str(media), "role": "inbox", "compare_scope": "all"}
        folder = client.post("/api/p/demo/folders", json=body).json()
        client.post(f"/api/p/demo/folders/{folder['id']}/scan")
        wait_idle(client, "demo")
        before = snapshot(client, "demo")
        assert len(before["groups"]) == 1 and before["groups"][0]["member_count"] == 2
    # stop-and-copy (design §3.1): the whole data directory, nothing else
    second_root = tmp_path / "second"
    shutil.copytree(first_root / "data", second_root / "data")
    with TestClient(create_app(make_config(second_root, model_dir))) as client:
        login(client)
        after = snapshot(client, "demo")
        assert after == before  # every query answers the same from the moved directory
        jobs = client.get("/api/p/demo/jobs?limit=200").json()
        repairs = [
            j
            for j in jobs
            if j["type"] in ("rebuild_index", "fingerprint") and j["state"] in ("queued", "running")
        ]
        assert repairs == []  # nothing to repair after a move
        (media / "d.png").write_bytes(png_bytes(64, 48, rgb=(0, 200, 0)))  # identical to c.png
        client.post(f"/api/p/demo/folders/{folder['id']}/scan")
        wait_idle(client, "demo")
        groups = client.get("/api/p/demo/groups?limit=200").json()
        assert sorted(g["member_count"] for g in groups) == [2, 2]
