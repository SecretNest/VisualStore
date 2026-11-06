-- Milestone 1 tables of a profile's metadata database (design §5.1 Job, Setting).
CREATE TABLE job (
    id          BLOB PRIMARY KEY,
    type        TEXT NOT NULL,
    params      TEXT NOT NULL DEFAULT '{}',
    state       TEXT NOT NULL CHECK (state IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    priority    INTEGER NOT NULL DEFAULT 0,
    kind        TEXT NOT NULL CHECK (kind IN ('io', 'compute')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    progress    TEXT NOT NULL DEFAULT '{}',
    error       TEXT,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);

CREATE INDEX job_queue ON job (state, kind, priority DESC, created_at);

CREATE TABLE setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
