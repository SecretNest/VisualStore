#!/usr/bin/env bash
# Builds the frontend and starts the server on a throwaway data directory for Playwright.
# The media are real PNGs and the active model is a tiny local-only ONNX model, so the full
# pipeline (decode → embed → thumbnail → match → group) runs. libvips is required; when it is
# not installed on this machine the server runs inside the `visualstore:test` image instead.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."
npm --prefix web run build
tmp="$(mktemp -d)"
mkdir -p "$tmp/media/lib/sub" "$tmp/models" "$tmp/data" "$tmp/backups"
uv run python - "$tmp" <<'PY'
import json, struct, sys, zlib
from pathlib import Path
from tests.fixtures.tiny_model import build_tiny_model

tmp = Path(sys.argv[1])

def png(rgb, size=64):
    raw = b"".join(b"\0" + bytes(rgb) * size for _ in range(size))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

(tmp / "media/lib/a.png").write_bytes(png((255, 0, 0)))
(tmp / "media/lib/b.png").write_bytes(png((255, 0, 0)))  # byte-identical to a.png
(tmp / "media/lib/sub/c.png").write_bytes(png((0, 255, 0)))  # far from red under the tiny model
build_tiny_model(tmp / "models/tiny-test-8-v1.onnx")
(tmp / "models/models.json").write_text(json.dumps({
    "tiny-test-8-v1": {"dim": 8, "input_size": 16, "mean": [0, 0, 0], "std": [1, 1, 1],
                       "description": "e2e model"}}))
PY
printf '%s' "$tmp/media/lib" > "$HERE/.media-path"

export VISUALSTORE_PASSWORD=e2e-password
export VISUALSTORE_DATA_DIR="$tmp/data"
export VISUALSTORE_BACKUP_DIR="$tmp/backups"
export VISUALSTORE_MODEL_DIR="$tmp/models"
export VISUALSTORE_PORT=8099
export VISUALSTORE_COMPUTE_PROCESSES=1

if uv run python -c "import pyvips" 2>/dev/null; then
  exec uv run python -m visualstore
fi
if ! docker image inspect visualstore:test >/dev/null 2>&1; then
  echo "libvips is not installed and the visualstore:test image is missing." >&2
  echo "Build it: BUILDX_CONFIG=/tmp/vs-buildx docker build --target test -f docker/Dockerfile -t visualstore:test ." >&2
  exit 1
fi
echo "libvips not found locally; running the server in visualstore:test" >&2
name=vs-e2e-$$
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap 'cleanup; exit 0' TERM INT
trap cleanup EXIT
# Playwright kills the whole process group when the run ends; a watchdog in its own session
# outlives that and removes the container once this script is gone.
setsid bash -c 'while kill -0 "$1" 2>/dev/null; do sleep 1; done; docker rm -f "$2" >/dev/null 2>&1' _ $$ "$name" &
docker run --rm --name "$name" --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -p 127.0.0.1:8099:8099 \
  -v "$PWD/visualstore:/app/visualstore:ro" -v "$tmp:$tmp" \
  -e VISUALSTORE_PASSWORD -e VISUALSTORE_DATA_DIR -e VISUALSTORE_BACKUP_DIR \
  -e VISUALSTORE_MODEL_DIR -e VISUALSTORE_PORT -e VISUALSTORE_COMPUTE_PROCESSES \
  visualstore:test python -m visualstore &
wait $!
