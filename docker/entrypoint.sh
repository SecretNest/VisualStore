#!/usr/bin/env bash
# Drop to the PUID/PGID user (default 1000:1000). /data and /backups are chowned only when
# empty: an existing data directory is the operator's, never touched (ADR-0008).
set -euo pipefail
if [ "$(id -u)" != "0" ]; then
  exec "$@"
fi
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
if ! getent group "$PGID" >/dev/null; then groupadd -g "$PGID" visualstore; fi
if ! getent passwd "$PUID" >/dev/null; then useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin visualstore; fi
for dir in "${VISUALSTORE_DATA_DIR:-/data}" "${VISUALSTORE_BACKUP_DIR:-/backups}"; do
  mkdir -p "$dir"
  if [ -z "$(ls -A "$dir")" ]; then chown "$PUID:$PGID" "$dir"; fi
done
export HOME=/tmp  # the dropped user has no home; onnxruntime wants a writable one
exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups "$@"
