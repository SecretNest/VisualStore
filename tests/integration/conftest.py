import pytest
from fastapi.testclient import TestClient

from tests.fixtures.models import install_fake_model
from visualstore.app import create_app
from visualstore.config import Config

PASSWORD = "pw"


@pytest.fixture
def config(tmp_path):
    install_fake_model(tmp_path / "models")  # scans need the active model to resolve
    return Config(
        data_dir=tmp_path / "data",
        backup_dir=tmp_path / "backups",
        password=PASSWORD,
        compute_processes=1,
        port=8080,
        model_dir=tmp_path / "models",
    )


@pytest.fixture
def client(config):
    # Entering the context runs the lifespan: registry open, pool start, scheduler start.
    with TestClient(create_app(config)) as c:
        yield c


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
