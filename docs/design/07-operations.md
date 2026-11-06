# 07 · Operations

## 9.1 Backup

- Use SQLite's online backup API (`sqlite3.Connection.backup`) to back up `visualstore.db` and `vectors.db` separately into `/backups/<profile>/`, file names carrying a timestamp. `global.db` is tiny and is copied to `/backups/global.db` alongside any profile's backup. The backup directory is its own volume so an external backup system can grab it in one place.
- Policy is a per-profile setting. Default: metadata database daily, keep 14; vector database weekly, keep 4.
- Verify after each backup by running `PRAGMA integrity_check` on the copy.
- `index.usearch` and `thumbs/` are not backed up; they can be rebuilt.
- The restore procedure goes into the README: stop the container, replace the files under the profile directory, start; the server notices the index and vectors disagree and rebuilds automatically. Restoring one profile does not affect others.
- Whole-system migration ([02 §3.1](02-architecture.md#31-initialisation-and-migration)) is a stop-and-copy of the entire `/data`, not a backup flow; index and thumbnails travel with it so nothing is rebuilt on the NAS.

## 9.2 Authentication

- One password shared by all profiles, read from the environment variable `VISUALSTORE_PASSWORD`. Not stored in the database, no change-password UI; changing it means changing the env and restarting. If unset, the server refuses to start and says why.
- Successful login issues a session cookie (HttpOnly, SameSite=Lax; the signing key is generated randomly on first start and stored in `global.db`). Failed logins are rate-limited. Password comparison is constant-time.
- Server and compute children communicate over in-process queues with no network interface; no authentication needed there.
- HTTPS is the reverse proxy's responsibility; document it.

## 9.3 Configuration

Environment variables:

| Variable | Description |
|---|---|
| `VISUALSTORE_DATA_DIR` | Data directory, default `/data`. |
| `VISUALSTORE_BACKUP_DIR` | Backup directory, default `/backups`. |
| `VISUALSTORE_PASSWORD` | Login password. Required. |
| `VISUALSTORE_COMPUTE_PROCESSES` | Number of compute children, default CPU count. |
| `VISUALSTORE_PORT` | Listen port, default 8080. |

Every other runtime parameter is a per-profile Setting editable in the UI.

## 9.4 Container and compose

```yaml
services:
  visualstore:
    image: ghcr.io/secretnest/visualstore:latest
    ports: ["8080:8080"]
    environment:
      VISUALSTORE_PASSWORD: "${VISUALSTORE_PASSWORD}"
      # PUID / PGID: the user the container runs as (default 1000:1000, ADR-0008)
    volumes:
      - ./data:/data
      - ./backups:/backups
      - /volume1/Images:/media/images
      - /volume1/Photos:/media/photos
```

Images are published to `ghcr.io/secretnest/visualstore` and to the same `secretnest/visualstore` path on Alibaba Cloud Container Registry ([ADR-0003](../decisions/0003-m1-implementation-choices.md)). Media directories for every profile are mounted into the same container; each profile's Folders point at their own mount paths. The initialisation machine and the NAS use the same compose file, changing only the host side of the mounts.

## 9.5 Resources

- Index memory: one million files, 1.5 frames on average, 512-dim: about 1.5 GB in f16, about 0.8 GB with i8 quantisation, plus the HNSW graph. usearch opens the index as an mmap read-only view, so it need not be fully resident for queries. If NAS memory is tight, PCA down to 256 dimensions is available as a setting.
- Thumbnails: one million 256 px webp files take roughly 10 to 20 GB.
- Inference: several hundred milliseconds to a second per image on a weak CPU; acceptable for incremental work on the NAS. The first full run happens on a powerful machine per [02 §3.1](02-architecture.md#31-initialisation-and-migration).
