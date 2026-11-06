import pytest

from visualstore.db import settings
from visualstore.db.connection import connect
from visualstore.db.migrate import migrate, migrations_dir


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "p.db")
    migrate(c, migrations_dir("profile"))
    return c


def test_defaults_are_returned_when_nothing_is_stored(conn):
    assert settings.get(conn, "backup.metadata.interval_hours") == 24
    assert settings.get(conn, "backup.metadata.keep") == 14
    assert settings.get(conn, "backup.vectors.interval_hours") == 168
    assert settings.get(conn, "backup.vectors.keep") == 4
    assert settings.all(conn) == settings.DEFAULTS


def test_set_persists_and_overrides_default(conn):
    settings.set(conn, "backup.metadata.keep", 30)
    assert settings.get(conn, "backup.metadata.keep") == 30
    assert settings.all(conn)["backup.metadata.keep"] == 30
    assert settings.all(conn)["backup.vectors.keep"] == 4


def test_unknown_key_is_rejected_on_get_and_set(conn):
    with pytest.raises(KeyError):
        settings.get(conn, "no.such.key")
    with pytest.raises(KeyError):
        settings.set(conn, "no.such.key", 1)


def test_value_must_match_default_type(conn):
    with pytest.raises(TypeError):
        settings.set(conn, "backup.metadata.keep", "14")
    with pytest.raises(TypeError):
        settings.set(conn, "backup.metadata.keep", True)  # bool is not accepted for an int


def test_milestone_2_defaults_and_ranges(conn):
    assert settings.get(conn, "trash.enabled") is True
    assert settings.get(conn, "trash.retention_days") == 30
    assert settings.get(conn, "resolution.auto_execute_identical") is True
    assert settings.get(conn, "scan.interval_hours") == 24
    assert settings.get(conn, "grouping.group_distance") == 0.3
    settings.set(conn, "scan.interval_hours", 0)  # 0 = disabled is allowed
    with pytest.raises(ValueError):
        settings.set(conn, "trash.retention_days", 0)
    with pytest.raises(ValueError):
        settings.set(conn, "backup.metadata.keep", 0)
    with pytest.raises(ValueError):
        settings.set(conn, "grouping.group_distance", -0.1)
    settings.set(conn, "grouping.group_distance", 0.35)
    assert settings.get(conn, "grouping.group_distance") == 0.35


def test_milestone_3_defaults_and_string_setting(conn):
    assert settings.get(conn, "model.id") == "sscd-resnet50-512-v1"
    assert settings.get(conn, "similarity.candidate_distance") == 0.5
    assert settings.get(conn, "similarity.near_duplicate") == 0.15
    assert settings.get(conn, "similarity.variant") == 0.3
    assert settings.get(conn, "similarity.top_k") == 64
    assert settings.get(conn, "grouping.group_distance") == 0.3
    assert settings.get(conn, "media.max_pixels") == 80_000_000
    settings.set(conn, "model.id", "dinov2-vits14-384-v1")
    assert settings.get(conn, "model.id") == "dinov2-vits14-384-v1"
    with pytest.raises(ValueError):
        settings.set(conn, "model.id", "")
    with pytest.raises(ValueError):
        settings.set(conn, "similarity.top_k", 0)


def test_milestone_5_defaults_and_ranges(conn):
    assert settings.get(conn, "frames.max_per_file") == 8
    assert settings.get(conn, "media.ffmpeg_timeout_s") == 120
    with pytest.raises(ValueError):
        settings.set(conn, "frames.max_per_file", 0)
    with pytest.raises(ValueError):
        settings.set(conn, "media.ffmpeg_timeout_s", 0)
    settings.set(conn, "frames.max_per_file", 3)
    assert settings.get(conn, "frames.max_per_file") == 3


def test_milestone_6_index_settings(conn):
    assert settings.get(conn, "index.quantization") == "f16"
    assert settings.get(conn, "index.pca_dims") == 0
    settings.set(conn, "index.quantization", "i8")
    with pytest.raises(ValueError):
        settings.set(conn, "index.quantization", "f32")
    assert settings.get(conn, "maintenance.vacuum_free_ratio") == 0.2
