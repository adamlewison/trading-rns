#!/usr/bin/env bash
# Run a command on the droplet from wherever this agent is:
#   1. SSH, if this machine has a key the droplet accepts (local machines);
#   2. otherwise the B2 job queue (core.remote), e.g. from cloud sessions, which can't SSH.
#     deploy/on-droplet.sh "systemctl status trading-rns-us --no-pager"
#     deploy/on-droplet.sh "git pull" /opt/trading-rns-us
set -uo pipefail
HOST=${DROPLET_HOST:-178.62.96.171}
USER_=${DROPLET_USER:-root}
CMD=${1:?usage: on-droplet.sh "COMMAND" [DIR]}
DIR=${2:-/opt/trading-rns-us}
cd "$(dirname "$0")/.."
if command -v ssh >/dev/null && ssh -o BatchMode=yes -o ConnectTimeout=6 "$USER_@$HOST" true 2>/dev/null; then
  exec ssh -o BatchMode=yes "$USER_@$HOST" "cd $(printf %q "$DIR") && $CMD"
fi
echo "[on-droplet] SSH not available from here; using the B2 job queue" >&2
exec uv run python -W ignore -m core.remote run "$CMD" --cwd "$DIR"
