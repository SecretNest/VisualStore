-- Milestone 3: per-frame vectors and the usearch key map (design §5.1 Fingerprint, §5.2).
CREATE TABLE fingerprint (
    file_id       BLOB NOT NULL,
    model_id      TEXT NOT NULL,
    frame_index   INTEGER NOT NULL,
    frame_time_ms INTEGER NOT NULL DEFAULT 0,
    vector        BLOB NOT NULL,                 -- float16, dimension set by the model
    PRIMARY KEY (file_id, model_id, frame_index)
);
CREATE INDEX fingerprint_model ON fingerprint (model_id);

-- usearch keys are 64-bit integers; this table is the only map back to (file, frame).
-- Keys are never recycled: a deleted key stays marked until the next match job removes it
-- from the index file, then the row is dropped.
CREATE TABLE index_key (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     BLOB NOT NULL,
    model_id    TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (file_id, model_id, frame_index)
);
CREATE INDEX index_key_deleted ON index_key (model_id, deleted);

CREATE TABLE index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
