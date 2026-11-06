# VisualStore

Self-hosted deduplication for large image, animated-image, and video libraries. One Docker container, a browser UI, and a review workflow built for collections of around a million files.

VisualStore is the successor to [ImageStore](https://github.com/SecretNest/ImageStore), a Windows PowerShell module that found byte-identical files with SHA-1 and visually similar images with pHash. VisualStore keeps ImageStore's domain rules and can import its database, but is a rewrite: cross-platform, browser-operated, and with a similarity engine that treats stills, animated images, and video frames in one vector space.

> **Status: milestone 2 complete.** VisualStore replaces ImageStore's same-file workflow: Folders,
> scanning with move detection, SHA-1 groups with preselected decisions, trash with restore, archive
> move and v1 import all run from the browser. Visual similarity arrives in milestone 3. See the
> [roadmap](docs/design/11-roadmap.md).

## What it does

- **Finds duplicates two ways in one pipeline.** Byte-identical files (SHA-1) and visually similar media (deep copy-detection features with nearest-neighbour search over a vector index) land in the same groups and the same review UI.
- **Compares across media types.** Still images, animated images, and video all take part. A screenshot can match the frame of a video it came from.
- **Scores you can act on in bulk.** Similarity falls into stable bands: identical, near-duplicate, variant, candidate. Byte-identical groups resolve automatically; near-duplicates are preselected for one-key confirmation; variants get a human look.
- **Is built for CG libraries.** A Folder can be told not to compare files within the same directory, so intentional variants are not flagged against each other. Folders have an archive or inbox role that drives which copy is kept by default.
- **Keeps the fixed workflow short.** Ingest → compute → find duplicates → resolve → move to archive, all in the browser. Resolving a group takes about three keystrokes.
- **Does not lose work.** Deletion goes to a trash directory with a retention period. Moving or renaming files on disk is detected on the next scan and keeps hashes and judgments. Scheduled SQLite backups keep the most recent N copies.
- **Runs where the files are.** A single container on a NAS or home server with media mounted as volumes. The first full computation can run on a faster machine and the data directory then moves to the NAS as-is.
- **Hosts several libraries.** Profiles isolate databases, settings, and Folders for unrelated collections inside one service instance.

## Architecture in brief

```
browser ──HTTP──► server (FastAPI + static React frontend)
                   │  API, scan, file actions, grouping, backup, scheduling
                   └─► compute process pool
                        SHA-1, decode, frame sampling, ONNX inference, thumbnails, index queries

/data      SQLite metadata and vectors per profile, usearch index, thumbnails
/backups   timestamped SQLite backups
/media/... your libraries, mounted read-write
```

One image, one container, one process tree. The server is the only database writer; compute children receive work over in-process queues. No external broker, no database server.

Planned stack: Python 3.12, FastAPI, SQLite (WAL), ONNX Runtime, pyvips, ffmpeg, usearch, React + TypeScript + Vite. Multi-arch image for linux/amd64 and linux/arm64. Details in [Architecture](docs/design/02-architecture.md).

## Configuration

| Variable | Description |
|---|---|
| `VISUALSTORE_PASSWORD` | Login password. Required. |
| `VISUALSTORE_DATA_DIR` | Data directory, default `/data`. |
| `VISUALSTORE_BACKUP_DIR` | Backup directory, default `/backups`. |
| `VISUALSTORE_COMPUTE_PROCESSES` | Compute children, default CPU count. |
| `VISUALSTORE_PORT` | Listen port, default 8080. |

Everything else is a per-profile setting in the UI. A compose example is in [Operations](docs/design/07-operations.md#94-container-and-compose). Images will be published to `ghcr.io/secretnest/visualstore` and to Alibaba Cloud Container Registry under the same path.

## Running

```bash
cd docker
cp ../.env.example .env          # set VISUALSTORE_PASSWORD in docker/.env
docker compose up -d             # data in docker/data, backups in docker/backups
```

Compose resolves `.env` and the relative volume paths against the directory that holds
`compose.yaml`, so run it from `docker/` (or copy `compose.yaml` and `.env` to a directory of
your own). Edit the media volumes in `compose.yaml` first; keep the container-side paths the same
on every machine that will host the data directory.

Images: `ghcr.io/secretnest/visualstore` and the same `secretnest/visualstore` path on Alibaba
Cloud Container Registry.

**Models.** The image ships both similarity models in `/app/models` (SSCD ResNet-50, the
default, and DINOv2 ViT-S/14 for comparison; about 190 MB together). They are fetched from this
repository's `models-v1` release and verified by sha256 when the image is built, so nothing is
downloaded at run time. Set `VISUALSTORE_MODEL_DIR` only to bring your own exports: that directory
must then hold every `.onnx` you use, and a `models.json` in it registers custom models as
local-only entries (see [ADR-0005](docs/decisions/0005-m3-implementation-choices.md)); a built-in
model missing from it is downloaded from the release on first use and verified. Switching the
active model (Jobs → Model) computes the missing vectors and rebuilds the index; judgments and
groups stay. To evaluate a model against your ImageStore judgments, run Jobs → Evaluation with
the path of the v1 `.db`; the report lands in `/data/profiles/<profile>/reports/` and is linked
from the same page.

**Reviewing.** The Groups page lists open groups filtered by status, band and Folder. Group
detail compares the selected member with its nearest neighbour side by side (`s`), as an overlay
with an opacity slider (`o`) or blinking (`b`); `n`/`p` pick another member to compare with and
Space swaps the two; `k`/`d` mark keep/delete, `Enter` confirms and moves to the next group, `Esc`
skips. The file detail page lists candidate pairs beyond the grouping threshold with "related" /
"unrelated" judgments, and offers move and rename. "Default rules for this filter" on the Groups
page previews and executes the default resolution for every open group matching the filter in one
job; when more files than `bulk.confirm_threshold` would be deleted, the count must be typed back.

**Behind a reverse proxy.** HTTPS is the proxy's job; VisualStore speaks plain HTTP and its
session cookie is not marked `Secure`, so terminate TLS in front of it and do not expose port
8080 directly. Set `FORWARDED_ALLOW_IPS` to the proxy's address (uvicorn reads it from the
environment) so that login rate limiting sees each client's own address instead of the proxy's.
Without it, five wrong passwords from anyone lock everyone out for a minute.

**Restoring a backup.** Backups are plain SQLite files under `/backups/<profile>/`
(`visualstore-<timestamp>.db` is the metadata database, `vectors-<timestamp>.db` the vectors) and
`/backups/global.db`. To restore one profile:

1. Stop the container.
2. Copy the chosen `visualstore-<timestamp>.db` over `/data/profiles/<profile>/visualstore.db`,
   and, if needed, `vectors-<timestamp>.db` over `vectors.db` in the same directory.
3. Start the container. The search index and thumbnails are derived data and are rebuilt when
   they no longer match the restored databases. Other profiles are unaffected.

Moving the whole installation to another machine is not a restore: stop the container and copy
the entire `/data` directory as-is (see [Operations](docs/design/07-operations.md#91-backup)).

Development: `uv sync`, `cd web && npm ci && npm run build`, then
`VISUALSTORE_PASSWORD=pw VISUALSTORE_DATA_DIR=./data VISUALSTORE_BACKUP_DIR=./backups uv run python -m visualstore`.
Tests: `uv run pytest`, `cd web && npm test`, `cd web && npm run e2e`.

## Documentation

Everything lives under [`docs/`](docs/README.md):

- [`docs/design/`](docs/design/): the specification, by chapter. Start with [Overview](docs/design/00-overview.md).
- [`docs/decisions/`](docs/decisions/): architecture decision records.
- [`docs/plans/`](docs/plans/): implementation plans, one per milestone.

## Roadmap

1. Skeleton: container, profiles, migrations, login, job system, backup, frontend shell.
2. Byte-identical pipeline with ImageStore import. Replaces ImageStore's same-file workflow.
3. Similarity engine: ONNX inference, vector index, model evaluation.
4. Similarity review UI: image comparison, shortcuts, bulk rules.
5. Video and animated images.
6. Performance, maintenance, user documentation, migration rehearsal.

## Migrating from ImageStore

The ImageStore SQLite database imports in one step: Folder configuration, files, SHA-1 hashes, and byte-identical groups are kept, including their ids. pHash values are dropped and similarity is recomputed with the new model. ImageStore's manual similarity judgments are used to evaluate the new model and can optionally be imported. See [v1 Data Import](docs/design/08-v1-import.md).

## License

[MIT](LICENSE)
