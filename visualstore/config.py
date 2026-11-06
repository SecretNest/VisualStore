"""Process-wide configuration read from environment variables.

Everything else that can be changed at runtime is a per-profile Setting
(see visualstore.db.settings); only what the process needs before it can
open a database lives here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """The environment does not describe a runnable configuration."""


@dataclass(frozen=True)
class Config:
    data_dir: Path
    backup_dir: Path
    password: str
    compute_processes: int
    port: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        password = env.get("VISUALSTORE_PASSWORD", "")
        if not password:
            raise ConfigError(
                "VISUALSTORE_PASSWORD is not set. VisualStore refuses to start without a "
                "login password; set the environment variable and restart the container."
            )
        return cls(
            data_dir=Path(env.get("VISUALSTORE_DATA_DIR") or "/data"),
            backup_dir=Path(env.get("VISUALSTORE_BACKUP_DIR") or "/backups"),
            password=password,
            compute_processes=_positive_int(
                env, "VISUALSTORE_COMPUTE_PROCESSES", os.cpu_count() or 1
            ),
            port=_positive_int(env, "VISUALSTORE_PORT", 8080),
        )


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a positive integer, got {raw!r}") from None
    if value < 1:
        raise ConfigError(f"{key} must be a positive integer, got {raw!r}")
    return value
