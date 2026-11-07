"""Job type registry and priorities (design §6.2, §6.3)."""

PRIORITY_USER = 100
PRIORITY_INBOX = 50
PRIORITY_ARCHIVE = 20
PRIORITY_MAINTENANCE = 0

# Which executor runs a job type. Later milestones add scan, fingerprint, match, ...
# Queued jobs of these types are the only record of pending index/file work: a cancelled match
# leaves live keys out of the index, a cancelled build_index leaves vectors unmatched, a cancelled
# apply_action/bulk_resolve/folder_delete leaves half-applied state. The API refuses to cancel them.
INTERNAL_JOB_TYPES = frozenset(
    {"match", "build_index", "apply_action", "bulk_resolve", "folder_delete", "model_switch"}
)

KIND_OF: dict[str, str] = {
    "backup": "io",
    "ping": "compute",  # pool round-trip check; used by tests and the system status page
    "fingerprint": "compute",
    "match": "compute",
    "build_index": "compute",
    "rebuild_index": "io",
    "eval": "io",
    "model_switch": "io",
    "bulk_resolve": "io",
    "maintenance": "io",
    "folder_delete": "io",
    "clean_empty_dirs": "io",
    "scan": "io",
    "regroup": "io",
    "apply_action": "io",
    "purge_trash": "io",
    "archive_move": "io",
    "import_v1": "io",
}
