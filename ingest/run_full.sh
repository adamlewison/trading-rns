#!/usr/bin/env bash
# Full Phase 1 announcement scrape (resumable: just rerun after any interruption).
set -u
cd "$(dirname "$0")/.."
R="uv run python -W ignore -m"
step() { "$@" || { echo "STOPPED: step failed (exit $?): $*"; exit 1; }; }
step $R ingest.announcements.run index --mode company
step $R ingest.announcements.run index --mode daily
step $R ingest.announcements.run reclassify
step $R ingest.universe add-delisted
step $R ingest.prices download              # prices for newly added (delisted) tickers
step $R ingest.announcements.run bodies
step $R ingest.announcements.run build
$R ingest.announcements.run report > data/logs/phase1_report.txt
echo "PHASE1 FULL RUN COMPLETE"
