"""Io job handlers run by the scheduler's worker thread."""

from visualstore.jobs.io.actions import run_apply_action, run_purge_trash
from visualstore.jobs.io.archive_move import run_archive_move
from visualstore.jobs.io.backup import run_backup
from visualstore.jobs.io.bulk_resolve import run_bulk_resolve
from visualstore.jobs.io.eval import run_eval
from visualstore.jobs.io.import_v1 import run_import_v1
from visualstore.jobs.io.model_switch import run_model_switch
from visualstore.jobs.io.rebuild import run_rebuild_index
from visualstore.jobs.io.regroup import run_regroup
from visualstore.jobs.io.scan import run_scan
from visualstore.jobs.scheduler import IoHandler

IO_HANDLERS: dict[str, IoHandler] = {
    "backup": run_backup,
    "scan": run_scan,
    "regroup": run_regroup,
    "apply_action": run_apply_action,
    "purge_trash": run_purge_trash,
    "archive_move": run_archive_move,
    "import_v1": run_import_v1,
    "rebuild_index": run_rebuild_index,
    "eval": run_eval,
    "model_switch": run_model_switch,
    "bulk_resolve": run_bulk_resolve,
}
