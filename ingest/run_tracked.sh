#!/usr/bin/env bash
# Foreground wrapper so the harness tracks the job (keeps the session active):
# ensures the backup loop is running, then runs the full scrape in the foreground.
cd "$(dirname "$0")/.."
pgrep -f backup_loop.sh >/dev/null || nohup ingest/backup_loop.sh >> data/logs/backup_loop.out 2>&1 &
exec ingest/run_full.sh >> data/logs/full_run.out 2>&1
