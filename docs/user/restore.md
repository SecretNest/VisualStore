# Backups and restore

## What is backed up

The backup job copies the two SQLite databases of a profile with SQLite's online backup API
and verifies each copy with `PRAGMA integrity_check`:

- `/backups/<profile>/visualstore-<timestamp>.db` — metadata (Folders, files, pairs, groups,
  judgments, jobs, settings). Daily by default, 14 kept.
- `/backups/<profile>/vectors-<timestamp>.db` — vectors and index keys. Weekly, 4 kept.
- `/backups/global.db` — the profile list, copied with every backup.

The search index (`index.usearch`), `thumbs/` and `previews/` are derived and not backed up.
Change the policy per profile under **Jobs → Settings** (`backup.*`), or run **Overview → Back
up now**. Point your external backup tool at `/backups`.

## Restore one profile

1. `docker compose down`.
2. Copy the chosen `visualstore-<timestamp>.db` over `/data/profiles/<profile>/visualstore.db`
   and, if the vectors are gone or damaged, `vectors-<timestamp>.db` over `vectors.db`. Delete
   stale `visualstore.db-wal` / `visualstore.db-shm` files next to them.
3. `docker compose up -d`.

On start the server heals the rest by itself:

- vectors that belong to files the restored metadata no longer knows are removed and the index
  is rebuilt;
- files the metadata knows as fingerprinted but which lost their vectors are fingerprinted again;
- an index that does not match the databases or the index settings is rebuilt.

Other profiles are unaffected. Files added after the backup are found again by the next scan.

## Restore versus move

Moving the whole installation is **not** a restore: stop the container and copy the entire
`/data` directory ([Initialise and migrate](initialise-and-migrate.md)). A restore replaces one
profile's databases from `/backups`.
