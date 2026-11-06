import pytest

from tests.integration.conftest import login


def test_requires_login(client):
    assert client.get("/api/profiles").status_code == 401
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 401


def test_create_list_rename_delete(client, config):
    login(client)
    assert client.get("/api/profiles").json() == []

    created = client.post("/api/profiles", json={"name": "cg", "display_name": "CG library"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "cg" and body["display_name"] == "CG library"
    assert (config.data_dir / "profiles" / "cg" / "visualstore.db").is_file()

    assert [p["name"] for p in client.get("/api/profiles").json()] == ["cg"]

    renamed = client.patch("/api/profiles/cg", json={"display_name": "Renamed"})
    assert renamed.status_code == 200 and renamed.json()["display_name"] == "Renamed"

    usage = client.get("/api/profiles/cg/usage")
    assert usage.status_code == 200 and usage.json()["bytes"] > 0

    wrong = client.request("DELETE", "/api/profiles/cg", json={"confirm_name": "c"})
    assert wrong.status_code == 400
    assert (config.data_dir / "profiles" / "cg").is_dir()

    gone = client.request("DELETE", "/api/profiles/cg", json={"confirm_name": "cg"})
    assert gone.status_code == 200
    assert not (config.data_dir / "profiles" / "cg").exists()
    assert client.get("/api/profiles").json() == []


@pytest.mark.parametrize("bad", ["CG", "a b", "a/b", "../x", "..", "", "-a", "é", "cg\n"])
def test_invalid_names_are_400_and_touch_nothing(client, config, bad):
    """Review Focus 1 at the HTTP boundary."""
    login(client)
    response = client.post("/api/profiles", json={"name": bad})
    assert response.status_code == 400, response.text
    assert list((config.data_dir / "profiles").iterdir()) == []
    assert not (config.data_dir / "x").exists()


def test_duplicate_is_400_and_unknown_is_404(client):
    login(client)
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 201
    assert client.post("/api/profiles", json={"name": "a"}).status_code == 400
    assert client.patch("/api/profiles/zzz", json={"display_name": "x"}).status_code == 404
    assert client.get("/api/profiles/zzz/usage").status_code == 404
    delete = client.request("DELETE", "/api/profiles/zzz", json={"confirm_name": "zzz"})
    assert delete.status_code == 404


def test_system_status_counts_profiles(client):
    login(client)
    client.post("/api/profiles", json={"name": "a"})
    client.post("/api/profiles", json={"name": "b"})
    assert client.get("/api/system/status").json()["profiles"] == 2
