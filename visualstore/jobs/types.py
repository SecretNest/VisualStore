"""Job type registry and priorities (design §6.2, §6.3)."""

PRIORITY_USER = 100
PRIORITY_INBOX = 50
PRIORITY_ARCHIVE = 20
PRIORITY_MAINTENANCE = 0

# Which executor runs a job type. Later milestones add scan, fingerprint, match, ...
KIND_OF: dict[str, str] = {
    "backup": "io",
    "ping": "compute",  # pool round-trip check; used by tests and the system status page
    "fail": "compute",  # deliberately raises; used by tests
    "fingerprint": "compute",
    "match": "compute",
    "build_index": "compute",
    "rebuild_index": "io",
    "scan": "io",
    "regroup": "io",
    "apply_action": "io",
    "purge_trash": "io",
    "archive_move": "io",
    "import_v1": "io",
}
