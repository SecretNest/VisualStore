# ADR-0003 · Milestone 1 implementation choices

Date: 2026-11-06
Status: accepted

## Context

The design leaves a few implementation-level options open ([02 §4](../design/02-architecture.md#4-technology-stack): "SQLAlchemy Core or direct sqlite3", "Alembic or a hand-written version table") and two product questions were still marked as open in [00 §0](../design/00-overview.md#0-notes-for-whoever-picks-this-up). The milestone 1 plan needs them fixed.

## Decisions

- **Database access**: the standard library `sqlite3` module, no SQLAlchemy. One writer process, a handful of tables, and hand-written SQL keep the dependency list short and the queries visible.
- **Migrations**: numbered SQL files (`0001_init.sql`, `0002_...`) per database kind (global, profile metadata, vectors) applied by a small runner that records versions in a `schema_version` table. Each file runs in one transaction. No Alembic.
- **Packaging**: `uv` with `pyproject.toml` and a committed `uv.lock`; `hatchling` as build backend. The frontend build output is placed inside the Python package (`visualstore/static/`) so one wheel carries the whole application.
- **UI language**: English only. Strings live in the components; no runtime language switching and no translation framework.
- **Image publishing**: a manually dispatched GitHub Actions workflow builds `linux/amd64` and `linux/arm64` and pushes to two registries with the tags `<version>` and `latest`:
  - GHCR: `ghcr.io/secretnest/visualstore`
  - Alibaba Cloud Container Registry (personal edition): `<ACR_REGISTRY>/secretnest/visualstore`, where `ACR_REGISTRY`, `ACR_USERNAME`, `ACR_PASSWORD` are organisation secrets. The host currently in use is `crpi-xck5ot77uijk4gvl.cn-shenzhen.personal.cr.aliyuncs.com`.
  Provenance and SBOM attestations are turned off because ACR rejects their manifests, and the build cache lives in GHCR rather than the GitHub Actions cache. This mirrors the workflow of the sibling AzureStorageBackup project, which has already run into both problems.
- **Version source**: `[project].version` in `pyproject.toml`; the publish workflow reads it when no version is typed into the dispatch form.
- **CI**: on every push and pull request run ruff, pytest, the frontend type-check, vitest and the Vite build, and build the image for amd64 without pushing.

- **Threading rule for SQLite**: every connection owned by the `ProfileRegistry` is used only on the event-loop thread. API handlers and FastAPI dependencies are therefore `async def` (sync ones would run in a threadpool and share connections with the scheduler); io job handlers run in a worker thread and open their own connections from paths. A structural test (`tests/integration/test_api_async.py`) pins this.
- **Dead compute children**: a child announces which job it holds; the pool respawns dead children and the scheduler counts a failed attempt for the job they held, so an OOM during inference neither loses a pool slot nor leaves a job `running` forever.

## Consequences

- Any later need for an ORM or Alembic is a deliberate change, not a drift; this record would be superseded.
- Developers need `uv` and Node 22 locally; nothing else outside the container.
- Publishing is a human action after a green CI run; nothing publishes on push.
