#!/usr/bin/env bash
# One-time setup of the UK data collection on the VPS. Run as root:
#     bash /root/setup-trading-rns.sh      (written by deploy/cloud-init.yaml)  or  bash deploy/setup-uk.sh
# Mirrors deploy/setup-us.sh.
# Asks for secrets interactively (they are typed here, never sent anywhere else):
#   * GitHub fine-grained token with read-only "Contents" on adamlewison/trading-rns
#   * Backblaze B2 key ID + application key (ideally restricted to the backup bucket)
set -euo pipefail
REPO=adamlewison/trading-rns
BRANCH=claude/uk-smallcap-rns-drift-i83n19
DIR=/opt/trading-rns
export PATH="/root/.local/bin:$PATH"

# Re-running is safe: secrets are only asked for if they aren't configured yet.
if [ ! -s /root/.git-credentials ]; then
  read -rsp "GitHub token (read-only, hidden): " GH; echo
  # git credentials stored root-only so `git pull` works later for code updates
  git config --global credential.helper store
  umask 077
  printf 'https://x-access-token:%s@github.com\n' "$GH" > /root/.git-credentials
  umask 022
fi
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull; else git clone -b "$BRANCH" "https://github.com/$REPO.git" "$DIR"; fi
cd "$DIR"
if ! grep -q '^B2_KEY_ID=.\+' .env 2>/dev/null; then
  read -rp  "B2 key ID: " B2ID
  read -rsp "B2 application key (hidden): " B2KEY; echo
  umask 077
  cp -n .env.example .env
  sed -i "s|^B2_KEY_ID=.*|B2_KEY_ID=$B2ID|; s|^B2_APP_KEY=.*|B2_APP_KEY=$B2KEY|" .env
  umask 022
fi
uv sync -q
if [ ! -f data/uk.duckdb ]; then uv run python -W ignore -m core.backup pull --profile uk; fi

cat > /etc/systemd/system/trading-rns-scrape.service <<UNIT
[Unit]
Description=trading-rns UK data collection (resumable)
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=$DIR
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -c 'grep -q "UK FULL RUN COMPLETE" data/logs/uk_run.out 2>/dev/null || exec ingest/uk/run_all.sh >> data/logs/uk_run.out 2>&1'
# no automatic restart: a stop caused by blocking must not hammer the site. It resumes on reboot.
Restart=no
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/trading-rns-backup.service <<UNIT
[Unit]
Description=trading-rns UK backup loop (B2, every 30 min) + remote status
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=$DIR
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -c 'exec deploy/backup-loop.sh uk >> data/logs/uk_backup.out 2>&1'
Restart=always
RestartSec=60
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now trading-rns-scrape.service trading-rns-backup.service
sleep 20
systemctl --no-pager status trading-rns-scrape.service | head -5
tail -3 data/logs/announcements.log
echo "UK SETUP COMPLETE: scrape + backups running. You can close this console."
