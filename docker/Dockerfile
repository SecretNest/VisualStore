# syntax=docker/dockerfile:1

# ---- frontend build --------------------------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# vite.config.ts writes to ../visualstore/static, i.e. /src/visualstore/static
RUN npm run build

# ---- runtime ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends libvips42 ffmpeg util-linux passwd \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# dependencies first, so source edits do not invalidate this layer
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# model artefacts: fetched from the models-v1 release and verified at build time, so the image
# works without internet access and nothing is downloaded on first use (ADR-0005)
ADD --chmod=644 --checksum=sha256:d8d800f9b2740eb625757abcdb2ba232399dece87448ed7005a0896d8dde64e0 \
    https://github.com/SecretNest/VisualStore/releases/download/models-v1/sscd-resnet50-512-v1.onnx \
    /app/models/sscd-resnet50-512-v1.onnx
ADD --chmod=644 --checksum=sha256:0dc96e18a60103b7267f45ca39a4828b236cdf64ef0894c0a4532ae86bd7a3d2 \
    https://github.com/SecretNest/VisualStore/releases/download/models-v1/dinov2-vits14-384-v1.onnx \
    /app/models/dinov2-vits14-384-v1.onnx
RUN chmod 755 /app/models

COPY visualstore/ ./visualstore/
COPY --from=web /src/visualstore/static ./visualstore/static
COPY README.md LICENSE ./
COPY scripts/ ./scripts/
RUN uv sync --frozen --no-dev --no-editable

COPY docker/check-media-support.py ./docker/
COPY --chmod=755 docker/entrypoint.sh ./docker/
RUN VISUALSTORE_MODEL_DIR=/app/models /app/.venv/bin/python docker/check-media-support.py

ENV PATH="/app/.venv/bin:$PATH" \
    VISUALSTORE_MODEL_DIR=/app/models \
    VISUALSTORE_DATA_DIR=/data \
    VISUALSTORE_BACKUP_DIR=/backups \
    VISUALSTORE_PORT=8080
VOLUME ["/data", "/backups"]
EXPOSE 8080
# Liveness: HTTP answers and the scheduler loop has ticked recently (ADR-0008); no curl in slim.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import os, sys, urllib.request; port = os.environ.get('VISUALSTORE_PORT', '8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/api/system/health', timeout=4).status == 200 else 1)"
# Runs as PUID:PGID (default 1000:1000); see docker/entrypoint.sh and ADR-0008.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "-m", "visualstore"]

# ---- test stage: runtime plus dev dependencies, project installed editable so the source
# tree can be bind-mounted over /app/visualstore and /app/tests to run the suite with libvips.
FROM runtime AS test
RUN uv sync --frozen
ENV VISUALSTORE_TEST_REQUIRE_MEDIA=1
CMD ["python", "-m", "pytest", "-q"]
