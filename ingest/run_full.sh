#!/usr/bin/env bash
# Full Phase 1 announcement scrape (resumable: just rerun after any interruption).
set -u
cd "$(dirname "$0")/.."
R="uv run python -W ignore -m"
$R ingest.announcements.run index --mode company
$R ingest.announcements.run index --mode daily
$R ingest.announcements.run reclassify
$R ingest.universe add-delisted
$R ingest.prices download              # prices for newly added (delisted) tickers
$R ingest.announcements.run bodies
$R ingest.announcements.run build
$R ingest.announcements.run report > data/logs/phase1_report.txt
echo "PHASE1 FULL RUN COMPLETE"
