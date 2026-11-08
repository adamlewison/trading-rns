#!/usr/bin/env bash
# One-time, idempotent: move a checkout's UK data from the old layout to the current one.
#     data/rns.duckdb             -> data/uk.duckdb
#     data/raw/{html,lse}         -> data/raw/uk/{html,lse}
#     data/backup_manifest.json   -> data/backup_manifest_uk.json
#     data/logs/full_run.out      -> data/logs/uk_run.out   (+ phase1_report.txt -> uk_report.txt)
# US data (data/us.duckdb, data/raw/us) is already in place. Nothing is deleted: files are moved.
# Refuses to run while the UK scrape is running. On the droplet it also re-installs the UK systemd
# units (stopping the old ones first) and starts them again. The UK run then re-runs its steps once,
# which is cheap because every step skips work already done, and finishes with the steps the old
# run didn't have (the SETS / SETSqx trading service).
#     cd /opt/trading-rns && git pull && bash deploy/migrate-layout.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if pgrep -f "run_full.sh|ingest/uk/run_all.sh|ingest.announcements.run|ingest.uk.announcements" >/dev/null; then
  echo "The UK scrape is still running: wait for it to finish (nothing was changed)."; exit 1
fi
ON_DROPLET=0; [ -f /etc/systemd/system/trading-rns-backup.service ] && ON_DROPLET=1
[ $ON_DROPLET = 1 ] && systemctl stop trading-rns-backup.service trading-rns-scrape.service 2>/dev/null || true

mv_if() { if [ -e "$1" ] && [ ! -e "$2" ]; then mkdir -p "$(dirname "$2")"; mv "$1" "$2"; echo "moved $1 -> $2"; fi; }
mv_if data/rns.duckdb data/uk.duckdb
mv_if data/rns.duckdb.wal data/uk.duckdb.wal
mv_if data/raw/html data/raw/uk/html
mv_if data/raw/lse data/raw/uk/lse
mv_if data/backup_manifest.json data/backup_manifest_uk.json
mv_if data/logs/full_run.out data/logs/uk_run.out
mv_if data/logs/phase1_report.txt data/logs/uk_report.txt
mv_if data/logs/backup_loop.out data/logs/uk_backup.out
uv sync -q
[ $ON_DROPLET = 1 ] && bash deploy/setup-uk.sh
echo "LAYOUT MIGRATION COMPLETE"
