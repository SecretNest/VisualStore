"""Design §9.1 restore: back up, change, stop, put the old metadata back, start; the server
heals vectors and index itself (Review Focus 3)."""

import shutil

from fastapi.testclient import TestClient

from tests.fixtures.images import png_bytes
from tests.fixtures.vips import require_pyvips
from tests.integration.conftest import login, make_config
from tests.integration.test_migration_rehearsal import tiny_model_dir, wait_idle
from visualstore.app import create_app
from visualstore.db import files as files_db
from visualstore.ids import id_from_str, id_to_str

pyvips = require_pyvips()


def fingerprinted_count(client):
    overview = client.get("/api/p/demo/overview").json()
    return overview["folders"][0]["counts"].get("fingerprinted", 0)


def test_restoring_an_older_metadata_backup_heals_vectors_and_index(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.png").write_bytes(png_bytes(64, 48, gradient=True))
    model_dir, spec = tiny_model_dir(tmp_path)
    root = tmp_path / "root"
    config = make_config(root, model_dir)
    with TestClient(create_app(config)) as client:
        login(client)
        client.post("/api/profiles", json={"name": "demo"})
        # model.id is not a PUT setting (ADR-0008): switch through the job, which also queues vectors
        assert (
            client.post("/api/p/demo/model-switch", json={"model_id": spec.id}).status_code == 201
        )
        wait_idle(client, "demo")
        body = {"name": "Lib", "path": str(media), "role": "inbox", "compare_scope": "all"}
        folder = client.post("/api/p/demo/folders", json=body).json()
        client.post(f"/api/p/demo/folders/{folder['id']}/scan")
        wait_idle(client, "demo")
        assert client.post("/api/p/demo/backups", json={"targets": ["metadata"]}).status_code == 201
        wait_idle(client, "demo")
        backups = client.get("/api/p/demo/backups").json()
        [meta_backup] = [b for b in backups if b["target"] == "metadata"]
        in_backup = {j["id"] for j in client.get("/api/p/demo/jobs?limit=200").json()}
        (media / "b.png").write_bytes(
            png_bytes(64, 48, rgb=(0, 0, 250))
        )  # arrives after the backup
        client.post(f"/api/p/demo/folders/{folder['id']}/scan")
        wait_idle(client, "demo")
        assert fingerprinted_count(client) == 2
        conn = client.app.state.vs.registry.conn("demo")
        a_id = id_to_str(
            bytes(files_db.find_file(conn, id_from_str(folder["id"]), "", "a.png")["id"])
        )
    # stop, put the older metadata back (design §9.1 restore procedure), start
    profile_dir = root / "data" / "profiles" / "demo"
    shutil.copy(root / "backups" / "demo" / meta_backup["file"], profile_dir / "visualstore.db")
    for extra in ("visualstore.db-wal", "visualstore.db-shm"):
        (profile_dir / extra).unlink(missing_ok=True)
    with TestClient(create_app(config)) as client:
        login(client)
        wait_idle(client, "demo")
        jobs = client.get("/api/p/demo/jobs?limit=200").json()
        assert any(
            j["type"] == "rebuild_index" and j["state"] == "done" for j in jobs
        )  # b.png's frames
        assert fingerprinted_count(client) == 1  # the restored metadata knows a.png only
        new_jobs = [j for j in jobs if j["id"] not in in_backup]
        for j in new_jobs:  # a.png kept its vectors: no re-fingerprint after the restore
            if j["type"] == "fingerprint":
                assert a_id not in [f["id"] for f in j["params"]["files"]]
        assert client.get(f"/api/p/demo/files/{a_id}/frames").json()["frames"] == [
            {"index": 0, "time_ms": 0}
        ]
        client.post(f"/api/p/demo/folders/{folder['id']}/scan")
        wait_idle(client, "demo")  # b.png is found again and fingerprinted
        assert fingerprinted_count(client) == 2
        conn = client.app.state.vs.registry.conn("demo")
        b_id = id_to_str(
            bytes(files_db.find_file(conn, id_from_str(folder["id"]), "", "b.png")["id"])
        )
        assert len(client.get(f"/api/p/demo/files/{b_id}/frames").json()["frames"]) == 1
        assert client.get("/api/p/demo/groups/counts").json()["dirty"] == 0
