"""What the US market backs up (used by core.backup), plus the status file the droplet uploads
so the run can be watched remotely (`core.backup status --remote --profile us`)."""
from __future__ import annotations

import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from core.config import load_config
from ingest.us.common import session, us_cfg
from ingest.us.schema import SCHEMA


def profile():
    from core.backup import Profile
    c = us_cfg()
    return Profile(
        name="us", prefix=c["backup_prefix"], db=c["db"], schema=SCHEMA, raw_dir=c["raw_dir"] / "sec",
        snapshot_tables=["sec_form_index", "sec_companies", "sec_filings", "sec_ticker_map", "us_facts",
                         "us_prices_daily", "us_price_download_log", "us_progress",
                         "us_splits", "us_split_log", "us_insider_tx", "us_etf_daily", "paper_picks", "paper_marks"],
        incr_table="us_filing_docs", incr_key="accession",
        manifest=c["db"].parent / "backup_manifest_us.json", status_name="us_status.json",
        status_fn=status, can_prune_raw=True, bulk_dir=c["raw_dir"] / "bulk")


def status() -> dict:
    log_dir: Path = load_config()["paths"]["log_dir"]

    def tail(name, n=15):
        f = log_dir / name
        return f.read_text(errors="replace").splitlines()[-n:] if f.exists() else []

    with session(read_only=True) as con:
        prog = con.execute("SELECT key, status, n FROM us_progress ORDER BY key").df().to_dict("records")
        docs = con.execute("""SELECT item_group, status, count(*) n FROM us_filing_docs GROUP BY ALL
                              ORDER BY ALL""").df().to_dict("records")
    procs = subprocess.run(["pgrep", "-fa", r"run_all|backup-loop|ingest\.us"], capture_output=True, text=True).stdout
    return {"host": platform.node(), "at": datetime.now().isoformat(),
            "uptime": Path("/proc/uptime").read_text().split()[0] if Path("/proc/uptime").exists() else None,
            "disk_free_gb": round(shutil.disk_usage(us_cfg()["db"].parent).free / 1e9, 2),
            "progress": prog[-8:], "docs": docs, "processes": procs.splitlines()[:10],
            "us_log": tail("us.log"), "run_log": tail("us_run.out", 8),
            "http_errors": [l for l in tail("http.log", 200) if "WARNING" in l or "ERROR" in l][-10:]}
