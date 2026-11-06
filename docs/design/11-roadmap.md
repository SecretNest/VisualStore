# 11 · Roadmap, Risks, Optional Work

## 13. Milestones

Every milestone ends in a running version. Each one gets its own implementation plan under [`docs/plans/`](../plans/README.md).

1. **Skeleton**: repository, multi-arch Docker build, FastAPI, profile registry with per-profile database creation, migrations, Setting, env-password login, job table and scheduler, process pool skeleton, backup job, frontend shell (login, profile management and switching, overview page).
2. **Byte-identical pipeline**: Folder / Extension / IgnoredDirectory management, scan (with move detection), SHA-1 fingerprinting, sha1 Pairs, grouping, default rules, group detail page (without image comparison), apply_action with trash, archive_move, v1 import. At this point VisualStore replaces v1's same-file workflow.
3. **Similarity engine**: model export and ONNX inference, vector fingerprints, thumbnails, usearch index, match job, band settings, eval job and report, model lock-in.
4. **Similarity review**: image comparison and keyboard shortcuts in group detail, review queue filters and bulk rules, candidate pairs on the file detail page.
5. **Video and animated images**: frame sampling, multi-frame index, frame strip in group detail.
6. **Wrap-up**: performance tuning (index quantisation, PCA option), maintenance job, user documentation (including initialisation and migration), migration and restore rehearsal.

All six milestones shipped between 2026-11-06 and 2026-11-07; the plans under `docs/plans/` are frozen and the implementation choices are in ADR-0003 to ADR-0008.

## 14. Risks and open verification

| Risk | Response |
|---|---|
| SSCD may not perform on CG / illustration as well as on photographs | Run DINOv2 alongside in the milestone 3 evaluation, compare with imported labels, then lock in. |
| NAS memory cannot hold the index | Three fallbacks, all settings: i8 quantisation, mmap view, PCA reduction. |
| Same-directory variants crowd out top-k | usearch filtered query first, larger k as fallback; construct this scenario in an integration test. |
| Reading a million files over SMB is slow | Fingerprint jobs are batched in directory order for sequential reads; full pool on the initialisation machine. Scanning reads directory entries only, never content. |
| v1 HiddenAndDisconnected labels are noisy | Treat as reference only in evaluation; rely mainly on random clean negatives. |
| New model still has high false positives, heavy manual review | Start the optional work in §15 and let v1 judgments resolve part of it automatically. |
| heif / avif support in the arm64 image's libvips | Verified during the first milestone 1 publish (2026-11-06): Debian bookworm libvips 8.14 reports `heifload yes` and `jxlload yes` on both linux/amd64 and linux/arm64. Risk closed; the ffmpeg fallback is not needed. |
| Automatic deletion of byte-identical groups is irreversible and destructive | Default to the trash directory, not permanent deletion; automatic execution can be turned off in settings. |
| Paths inconsistent after migration | Folder paths are container paths; keep mount points identical in both compose files; check each Folder's existence at start-up and show a prominent warning on the overview page. |
| Remote compute needed later | The boundary between the job table and compute is already clean; adding an HTTP endpoint for claiming jobs would not touch anything else. Not done now. |

## 15. Optional follow-up: resolve similar pairs with v1 judgments

**Trigger**: the milestone 3 evaluation shows the new model's false-positive rate in the near_duplicate band is still high and reviewing everything from scratch is unacceptable. If the evaluation is acceptable, skip this section.

**Content**:

- `import_v1` gains a switch "import similarity judgments" that converts v1 SimilarFile rows into judged Pairs as described in [08 §10](08-v1-import.md#10-import-job).
- These judgments act like user judgments during grouping: `unrelated` cuts an edge, `related_keep` keeps the edge without action. Pairs already judged in v1 therefore never reach the review queue.
- A new **judgment review queue** page lists pairs imported as `unrelated` that the new model rates near_duplicate, so bulk mislabels can be recovered. A changed judgment takes effect in grouping immediately.
- The review queue can filter by `judgment_origin` to separate "judged in v1" from "newly found".

**Not done**: v1 pHash distances are not used, and no fused score across the two models is built. v1's value lies in the human judgments, not its algorithm output.
