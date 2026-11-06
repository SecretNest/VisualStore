from pathlib import Path

import pytest

from visualstore.config import Config, ConfigError


def test_missing_password_refuses_to_start():
    with pytest.raises(ConfigError) as exc:
        Config.from_env({})
    assert "VISUALSTORE_PASSWORD" in str(exc.value)


def test_empty_password_is_treated_as_missing():
    with pytest.raises(ConfigError):
        Config.from_env({"VISUALSTORE_PASSWORD": ""})


def test_defaults_apply_when_only_password_is_set():
    cfg = Config.from_env({"VISUALSTORE_PASSWORD": "secret"})
    assert cfg.data_dir == Path("/data")
    assert cfg.backup_dir == Path("/backups")
    assert cfg.port == 8080
    assert cfg.compute_processes >= 1
    assert cfg.password == "secret"


def test_every_variable_can_be_overridden():
    cfg = Config.from_env(
        {
            "VISUALSTORE_PASSWORD": "pw",
            "VISUALSTORE_DATA_DIR": "/tmp/vs-data",
            "VISUALSTORE_BACKUP_DIR": "/tmp/vs-backups",
            "VISUALSTORE_COMPUTE_PROCESSES": "3",
            "VISUALSTORE_PORT": "9000",
        }
    )
    assert cfg.data_dir == Path("/tmp/vs-data")
    assert cfg.backup_dir == Path("/tmp/vs-backups")
    assert cfg.compute_processes == 3
    assert cfg.port == 9000


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5"])
def test_non_positive_or_non_integer_process_count_is_rejected(value):
    with pytest.raises(ConfigError) as exc:
        Config.from_env({"VISUALSTORE_PASSWORD": "pw", "VISUALSTORE_COMPUTE_PROCESSES": value})
    assert "VISUALSTORE_COMPUTE_PROCESSES" in str(exc.value)


def test_model_dir_default_and_override():
    assert Config.from_env({"VISUALSTORE_PASSWORD": "pw"}).model_dir == Path("/data/models")
    cfg = Config.from_env({"VISUALSTORE_PASSWORD": "pw", "VISUALSTORE_MODEL_DIR": "/models"})
    assert cfg.model_dir == Path("/models")
