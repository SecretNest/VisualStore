-- Milestone 2: libraries, files, byte-identical pairs, groups, actions (design §5.1).

CREATE TABLE folder (
    id            BLOB PRIMARY KEY,
    name          TEXT NOT NULL COLLATE NOCASE UNIQUE,
    path          TEXT NOT NULL,                    -- absolute container path, no trailing slash
    role          TEXT NOT NULL CHECK (role IN ('archive', 'inbox')),
    compare_scope TEXT NOT NULL CHECK (compare_scope IN ('all', 'other_directories', 'other_folders')),
    enabled       INTEGER NOT NULL DEFAULT 1,
    priority      INTEGER NOT NULL DEFAULT 0,       -- lower wins when several archives hold copies
    created_at    TEXT NOT NULL
);

CREATE TABLE ignored_directory (
    id        BLOB PRIMARY KEY,
    folder_id BLOB NOT NULL REFERENCES folder(id) ON DELETE CASCADE,
    directory TEXT NOT NULL COLLATE NOCASE,          -- relative, '/'-separated, no edge slashes
    recursive INTEGER NOT NULL DEFAULT 1,
    UNIQUE (folder_id, directory)
);

CREATE TABLE extension (
    id         BLOB PRIMARY KEY,
    extension  TEXT NOT NULL COLLATE NOCASE UNIQUE,  -- lowercase, no dot
    media_type TEXT NOT NULL CHECK (media_type IN ('image', 'animated', 'video', 'other')),
    enabled    INTEGER NOT NULL DEFAULT 1
);

INSERT INTO extension (id, extension, media_type) VALUES
    (randomblob(16), 'jpg', 'image'), (randomblob(16), 'jpeg', 'image'), (randomblob(16), 'png', 'image'),
    (randomblob(16), 'webp', 'image'), (randomblob(16), 'avif', 'image'), (randomblob(16), 'heic', 'image'),
    (randomblob(16), 'heif', 'image'), (randomblob(16), 'bmp', 'image'), (randomblob(16), 'tiff', 'image'),
    (randomblob(16), 'tif', 'image'),
    (randomblob(16), 'gif', 'animated'), (randomblob(16), 'apng', 'animated'),
    (randomblob(16), 'mp4', 'video'), (randomblob(16), 'mkv', 'video'), (randomblob(16), 'webm', 'video'),
    (randomblob(16), 'mov', 'video'), (randomblob(16), 'avi', 'video');

CREATE TABLE file (
    id           BLOB PRIMARY KEY,
    folder_id    BLOB NOT NULL REFERENCES folder(id) ON DELETE CASCADE,
    directory    TEXT NOT NULL COLLATE NOCASE,       -- '' for the folder root
    name         TEXT NOT NULL COLLATE NOCASE,       -- includes the extension
    extension_id BLOB NOT NULL REFERENCES extension(id),
    size         INTEGER NOT NULL,
    mtime        INTEGER NOT NULL,                   -- st_mtime_ns; 0 = unknown (imported)
    sha1         BLOB,
    state        TEXT NOT NULL CHECK (state IN ('new', 'fingerprinted', 'unreadable', 'not_media', 'missing')),
    created_at   TEXT NOT NULL,
    UNIQUE (folder_id, directory, name)
);
CREATE INDEX file_sha1 ON file (sha1) WHERE sha1 IS NOT NULL;
CREATE INDEX file_state ON file (state);
CREATE INDEX file_folder_dir ON file (folder_id, directory);

CREATE TABLE pair (
    file_a          BLOB NOT NULL REFERENCES file(id) ON DELETE CASCADE,
    file_b          BLOB NOT NULL REFERENCES file(id) ON DELETE CASCADE,
    distance        REAL,
    source          TEXT NOT NULL CHECK (source IN ('sha1', 'vector', 'import')),
    judgment        TEXT NOT NULL DEFAULT 'none' CHECK (judgment IN ('none', 'related_keep', 'unrelated')),
    judged_at       TEXT,
    judgment_origin TEXT CHECK (judgment_origin IN ('user', 'import_v1', 'rule')),
    model_id        TEXT,
    PRIMARY KEY (file_a, file_b),
    CHECK (file_a < file_b)
);
CREATE INDEX pair_file_b ON pair (file_b);

CREATE TABLE file_group (
    id            BLOB PRIMARY KEY,
    member_count  INTEGER NOT NULL,
    min_distance  REAL,
    has_identical INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL CHECK (status IN ('open', 'auto_resolved', 'resolved', 'dismissed')),
    updated_at    TEXT NOT NULL
);
CREATE INDEX file_group_status ON file_group (status, updated_at);

CREATE TABLE file_group_member (
    group_id BLOB NOT NULL REFERENCES file_group(id) ON DELETE CASCADE,
    file_id  BLOB NOT NULL REFERENCES file(id) ON DELETE CASCADE,
    decision TEXT NOT NULL DEFAULT 'undecided' CHECK (decision IN ('keep', 'delete', 'undecided')),
    PRIMARY KEY (group_id, file_id)
);
CREATE UNIQUE INDEX file_group_member_file ON file_group_member (file_id);  -- one group per file

-- Files whose connected component must be recomputed by the regroup job.
CREATE TABLE regroup_queue (
    file_id BLOB PRIMARY KEY REFERENCES file(id) ON DELETE CASCADE
);

-- Audit and undo. file_id has no foreign key: a trashed file's row is gone, the action remains.
CREATE TABLE action (
    id            BLOB PRIMARY KEY,
    type          TEXT NOT NULL CHECK (type IN ('trash', 'restore', 'move', 'rename', 'purge')),
    file_id       BLOB,
    before        TEXT NOT NULL,   -- JSON
    after         TEXT NOT NULL,   -- JSON
    at            TEXT NOT NULL,
    superseded_at TEXT             -- set on a trash action once restored or purged
);
CREATE INDEX action_type_at ON action (type, at);
