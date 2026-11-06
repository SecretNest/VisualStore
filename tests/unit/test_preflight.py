import os
import stat

import pytest

from visualstore.preflight import PreflightError, check_directories


def test_check_directories_creates_and_probes(tmp_path):
    data, backups = tmp_path / "data", tmp_path / "bk" / "nested"
    check_directories(data, backups)
    assert data.is_dir() and backups.is_dir()
    assert list(data.iterdir()) == [] and list(backups.iterdir()) == []  # probe removed


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_directory_fails_fast_with_a_clear_message(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(PreflightError) as info:
            check_directories(data, tmp_path / "bk")
        message = str(info.value)
        assert str(data) in message and "PUID" in message and f"uid {os.geteuid()}" in message
    finally:
        data.chmod(stat.S_IRWXU)
