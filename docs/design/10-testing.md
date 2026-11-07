# 10 · Test Strategy

- **domain unit tests**: union-find grouping and the effect of judged edges; the four cases of the default resolution rules; the three compare-scope levels; band classification; move-detection matching.
- **db layer tests**: migration from an empty database to the latest version; cascading deletes; the NOCASE unique index; creating a profile creates its databases and directories, deleting one removes the whole directory.
- **multi-profile isolation tests**: two profiles with a same-named Folder and same-path files; scan, match, actions, and backup in one profile leave the other untouched; compute results land in the correct profile.
- **media tests**: generated small images (solid colour, gradient, noise, plus scaled / recompressed / cropped versions) verify decoding, frame sampling, vector dimension, and normalisation; for video, generate a few-second synthetic clip with ffmpeg.
- **integration tests**: build a small two-Folder library in a temp directory, run scan → fingerprint → match → regroup, assert groups and preselection; then run apply_action and verify the trash directory and restore.
- **migration test**: after the integration flow, copy `/data` to another temp directory, start with the new data directory location, and verify that every query and an incremental scan give the same results.
- **evaluation job**: not an automated test but a milestone 3 deliverable that produces a report.
- **end to end**: log in, create a profile, configure a Folder, trigger a scan, resolve one group, see the job finish.


## The test image

The host needs libvips and ffmpeg for the media tests. Without them, run the suite in the test
stage of the image, which has both and the same Python environment as production:

```
BUILDX_CONFIG=/tmp/vs-buildx docker build --target test -f docker/Dockerfile -t visualstore:test .
docker run --rm -e VISUALSTORE_TEST_REQUIRE_MEDIA=1 \
  -v "$PWD/visualstore:/app/visualstore:ro" -v "$PWD/tests:/app/tests:ro" \
  visualstore:test python -m pytest -p no:cacheprovider tests -q --ignore=tests/integration/test_entrypoint.py
```

The Playwright run (`cd web && npm run e2e`) starts the server from that image automatically when
the host lacks the media libraries, mounting the working tree, so a fresh `npm run build` is
picked up without rebuilding the image. `tests/integration/test_entrypoint.py` runs the image's
entrypoint and skips when the image is absent; CI runs the same check in its image job.
