"""Io job handlers run by the scheduler's worker thread."""

from visualstore.jobs.io.backup import run_backup
from visualstore.jobs.io.scan import run_scan
from visualstore.jobs.scheduler import IoHandler

IO_HANDLERS: dict[str, IoHandler] = {
    "backup": run_backup,
    "scan": run_scan,
}
