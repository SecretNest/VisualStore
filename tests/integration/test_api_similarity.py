from tests.integration.conftest import login
from tests.integration.test_api_profile_scoped import wait_for
from visualstore.db import files, folders
from visualstore.ids import id_to_str


def setup(client):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201


def test_models_listing_and_switch_validation(client, config):
    setup(client)
    listed = client.get("/api/models").json()
    by_id = {m["id"]: m for m in listed}
    assert (
        by_id["sscd-resnet50-512-v1"]["dim"] == 512
        and by_id["sscd-resnet50-512-v1"]["available"] is False
    )
    config.model_dir.mkdir(parents=True, exist_ok=True)
    (config.model_dir / "dinov2-vits14-384-v1.onnx").write_bytes(b"x")
    assert {m["id"]: m["available"] for m in client.get("/api/models").json()}[
        "dinov2-vits14-384-v1"
    ] is True
    assert client.post("/api/p/a/model-switch", json={"model_id": "nope"}).status_code == 400
    job = client.post("/api/p/a/model-switch", json={"model_id": "dinov2-vits14-384-v1"})
    assert job.status_code == 201
    finished = wait_for(client, "a", job.json()["id"])
    assert (
        finished["state"] == "done"
        and finished["progress"]["result"]["model_id"] == "dinov2-vits14-384-v1"
    )
    assert client.get("/api/p/a/settings").json()["model.id"] == "dinov2-vits14-384-v1"


def test_eval_endpoints(client, tmp_path):
    setup(client)
    assert client.get("/api/p/a/eval/reports").json() == []
    job = client.post("/api/p/a/eval", json={"db_path": str(tmp_path / "missing.db")})
    assert job.status_code == 201
    finished = wait_for(client, "a", job.json()["id"])
    assert finished["state"] == "failed" and "not found" in finished["error"]
    reports = tmp_path / "data" / "profiles" / "a" / "reports"
    reports.mkdir()
    (reports / "eval-sscd-resnet50-512-v1-20261106T000000Z.md").write_text("# report")
    [entry] = client.get("/api/p/a/eval/reports").json()
    assert entry["file"].startswith("eval-")
    got = client.get(f"/api/p/a/eval/reports/{entry['file']}")
    assert got.status_code == 200 and got.text == "# report"
    assert client.get("/api/p/a/eval/reports/../../global.db").status_code == 404
    assert client.get("/api/p/a/eval/reports/other.txt").status_code == 404


def test_thumbnail_endpoint(client, tmp_path):
    setup(client)
    conn = client.app.state.vs.registry.conn("a")
    (tmp_path / "lib").mkdir()
    folder = folders.create_folder(
        conn, name="L", path=str(tmp_path / "lib"), role="inbox", compare_scope="all"
    )
    ext = folders.extension_map(conn)["jpg"]["id"]
    fid = files.insert_file(
        conn, folder_id=folder, directory="", name="a.jpg", extension_id=ext, size=1, mtime=1
    )
    text = id_to_str(fid)
    assert client.get(f"/api/p/a/files/{text}/thumbnail").status_code == 404
    thumbs = tmp_path / "data" / "profiles" / "a" / "thumbs" / text[:2]
    thumbs.mkdir(parents=True)
    (thumbs / f"{text}.webp").write_bytes(b"RIFF....WEBPVP8 ")
    got = client.get(f"/api/p/a/files/{text}/thumbnail")
    assert got.status_code == 200 and got.headers["content-type"] == "image/webp"
