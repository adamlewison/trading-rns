#!/usr/bin/env bash
# Push an incremental backup of one market every backup.interval_min minutes (default 30).
#     deploy/backup-loop.sh uk
#     deploy/backup-loop.sh us --prune-raw
set -u
cd "$(dirname "$0")/.."
MARKET=${1:?usage: backup-loop.sh uk|us [push options]}; shift
MIN=$(uv run python -c "from core.config import load_config; print(load_config()['backup']['interval_min'])" 2>/dev/null || echo 30)
while true; do
  uv run python -W ignore -m core.backup push --profile "$MARKET" "$@" || echo "[backup $MARKET] push failed, will retry"
  sleep $((MIN * 60))
done
