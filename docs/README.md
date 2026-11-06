# VisualStore Documentation

This directory is the single documentation tree of the project. Nothing about the design, decisions, or plans lives anywhere else (no `docs/superpowers/`, no scratch files in the repository root; see [ADR-0002](decisions/0002-documentation-structure.md)).

| Directory | Contents | When it changes |
|---|---|---|
| [`design/`](design/) | The design specification, split into numbered chapters. The single source of truth for what is being built and why. | When a design decision changes. Update the chapter; record the reason as an ADR. |
| [`decisions/`](decisions/) | Architecture Decision Records: short notes on decisions whose rationale is not obvious from the design itself. | Append-only. Superseded decisions are marked, not deleted. |
| [`plans/`](plans/) | Implementation plans, one per milestone, written before coding that milestone starts. | One new file per milestone; a plan is frozen once its milestone ships. |
| `user/` | End-user documentation: deployment, initialisation and migration, restore, v1 import, review workflow. Created in milestone 6 of the [roadmap](design/11-roadmap.md). | Not yet present. |

## Reading order for the design

1. [00 Overview](design/00-overview.md): background, goals, non-goals, acceptance criteria, and notes for whoever picks the project up.
2. [02 Architecture](design/02-architecture.md): the single-container model, initialisation-then-migrate procedure, technology stack.
3. [05 Core Pipeline](design/05-pipeline.md): scan → fingerprint → match → regroup, model and threshold bands.
4. Then as needed:
   - [01 Requirements Baseline](design/01-requirements.md)
   - [03 Domain Model and Database](design/03-data-model.md)
   - [04 Job System](design/04-jobs.md)
   - [06 Manual Review and UI](design/06-review-ui.md)
   - [07 Operations](design/07-operations.md): backup, authentication, configuration, compose, resources
   - [08 v1 Data Import](design/08-v1-import.md): import rules, v1 schema, reading pitfalls, v1 files worth reading
   - [09 Project Layout](design/09-project-layout.md)
   - [10 Test Strategy](design/10-testing.md)
   - [11 Roadmap, Risks, Optional Work](design/11-roadmap.md)

Section numbers inside the chapters (§1 to §15, Appendix A and B) are kept from the original single-file design so that cross-references in older discussion still resolve.

## Conventions

- Language: English throughout.
- A chapter states decisions, not options. Open questions are listed explicitly in [00 Overview §0](design/00-overview.md#0-notes-for-whoever-picks-this-up) and removed once answered.
- Code identifiers, paths, environment variables, and table columns are written in backticks exactly as they will appear in code.
