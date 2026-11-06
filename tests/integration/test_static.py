from fastapi.testclient import TestClient

from visualstore.app import create_app


def make_static(tmp_path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>VisualStore</title>")
    (static / "assets" / "app.js").write_text("console.log('hi')")
    return static


def test_spa_fallback_and_assets(config, tmp_path):
    static = make_static(tmp_path)
    with TestClient(create_app(config, static_dir=static)) as client:
        assert client.get("/").text.startswith("<!doctype html>")
        assert client.get("/p/cg/overview").text.startswith("<!doctype html>")
        assert client.get("/login").text.startswith("<!doctype html>")
        js = client.get("/assets/app.js")
        assert js.status_code == 200 and "console.log" in js.text
        missing = client.get("/api/does-not-exist")
        assert missing.status_code == 404
        assert missing.headers["content-type"].startswith("application/json")
        # a path that escapes the static directory falls back to index instead of leaking files
        escaped = client.get("/%2e%2e/pyproject.toml")
        assert escaped.status_code == 200 and escaped.text.startswith("<!doctype html>")


def test_without_a_build_the_api_still_works_and_root_is_404(config, tmp_path):
    with TestClient(create_app(config, static_dir=tmp_path / "nowhere")) as client:
        assert client.get("/api/auth/me").status_code == 200
        assert client.get("/").status_code == 404
