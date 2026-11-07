#!/usr/bin/env bash
# Push an incremental backup every backup.interval_min minutes (and once more at exit of the scrape).
cd "$(dirname "$0")/.."
MIN=$(uv run python -c "from core.config import load_config; print(load_config()['backup']['interval_min'])" 2>/dev/null || echo 60)
while true; do
  uv run python -W ignore -m core.backup push || echo "[backup] push failed, will retry"
  grep -q "PHASE1 FULL RUN COMPLETE" data/logs/full_run.out 2>/dev/null && ! pgrep -f run_full.sh >/dev/null && \
    { uv run python -W ignore -m core.backup push; echo "[backup] final push done"; exit 0; }
  sleep $((MIN * 60))
done
