#!/usr/bin/env bash
# US document download, every step in order (resumable: just rerun after any interruption).
# Mirrors ingest/uk/run_all.sh. Index / submissions / facts / prices are restored from the B2
# backup; refresh them separately if needed. Exit code 3 = SEC refusing us: stop, don't hammer;
# exit code 4 = low disk.
set -u
cd "$(dirname "$0")/../.."
R="uv run python -W ignore -m"
# A plain failure (exit 1, e.g. a memory shortage while the backup runs) is retried twice after
# 5 minutes; 3 (SEC refusing) and 4 (low disk) stop at once.
step() {
  for try in 1 2 3; do
    "$@"; rc=$?
    [ $rc -eq 0 ] && return 0
    if [ $rc -ne 1 ] || [ $try -eq 3 ]; then echo "STOPPED: step failed (exit $rc): $*"; exit $rc; fi
    echo "step failed (exit 1), retry $try/2 in 5 min: $*"; sleep 300
  done
}
step $R ingest.us.run docs --group earnings --yes
step $R ingest.us.run docs --group news --yes
$R ingest.us.run report > data/logs/us_report.txt 2>&1
echo "US FULL RUN COMPLETE"
