"""Run shell commands on the droplet from anywhere that can reach B2 (cloud sessions can't SSH).

    # on the droplet, once, as a service (deploy/setup-agent.sh):
    uv run python -W ignore -m core.remote serve
    # from a cloud session or any machine with B2 access:
    uv run python -W ignore -m core.remote run "systemctl status trading-rns-us --no-pager"
    uv run python -W ignore -m core.remote run "git pull" --cwd /opt/trading-rns-us
    uv run python -W ignore -m core.remote run "..." --no-wait        # returns the job id
    uv run python -W ignore -m core.remote result JOB_ID

How: the caller writes a job (a bash command) to B2 under `trading-rns/control/jobs/`; the droplet's
runner polls every POLL_S seconds (IDLE_POLL_S when idle), runs it with a timeout, and writes stdout/stderr and the exit code
to `trading-rns/control/results/`. Only HTTPS to B2 is needed on both sides.

Trust: anyone who can write to the bucket can run commands as root on the droplet, so the B2 key is
as sensitive as an SSH key. Jobs older than MAX_AGE are ignored, each job runs once, and every job
and its output stay in B2 as an audit log. Long jobs should start themselves with
`systemd-run --unit=NAME ...` so they outlive the job's timeout."""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from core.backup import get_store
from core.log import get_logger

log = get_logger("remote")
ROOT = "trading-rns/control"
POLL_S = 5                             # while busy (a job in the last BUSY_FOR)
IDLE_POLL_S = 15                       # otherwise: keeps B2 list calls near the free daily quota
BUSY_FOR = timedelta(minutes=10)
MAX_AGE = timedelta(hours=1)
MAX_OUT = 200_000                      # characters kept from each of stdout and stderr
DEFAULT_CWD = "/opt/trading-rns-us"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def submit(cmd: str, cwd: str = DEFAULT_CWD, timeout_s: int = 1800, store=None) -> str:
    store = store or get_store()
    jid = f"{_now():%Y%m%dT%H%M%S}-{secrets.token_hex(4)}"
    job = {"id": jid, "cmd": cmd, "cwd": cwd, "timeout_s": timeout_s, "created": _now().isoformat()}
    store.put(f"{ROOT}/jobs/{jid}.json", json.dumps(job).encode())
    return jid


def result(jid: str, store=None) -> dict | None:
    raw = (store or get_store()).get(f"{ROOT}/results/{jid}.json")
    return json.loads(raw) if raw else None


def wait(jid: str, timeout_s: int, store=None, poll_s: float = 2) -> dict:
    store = store or get_store()
    end = time.time() + timeout_s + 3 * IDLE_POLL_S + 60
    while time.time() < end:
        r = result(jid, store)
        if r:
            return r
        time.sleep(poll_s)
    raise TimeoutError(f"no result for {jid}: is the droplet's runner (trading-rns-agent) up?")


def run_job(job: dict) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run(["bash", "-lc", job["cmd"]], cwd=job.get("cwd") or None, capture_output=True,
                           text=True, timeout=job.get("timeout_s", 1800))
        code, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        code, out, err = 124, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), "timed out"
    except Exception as e:                      # bad cwd etc.
        code, out, err = 127, "", f"{type(e).__name__}: {e}"
    return {"id": job["id"], "exit": code, "stdout": out[-MAX_OUT:], "stderr": err[-MAX_OUT:],
            "started": datetime.fromtimestamp(t0, timezone.utc).isoformat(), "seconds": round(time.time() - t0, 1)}


def serve_once(store=None, done: set | None = None) -> int:
    """Run every pending job once; returns how many ran. `done` (job file names already answered) is
    kept by the caller so each poll costs one B2 list call; without it, results are listed."""
    store = store or get_store()
    if done is None:
        done = {k.rsplit("/", 1)[-1] for k in store.list(f"{ROOT}/results/")}
    n = 0
    for key in sorted(store.list(f"{ROOT}/jobs/")):
        name = key.rsplit("/", 1)[-1]
        if name in done:
            continue
        job = json.loads(store.get(key))
        if _now() - datetime.fromisoformat(job["created"]) > MAX_AGE:
            res = {"id": job["id"], "exit": None, "stdout": "", "stderr": "expired: not run", "seconds": 0}
        else:
            log.info("job %s: %s", job["id"], job["cmd"][:200])
            res = run_job(job)
            log.info("job %s finished: exit %s in %ss", job["id"], res["exit"], res["seconds"])
        store.put(f"{ROOT}/results/{name}", json.dumps(res).encode())
        done.add(name)
        n += 1
    return n


def serve() -> None:
    log.info("remote runner started; polling %s every %ss (busy) / %ss (idle)", ROOT, POLL_S, IDLE_POLL_S)
    done, last_job, store = None, _now() - BUSY_FOR, None
    while True:
        try:
            store = store or get_store()             # log in once; again only after an error
            if done is None:
                done = {k.rsplit("/", 1)[-1] for k in store.list(f"{ROOT}/results/")}
            if serve_once(store, done):
                last_job = _now()
        except Exception as e:                  # network hiccup: keep going
            log.warning("runner: %s: %s", type(e).__name__, str(e)[:200])
            store = None
        time.sleep(POLL_S if _now() - last_job < BUSY_FOR else IDLE_POLL_S)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("command")
    r.add_argument("--cwd", default=DEFAULT_CWD)
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--no-wait", action="store_true")
    g = sub.add_parser("result"); g.add_argument("id")
    sub.add_parser("serve")
    a = ap.parse_args()
    if a.cmd == "serve":
        serve()
    elif a.cmd == "result":
        print(json.dumps(result(a.id), indent=1))
    else:
        jid = submit(a.command, a.cwd, a.timeout)
        if a.no_wait:
            print(jid)
            return
        res = wait(jid, a.timeout)
        sys.stdout.write(res["stdout"])
        sys.stderr.write(res["stderr"])
        sys.exit(res["exit"] if isinstance(res["exit"], int) else 1)


if __name__ == "__main__":
    main()
