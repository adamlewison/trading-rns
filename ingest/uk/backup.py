"""What the UK market backs up (used by core.backup), plus the status file the droplet uploads
so the run can be watched remotely (`core.backup status --remote --profile uk`)."""
from __future__ import annotations

import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from core.config import load_config
from ingest.uk.common import session, uk_cfg
from ingest.uk.schema import SCHEMA


def profile():
    from core.backup import Profile
    c = uk_cfg()
    return Profile(
        name="uk", prefix=c["backup_prefix"], db=c["db"], schema=SCHEMA, raw_dir=c["raw_dir"],
        snapshot_tables=["lse_instruments", "lse_issuer_snapshots", "universe", "prices_daily",
                         "price_download_log", "ann_index", "scrape_progress",
                         "lse_service_map", "uk_trading_service", "uk_market_makers"],
        incr_table="ann_bodies", incr_key="ann_id",
        manifest=c["db"].parent / "backup_manifest_uk.json", status_name="status.json",
        status_fn=status, can_prune_raw=False)          # the UK page cache is never pruned


def status() -> dict:
    log_dir: Path = load_config()["paths"]["log_dir"]

    def tail(name, n=15):
        f = log_dir / name
        return f.read_text(errors="replace").splitlines()[-n:] if f.exists() else []

    with session(read_only=True) as con:
        prog = con.execute("""SELECT split_part(key, ':', 1) AS kind, status, count(*) AS n, max(key) AS last_key
                              FROM scrape_progress GROUP BY ALL ORDER BY ALL""").df().to_dict("records")
        bodies = con.execute("SELECT status, count(*) AS n FROM ann_bodies GROUP BY 1").df().to_dict("records")
    procs = subprocess.run(["pgrep", "-fa", r"run_all|backup-loop|ingest\.uk"], capture_output=True, text=True).stdout
    return {"host": platform.node(), "at": datetime.now().isoformat(),
            "uptime": Path("/proc/uptime").read_text().split()[0] if Path("/proc/uptime").exists() else None,
            "disk_free_gb": round(shutil.disk_usage(uk_cfg()["db"].parent).free / 1e9, 2),
            "progress": prog, "bodies": bodies, "processes": procs.splitlines()[:10],
            "announcements_log": tail("announcements.log"), "full_run": tail("uk_run.out", 8),
            "http_errors": [l for l in tail("http.log", 200) if "WARNING" in l or "ERROR" in l][-10:]}
