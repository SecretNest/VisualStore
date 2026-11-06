# CLAUDE.md

Guidance for agent sessions working in this repository.

## What this is

VisualStore: self-hosted deduplication for image, animated-image, and video libraries. Single Docker container, FastAPI backend with a compute process pool, React frontend, SQLite plus a usearch vector index. Successor to the ImageStore PowerShell module (`../ImageStore/`, referred to as v1).

Status: design complete, implementation not started. The design is the contract; read it before proposing anything.

## Documentation rules

- `docs/` is the only documentation tree. Read [docs/README.md](docs/README.md) first.
- Design specifications go in `docs/design/` (numbered chapters). Implementation plans go in `docs/plans/`, one per milestone. Decisions with non-obvious rationale go in `docs/decisions/` as ADRs.
- Do **not** create `docs/superpowers/` or any parallel documentation tree. When a workflow skill wants to write a spec or plan to its default location, write it to the locations above instead.
- All documentation in the repository is English. Conversation with the user may be in Chinese.

## Decisions already made (do not reopen)

Listed in [docs/design/00-overview.md §0](docs/design/00-overview.md#0-notes-for-whoever-picks-this-up). In short: technology stack, single-container architecture, initialise-then-migrate procedure, SSCD-first similarity engine with evaluation in milestone 3, multiple profiles, password from environment only, trash-by-default deletion, v1 similarity judgments not imported by default.

## Naming

The project is VisualStore everywhere: package `visualstore/`, env vars `VISUALSTORE_*`, metadata database `visualstore.db`, trash directory `.visualstore-trash/`. The predecessor is "ImageStore" or "v1", never "v2" for this project. See [ADR-0001](docs/decisions/0001-rename-to-visualstore.md).

## v1 reference

When v1 behaviour matters, read `../ImageStore/` directly; the files worth reading are listed in [docs/design/08-v1-import.md, Appendix B](docs/design/08-v1-import.md#appendix-b-files-worth-reading-in-the-v1-repository). Do not port v1 code; keep its domain rules and data.
