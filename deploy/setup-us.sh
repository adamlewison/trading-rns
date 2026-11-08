#!/usr/bin/env bash
# One-time setup of the US (SEC EDGAR) download on the droplet. Run as root in Termius:
#     bash /opt/trading-rns/deploy/setup-us.sh        (after `git -C /opt/trading-rns pull`)
# Uses a SEPARATE checkout + data folder so it never touches the running UK scrape or its DB.
# If a DigitalOcean volume is attached it is formatted (only if blank) and mounted, and the US
# data lives there.
set -euo pipefail
export PATH="/root/.local/bin:$PATH"
UK=/opt/trading-rns
BRANCH=claude/uk-smallcap-rns-drift-i83n19

VOL=$(ls /dev/disk/by-id/scsi-0DO_Volume_* 2>/dev/null | head -1 || true)
if [ -n "$VOL" ]; then
  MNT=/mnt/usdata
  mkdir -p "$MNT"
  if ! blkid "$VOL" >/dev/null 2>&1; then echo "formatting blank volume $VOL"; mkfs.ext4 -q "$VOL"; fi
  grep -q "$MNT" /etc/fstab || echo "$VOL $MNT ext4 defaults,nofail,discard 0 2" >> /etc/fstab
  mountpoint -q "$MNT" || mount "$MNT"
  DIR=$MNT/trading-rns-us
else
  echo "No DigitalOcean volume attached: using the droplet disk."
  DIR=/opt/trading-rns-us
fi
[ -d "$DIR/.git" ] || git clone -q -b "$BRANCH" "$(git -C $UK remote get-url origin)" "$DIR"
git -C "$DIR" pull -q
cp "$UK/.env" "$DIR/.env"                     # B2 key (and nothing US-secret)
if ! grep -q '^SEC_USER_AGENT=' "$DIR/.env"; then
  read -rp 'SEC User-Agent ("Name email"): ' UA
  echo "SEC_USER_AGENT=\"$UA\"" >> "$DIR/.env"
fi
cd "$DIR"
uv sync -q
# tables only: the cached raw pages stay in B2 (not needed to resume; saves disk)
[ -f data/us.duckdb ] || uv run python -W ignore -m core.backup pull --profile us --skip-raw
df -h "$DIR" | tail -1

cat > /etc/systemd/system/trading-rns-us.service <<UNIT
[Unit]
Description=trading-rns US SEC EDGAR document download (resumable)
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=$DIR
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -c 'grep -q "US FULL RUN COMPLETE" data/logs/us_run.out 2>/dev/null || exec ingest/us/run_all.sh >> data/logs/us_run.out 2>&1'
Restart=no
Nice=5
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/trading-rns-us-backup.service <<UNIT
[Unit]
Description=trading-rns US backup loop (B2, every 30 min) + remote status
After=network-online.target
[Service]
WorkingDirectory=$DIR
Environment=PATH=/root/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -c 'while true; do uv run python -W ignore -m core.backup push --profile us --prune-raw >> data/logs/us_backup.out 2>&1; sleep 1800; done'
Restart=always
RestartSec=60
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now trading-rns-us.service trading-rns-us-backup.service
sleep 15; tail -3 data/logs/us.log 2>/dev/null || true
echo "US SETUP COMPLETE"
