#!/usr/bin/env bash
# Install the remote job runner on the droplet (once): lets agents in cloud sessions run commands
# here through B2 (see core/remote.py). Run as root from the US checkout:
#     cd /opt/trading-rns-us && git pull && bash deploy/setup-agent.sh
set -euo pipefail
DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$DIR"
mkdir -p data/logs
cat > /etc/systemd/system/trading-rns-agent.service <<UNIT
[Unit]
Description=trading-rns remote job runner (polls B2 for agent jobs)
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=$DIR
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -c 'exec uv run python -W ignore -m core.remote serve >> data/logs/agent.out 2>&1'
Restart=always
RestartSec=30
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now trading-rns-agent.service
sleep 5; systemctl --no-pager status trading-rns-agent.service | head -5
echo "AGENT RUNNER INSTALLED"
