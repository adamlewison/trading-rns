#!/usr/bin/env bash
# Paper-trading timer on the droplet (US checkout): every weekday 22:30 UTC, `paper.factors daily`
# (acts on the first trading day of each month; marks and status every day). Run once as root:
#     cd /opt/trading-rns-us && git pull && bash deploy/setup-paper.sh
set -euo pipefail
DIR=$(cd "$(dirname "$0")/.." && pwd)
cat > /etc/systemd/system/trading-rns-paper.service <<UNIT
[Unit]
Description=trading-rns paper trading (factor portfolios)
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=$DIR
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -c 'exec uv run python -W ignore -m paper.factors daily >> data/logs/paper.out 2>&1'
TimeoutStartSec=4h
UNIT
cat > /etc/systemd/system/trading-rns-paper.timer <<UNIT
[Unit]
Description=Run trading-rns paper trading every weekday after the US close
[Timer]
OnCalendar=Mon..Fri 22:30 UTC
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now trading-rns-paper.timer
systemctl list-timers trading-rns-paper.timer --no-pager | head -3
echo "PAPER TIMER INSTALLED"
