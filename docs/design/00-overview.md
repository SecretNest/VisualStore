# 00 · Overview: Background, Goals, Acceptance

Status: design confirmed with the user; implementation may start from it.
Origin: the ImageStore v2 design finalized on 2026-11-02 in the v1 repository. The project was renamed VisualStore (see [ADR-0001](../decisions/0001-rename-to-visualstore.md)).
Replaces: the ImageStore PowerShell module, referred to below as **v1**. Its repository sits next to this one at `../ImageStore/`.

---

## 0. Notes for whoever picks this up

These documents are the requirements and design settled after several rounds of discussion in the v1 repository. VisualStore is implemented from scratch in this repository. Reading order: this page and [02 Architecture](02-architecture.md) first, then the rest as needed. The full list is in [docs/README.md](../README.md).

**Already decided. Do not ask the user again**: the technology stack ([02 §4](02-architecture.md#4-technology-stack)), the single-container architecture and the migration procedure ([02 Architecture](02-architecture.md)), the direction of the similarity engine ([05 §7.6](05-pipeline.md#76-model-and-thresholds)), multiple profiles, password read only from an environment variable, deletion going to a trash directory by default, v1 similarity judgments not being a default dependency ([11 §15](11-roadmap.md#15-optional-follow-up-resolve-similar-pairs-with-v1-judgments)). Every "default" in these documents is a trade-off the user has confirmed.

**Repository matters already settled**: name VisualStore, MIT license, all documentation in English ([ADR-0002](../decisions/0002-documentation-structure.md)), English-only UI, images published manually from GitHub Actions to GHCR and Alibaba Cloud ACR ([ADR-0003](../decisions/0003-m1-implementation-choices.md)). No open questions remain before milestone 1.

**Next step**: expand milestone 1 of [11 §13](11-roadmap.md#13-milestones) into an implementation plan under `docs/plans/`, have the user confirm it, then implement. Every milestone must end in a version that runs.

**v1 reference material**: read `../ImageStore/` directly when v1 behaviour matters. [08 Appendix B](08-v1-import.md#appendix-b-files-worth-reading-in-the-v1-repository) lists the files worth reading. Do not port v1 code into this repository. What v1 contributes is domain rules and data.

**Terminology**: "the user" is the single person who uses this tool and who stated the requirements. A **Folder** (capitalised) is a v1 term for one media library root, not an arbitrary filesystem directory. **Media** covers the three kinds of files handled here: still images, animated images, and video.

---

## 1. Background and goals

v1 is a PowerShell binary module. It finds byte-identical files with SHA-1, visually similar images with pHash, and offers WinForms windows for manual review. It has been used for years on a CG library of about a million files and exposed four problems:

1. pHash misfires often: two entirely different pictures are frequently rated highly similar; the difference score is not interpretable; false negatives cannot be measured.
2. The flexibility of composing PowerShell cmdlets is almost never used. The real workflow is fixed: ingest, compute, find duplicates, resolve, move to archive.
3. Format support is narrow: video (webm / mp4 / mkv) takes no part in similarity, animated images contribute only their first frame, modern still formats are missing.
4. Windows only.

VisualStore is a rewrite, not a migration: it keeps v1's domain rules and data and replaces the implementation. The rename follows directly from point 3. Video and animated images join still images in one similarity pipeline, and "Image" no longer describes what the tool handles.

### 1.1 Goals

- One Docker container running permanently on a NAS or home server, media directories mounted as volumes, operated from a browser.
- Turn the fixed workflow into a product: ingest → compute → find duplicates → resolve → archive, all clickable in the browser.
- Replace similarity detection with deep copy-detection features plus vector nearest-neighbour search, cutting the "entirely different pictures rated similar" class of error substantially. Similarity scores have stable, interpretable bands so groups can be processed in bulk per band.
- Byte-identical and visually similar duplicates share one pipeline and one review UI.
- Still images, animated images, and video all take part in similarity, and stills can match video frames.
- One-time import of the v1 database: Folder configuration, files, SHA-1 hashes, byte-identical groups. Do not start from zero. v1 similarity judgments serve only as an evaluation reference and are not a default dependency (see [05 §7.6](05-pipeline.md#76-model-and-thresholds) and [11 §15](11-roadmap.md#15-optional-follow-up-resolve-similar-pairs-with-v1-judgments)).
- The data directory is portable as a whole: the first full computation runs on a powerful machine, the data directory is copied verbatim to the NAS, and incremental work continues there.
- Scheduled database backups to a fixed directory, keeping the most recent N copies.
- Multiple profiles: each profile has its own database, settings, and Folder set for an unrelated media collection. One service instance hosts all profiles.

### 1.2 Non-goals

- Multiple users, permissions, or hardening for public exposure. Password login only; HTTPS is the reverse proxy's job.
- Distributed computation across machines. Compute and web live in one container. The "initialise on a strong machine, then move" procedure covers the first full run.
- General photo-album features: tags, search, browsing. VisualStore only deduplicates.
- Semantic retrieval ("find every picture with a cat").

### 1.3 Acceptance criteria

- At the one-million-file scale, the routine incremental workflow (a few thousand new files) finishes overnight on the NAS and the UI stays responsive.
- Evaluated against the human judgments imported from v1, the new algorithm's recall on "confirmed related" samples is not below pHash, and its false-positive rate on "confirmed unrelated" samples is substantially below pHash. Concrete numbers are written back here once the evaluation in [05 §7.6](05-pipeline.md#76-model-and-thresholds) establishes the baseline.
- Byte-identical groups need no human intervention by default.
- Resolving one similarity group by hand takes at most three keystrokes (look, choose what to keep, confirm).
- After the data directory moves from the initialisation machine to the NAS, the service works without any repair step.
