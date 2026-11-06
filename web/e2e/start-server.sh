#!/usr/bin/env bash
# Builds the frontend and starts the server on a throwaway data directory for Playwright.
set -euo pipefail
cd "$(dirname "$0")/../.."
npm --prefix web run build
tmp="$(mktemp -d)"
export VISUALSTORE_PASSWORD=e2e-password
export VISUALSTORE_DATA_DIR="$tmp/data"
export VISUALSTORE_BACKUP_DIR="$tmp/backups"
export VISUALSTORE_PORT=8099
export VISUALSTORE_COMPUTE_PROCESSES=1
exec uv run python -m visualstore
