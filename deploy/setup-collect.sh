#!/usr/bin/env bash
# Daily data collection timers on the droplet (collect/run.py). Run once as root:
#     cd /opt/trading-rns-us && git pull && bash deploy/setup-collect.sh
# Heavy jobs share a lock so only one runs at a time on the 1GB droplet. Times are UTC.
set -euo pipefail
DIR=$(cd "$(dirname "$0")/.." && pwd)
cat > /etc/systemd/system/trading-rns-collect@.service <<UNIT
[Unit]
Description=trading-rns data collection: %i
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=$DIR
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -c 'exec flock /tmp/trading-rns-collect.lock uv run python -W ignore -m collect.run %i >> data/logs/collect.out 2>&1'
TimeoutStartSec=3h
UNIT
declare -A WHEN=(
  [borrow]="Mon..Fri 08:05,13:05,17:05,20:35 UTC"
  [options]="Mon..Fri 19:40 UTC"
  [prices]="Mon..Fri 21:40 UTC"
  [analysts]="Mon..Fri 23:10 UTC"
  [listings]="Mon..Sat 23:55 UTC"
)
for job in "${!WHEN[@]}"; do
cat > /etc/systemd/system/trading-rns-collect-$job.timer <<UNIT
[Unit]
Description=trading-rns data collection timer: $job
[Timer]
OnCalendar=${WHEN[$job]}
Persistent=true
Unit=trading-rns-collect@$job.service
[Install]
WantedBy=timers.target
UNIT
done
mkdir -p "$DIR/data/logs"
systemctl daemon-reload
for job in "${!WHEN[@]}"; do systemctl enable --now trading-rns-collect-$job.timer; done
systemctl list-timers 'trading-rns-collect-*' --no-pager
echo "COLLECT TIMERS INSTALLED"
