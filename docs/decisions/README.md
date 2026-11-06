# Architecture Decision Records

One file per decision, numbered in order of creation: `NNNN-short-title.md`.

Each record has four parts: **Context** (what forced the decision), **Decision** (what was chosen), **Consequences** (what follows, good and bad), **Status** (accepted / superseded by NNNN).

Records are never rewritten to say something different. A changed decision gets a new record that supersedes the old one.

| # | Title | Status |
|---|---|---|
| [0001](0001-rename-to-visualstore.md) | Rename the project from ImageStore v2 to VisualStore | accepted |
| [0002](0002-documentation-structure.md) | One documentation tree, in English, under `docs/` | accepted |
| [0003](0003-m1-implementation-choices.md) | Milestone 1 implementation choices: sqlite3, SQL migrations, uv, English UI, GHCR + ACR publishing | accepted |
| [0004](0004-m2-implementation-choices.md) | Milestone 2 implementation choices: result sinks, dirty queue, trash layout, import rules | accepted |
| [0005](0005-m3-implementation-choices.md) | Milestone 3 implementation choices: model download, plain usearch keys, scope in the sink, eval as io | accepted |
| [0006](0006-m4-implementation-choices.md) | Milestone 4 implementation choices: comparison modes, neighbours in detail, cached previews, bulk job, move/rename as actions | accepted |
| [0007](0007-m5-implementation-choices.md) | Milestone 5 implementation choices: frames over a pipe, ffprobe for timing, pyvips pages, APNG via ffmpeg, retired frame keys | accepted |
