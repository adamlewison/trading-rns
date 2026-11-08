#!/usr/bin/env bash
# US document download, every step in order (resumable: just rerun after any interruption).
# Mirrors ingest/uk/run_all.sh. Index / submissions / facts / prices are restored from the B2
# backup; refresh them separately if needed. Exit code 3 = SEC refusing us: stop, don't hammer;
# exit code 4 = low disk.
set -u
cd "$(dirname "$0")/../.."
R="uv run python -W ignore -m"
step() { "$@"; rc=$?; [ $rc -eq 0 ] || { echo "STOPPED: step failed (exit $rc): $*"; exit $rc; }; }
step $R ingest.us.run docs --group earnings --yes
step $R ingest.us.run docs --group news --yes
$R ingest.us.run report > data/logs/us_report.txt 2>&1
echo "US FULL RUN COMPLETE"
