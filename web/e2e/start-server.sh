#!/usr/bin/env bash
# Builds the frontend and starts the server on a throwaway data directory for Playwright.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."
npm --prefix web run build
tmp="$(mktemp -d)"
mkdir -p "$tmp/media/lib/sub"
printf 'identical-bytes' > "$tmp/media/lib/a.jpg"
printf 'identical-bytes' > "$tmp/media/lib/b.jpg"
printf 'different' > "$tmp/media/lib/sub/c.jpg"
printf '%s' "$tmp/media/lib" > "$HERE/.media-path"
export VISUALSTORE_PASSWORD=e2e-password
export VISUALSTORE_DATA_DIR="$tmp/data"
export VISUALSTORE_BACKUP_DIR="$tmp/backups"
export VISUALSTORE_PORT=8099
export VISUALSTORE_COMPUTE_PROCESSES=1
exec uv run python -m visualstore
