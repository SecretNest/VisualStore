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
 && apt-get install -y --no-install-recommends libvips42 ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# dependencies first, so source edits do not invalidate this layer
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY visualstore/ ./visualstore/
COPY --from=web /src/visualstore/static ./visualstore/static
COPY README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-editable

COPY docker/check-media-support.py ./docker/
RUN /app/.venv/bin/python docker/check-media-support.py

ENV PATH="/app/.venv/bin:$PATH" \
    VISUALSTORE_DATA_DIR=/data \
    VISUALSTORE_BACKUP_DIR=/backups \
    VISUALSTORE_PORT=8080
VOLUME ["/data", "/backups"]
EXPOSE 8080
CMD ["python", "-m", "visualstore"]

# ---- test stage: runtime plus dev dependencies, project installed editable so the source
# tree can be bind-mounted over /app/visualstore and /app/tests to run the suite with libvips.
FROM runtime AS test
RUN uv sync --frozen
CMD ["python", "-m", "pytest", "-q"]
