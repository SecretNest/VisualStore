"""Start-up pre-flight: the data and backup directories must be writable by this process,
otherwise the scheduler would loop on PermissionError (design §9.4, ADR-0008)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path


class PreflightError(Exception):
    pass


def _owner(path: Path) -> str:
    try:
        st = path.stat()
        return f"uid {st.st_uid}, gid {st.st_gid}"
    except OSError:
        return "unknown owner"


def check_directories(data_dir: Path, backup_dir: Path) -> None:
    for label, directory in (("data", data_dir), ("backup", backup_dir)):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / f".write-probe-{uuid.uuid4().hex}"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            raise PreflightError(
                f"The {label} directory {directory} is not writable by this process"
                f" (uid {os.geteuid()}, gid {os.getegid()}; the directory belongs to"
                f" {_owner(directory)}): {exc}. Set PUID/PGID to the owner of the volume or"
                " change the directory's ownership, then restart."
            ) from exc
