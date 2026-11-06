# Deployment

VisualStore is one container. It needs a password, a local disk for its data directory, a
directory for backups, and your media directories mounted read-write.

## Image

- `ghcr.io/secretnest/visualstore:<version>` (GitHub Container Registry)
- `<ACR_REGISTRY>/secretnest/visualstore:<version>` (Alibaba Cloud Container Registry, same content)

Both are multi-arch (amd64, arm64). The image contains the two similarity models (about 190 MB),
libvips and ffmpeg; nothing is downloaded at run time.

## Compose

Copy `docker/compose.yaml` and `.env.example` (as `.env`) into a directory of your own, set
`VISUALSTORE_PASSWORD` in `.env`, edit the media volumes, then `docker compose up -d`.

```yaml
services:
  visualstore:
    image: ghcr.io/secretnest/visualstore:latest
    restart: unless-stopped
    ports: ["8080:8080"]
    environment:
      VISUALSTORE_PASSWORD: "${VISUALSTORE_PASSWORD}"
      # PUID: "1000"                      # user the container runs as (default 1000:1000)
      # PGID: "1000"
      # VISUALSTORE_COMPUTE_PROCESSES: "2"  # compute children; default = CPU count
      # FORWARDED_ALLOW_IPS: "172.18.0.2"   # your reverse proxy, for login rate limiting
    volumes:
      - ./data:/data            # local disk only, never a network share
      - ./backups:/backups
      - /volume1/Images:/media/images
      - /volume1/Photos:/media/photos
```

Rules that matter:

- **`/data` must be on a local disk.** SQLite locking on SMB/NFS is unreliable.
- **Media container paths are part of your data.** Folders are stored with their container path
  (`/media/images`), so every machine that hosts the data directory must mount the media at the
  same container paths (the host side may differ). See [Initialise and migrate](initialise-and-migrate.md).
- **User and permissions.** The container runs as `PUID:PGID` (default 1000:1000). On first
  start an empty `/data` or `/backups` is given to that user; a non-empty one is left alone, so
  set `PUID`/`PGID` to the owner of existing directories. If a directory is not writable the
  container exits immediately with a message naming it.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VISUALSTORE_PASSWORD` | required | The single login password. Changing it logs every session out. |
| `PUID` / `PGID` | 1000 / 1000 | User and group the server runs as. |
| `VISUALSTORE_COMPUTE_PROCESSES` | CPU count | Parallel compute children (inference, hashing). Lower it on a NAS to leave CPU for other services; each child holds one ONNX session (about 300 MB for SSCD). |
| `VISUALSTORE_PORT` | 8080 | Listening port inside the container. |
| `VISUALSTORE_DATA_DIR` / `VISUALSTORE_BACKUP_DIR` | `/data` / `/backups` | Rarely changed; the volumes map to them. |
| `VISUALSTORE_MODEL_DIR` | `/app/models` | Only for custom model exports (see [Settings](settings.md#model)). |
| `FORWARDED_ALLOW_IPS` | unset | Address of your reverse proxy so the login limiter sees real client addresses. |

## Reverse proxy and HTTPS

VisualStore speaks plain HTTP. Terminate TLS in a reverse proxy, do not expose port 8080
directly, and set `FORWARDED_ALLOW_IPS` to the proxy's address.

## Health

`GET /api/system/health` needs no login and answers `200 {"status":"ok"}` while the job
scheduler ticks, `503 {"status":"stalled"}` when it has not for 60 s. The image's HEALTHCHECK
probes it every 30 s, so `docker ps` shows `healthy` / `unhealthy`.

## Logs and upgrades

`docker compose logs -f visualstore` shows start-up checks, requeues, failures and dead compute
children; routine job progress is on the Jobs page, not in the log.
To upgrade: `docker compose pull && docker compose up -d`. Database migrations run on start;
the search index is rebuilt when it no longer matches the databases or the index settings
(thumbnails and previews are kept, or rendered again on demand).
