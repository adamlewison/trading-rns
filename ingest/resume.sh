#!/usr/bin/env bash
# Idempotent "make sure Phase 1 is running" script, safe to call from a fresh container.
#  1. no local DB -> restore from backup (if one exists) -> else start from scratch
#  2. start the full scrape if not running and not finished
#  3. start the hourly backup loop if not running
set -u
cd "$(dirname "$0")/.."
mkdir -p data/logs
[ -d .venv ] || uv sync -q
if [ ! -f data/rns.duckdb ]; then
  echo "[resume] no local DB: restoring from backup"
  if ! uv run python -W ignore -m core.backup pull; then
    echo "[resume] restore failed / no backup: rebuilding universe+prices from scratch"
    uv run python -W ignore -m ingest.universe build
    uv run python -W ignore -m ingest.prices download
  fi
fi
if grep -q "PHASE1 FULL RUN COMPLETE" data/logs/full_run.out 2>/dev/null; then
  echo "[resume] full run already complete"
elif ! pgrep -f run_full.sh >/dev/null; then
  echo "[resume] starting full run"
  nohup ingest/run_full.sh >> data/logs/full_run.out 2>&1 &
fi
if ! pgrep -f backup_loop.sh >/dev/null; then
  echo "[resume] starting backup loop"
  nohup ingest/backup_loop.sh >> data/logs/backup_loop.out 2>&1 &
fi
