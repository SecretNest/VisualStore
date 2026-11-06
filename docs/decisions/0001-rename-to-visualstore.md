# ADR-0001 · Rename the project from ImageStore v2 to VisualStore

Date: 2026-11-06
Status: accepted

## Context

The design was written as "ImageStore v2", a rewrite of the ImageStore PowerShell module. One of its stated goals is that video and animated images join still images in a single similarity pipeline, with frames of a video comparable against stills ([00 Overview §1](../design/00-overview.md#1-background-and-goals)). "Image" no longer describes the set of files the tool handles.

The rewrite also lives in a new repository with no shared code, so a name change costs nothing in compatibility.

## Decision

The project, repository, Python package, Docker image, and all user-facing identifiers are named **VisualStore**. The predecessor is referred to as **ImageStore** or **v1**; the term "v2" is not used for VisualStore.

Concrete renames applied to the design, all from the original `imagestore` spelling:

| Original | VisualStore |
|---|---|
| Python package `imagestore/` | `visualstore/` |
| Environment variables `IMAGESTORE_*` | `VISUALSTORE_*` |
| Per-profile metadata database `imagestore.db` | `visualstore.db` |
| Trash directory `.imagestore-trash/` | `.visualstore-trash/` |
| Container / compose service `imagestore` | `visualstore` |

Unchanged: the mount convention `/data`, `/backups`, `/media/...`; the v1 database schema and file names, which are read as-is during import.

## Consequences

- Every design chapter uses the new names. A reader of older discussion notes must map `imagestore` → `visualstore` mentally; the section numbering was kept to make this easy.
- Media type names (`image` / `animated` / `video` / `other`) keep the word "image" because there it is accurate.
- The v1 import job must still parse v1 cmdlet and table names; those are not renamed anywhere.
