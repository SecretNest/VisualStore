-- Profile registry and process secrets. The only cross-profile data (design §5.1, §5.2).
CREATE TABLE profile (
    name         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE secret (
    key   TEXT PRIMARY KEY,
    value BLOB NOT NULL
);
