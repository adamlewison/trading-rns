#!/usr/bin/env bash
# UK Phase 1 data collection, every step in order (resumable: just rerun after any interruption).
# Mirrors ingest/us/run_all.sh. Exit code 3 from a step = the site is refusing us: stop, don't hammer.
set -u
cd "$(dirname "$0")/../.."
R="uv run python -W ignore -m"
step() { "$@"; rc=$?; [ $rc -eq 0 ] || { echo "STOPPED: step failed (exit $rc): $*"; exit $rc; }; }
step $R ingest.uk.announcements.run index --mode company
step $R ingest.uk.announcements.run index --mode daily
step $R ingest.uk.announcements.run reclassify
step $R ingest.uk.universe add-delisted
step $R ingest.uk.prices download              # prices for newly added (delisted) tickers
step $R ingest.uk.announcements.run bodies
step $R ingest.uk.announcements.run build
step $R ingest.uk.trading_service build        # SETS / SETSqx per ticker
$R ingest.uk.announcements.run report > data/logs/uk_report.txt 2>&1
echo "UK FULL RUN COMPLETE"
