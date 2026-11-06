# Implementation Plans

One plan per milestone of the [roadmap](../design/11-roadmap.md#13-milestones), named `YYYY-MM-DD-m<N>-<short-title>.md` (for example `2026-11-10-m1-skeleton.md`).

A plan is written only after its milestone's design is confirmed, and it is reviewed by the user before any implementation starts. Once the milestone ships, the plan is frozen; corrections discovered later go into the design chapters or an ADR, not back into the plan.

Plans exist at two resolutions:

- **Executable**: every task carries its failing tests, its implementation code and its commit step (milestones 1 and 2). This is what an executor runs task by task.
- **Task-level**: decomposition, interfaces, tests to write, review focus and acceptance, but no code bodies (milestones 3 to 6). Each is expanded to executable resolution when its milestone starts, because its details depend on what the previous milestone produced (the evaluation in milestone 3 decides thresholds, for example). The "Decide before expanding" section of each lists the decisions still open and the recommendation.

| Milestone | Plan | Status |
|---|---|---|
| 1 Skeleton | [2026-11-06-m1-skeleton.md](2026-11-06-m1-skeleton.md) | shipped (merged to main 2026-11-06) |
| 2 Byte-identical pipeline | [2026-11-06-m2-byte-identical.md](2026-11-06-m2-byte-identical.md) | shipped |
| 3 Similarity engine | [2026-11-06-m3-similarity-engine.md](2026-11-06-m3-similarity-engine.md) | task-level |
| 4 Similarity review | [2026-11-06-m4-similarity-review.md](2026-11-06-m4-similarity-review.md) | task-level |
| 5 Video and animated images | [2026-11-06-m5-video-animated.md](2026-11-06-m5-video-animated.md) | task-level |
| 6 Wrap-up | [2026-11-06-m6-wrap-up.md](2026-11-06-m6-wrap-up.md) | task-level |
