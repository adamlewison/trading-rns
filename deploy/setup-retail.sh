#!/usr/bin/env bash
# Retail-offers live trial on the droplet: weekdays 07:30 and 17:30 UK time, `paper.retail_offers daily`
# (scan recent announcements, read new retail offers, update the status in B2). Run once as root from
# the checkout that tracks the working branch:   cd /opt/trading-rns-pm && git pull && bash deploy/setup-retail.sh
set -euo pipefail
DIR=$(cd "$(dirname "$0")/.." && pwd)
PY=/opt/trading-rns-us/.venv/bin/python
cat > /etc/systemd/system/trading-rns-retail.service <<UNIT
[Unit]
Description=trading-rns retail-offers live trial
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=$DIR
MemoryMax=450M
ExecStart=/bin/bash -c 'git pull -q >/dev/null 2>&1; exec $PY -W ignore -m paper.retail_offers daily >> data/logs/retail_trial.out 2>&1'
TimeoutStartSec=1h
UNIT
cat > /etc/systemd/system/trading-rns-retail.timer <<UNIT
[Unit]
Description=Run the retail-offers trial scan twice each weekday
[Timer]
OnCalendar=Mon..Fri 07:30 Europe/London
OnCalendar=Mon..Fri 17:30 Europe/London
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now trading-rns-retail.timer
systemctl list-timers trading-rns-retail.timer --no-pager | head -3
echo "RETAIL TIMER INSTALLED"
