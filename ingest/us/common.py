"""US plumbing: config, the US DuckDB (data/us.duckdb), the SEC client, bulk downloads and disk
checks. Mirrors ingest/uk/common.py."""
from __future__ import annotations

import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import httpx

from core import db as _db
from core.config import market
from core.http import PoliteClient
from core.log import get_logger
from ingest.us.schema import SCHEMA

log = get_logger("us")

SEC = "https://www.sec.gov"
SUBMISSIONS_ZIP = f"{SEC}/Archives/edgar/daily-index/bulkdata/submissions.zip"
FACTS_ZIP = f"{SEC}/Archives/edgar/daily-index/xbrl/companyfacts.zip"
TICKERS_URL = f"{SEC}/files/company_tickers_exchange.json"


def us_cfg() -> dict:
    return market("us")


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip().strip('"')
    if not ua or "@" not in ua:
        raise SystemExit("SEC_USER_AGENT missing in .env (SEC requires 'Name email'). Not guessing one.")
    return ua


@contextmanager
def session(read_only: bool = False, memory_limit: str | None = None):
    with _db.session(us_cfg()["db"], SCHEMA, read_only=read_only, memory_limit=memory_limit) as con:
        yield con


def sec_client() -> PoliteClient:
    c = us_cfg()
    client = PoliteClient(cache_root=c["raw_dir"], user_agent=user_agent(), cache_subdir="sec",
                          min_interval_s=c["min_interval_s"])
    client.respect_robots = True       # sec.gov robots.txt allows /Archives
    return client


def bulk_download(url: str, dest: Path, refresh: bool = False) -> Path:
    """Stream a large SEC bulk file to disk with HTTP Range resume. Never held in memory."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not refresh:
        log.info("bulk %s already present (%.2f GB)", dest.name, dest.stat().st_size / 1e9)
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    headers = {"User-Agent": user_agent()}
    with httpx.Client(timeout=120, follow_redirects=True, headers=headers) as h:
        total = int(h.head(url).headers.get("content-length", 0))
        check_disk(total, what=dest.name, interactive=False)
        for attempt in range(8):
            have = part.stat().st_size if part.exists() else 0
            if total and have >= total:
                break
            try:
                with h.stream("GET", url, headers={"Range": f"bytes={have}-"} if have else {}) as r:
                    if r.status_code not in (200, 206):
                        raise httpx.HTTPError(f"HTTP {r.status_code}")
                    mode = "ab" if r.status_code == 206 else "wb"
                    with open(part, mode) as f:
                        last = time.monotonic()
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                            if time.monotonic() - last > 30:
                                log.info("  %s: %.0f / %.0f MB", dest.name, part.stat().st_size / 1e6, total / 1e6)
                                last = time.monotonic()
            except httpx.HTTPError as e:
                log.warning("bulk download interrupted (%s); resuming", e)
                time.sleep(5 * 2**attempt)
    if total and part.stat().st_size != total:
        raise RuntimeError(f"incomplete download {dest.name}: {part.stat().st_size} / {total}")
    part.replace(dest)
    log.info("downloaded %s (%.2f GB)", dest.name, dest.stat().st_size / 1e9)
    return dest


def check_disk(needed_bytes: float, what: str, interactive: bool = True, assume_yes: bool = False) -> None:
    """Refuse to start a bulk step that won't fit; require --yes above the warning threshold."""
    c = us_cfg()
    free = shutil.disk_usage(c["raw_dir"]).free
    gb = needed_bytes / 1e9
    log.info("disk check for %s: need ~%.1f GB, free %.1f GB", what, gb, free / 1e9)
    if needed_bytes > free * 0.9:
        raise SystemExit(f"Not enough disk for {what}: need ~{gb:.1f} GB, free {free / 1e9:.1f} GB")
    if interactive and gb > c["disk_warn_gb"] and not assume_yes:
        raise SystemExit(f"{what} needs ~{gb:.1f} GB (> {c['disk_warn_gb']} GB warning threshold). "
                         "Re-run with --yes once approved.")


# ---------------------------------------------------------------- progress (resumability)
def mark_progress(con, key: str, status: str, n: int) -> None:
    con.execute("INSERT OR REPLACE INTO us_progress VALUES (?,?,?,?)", [key, status, n, datetime.now()])


def done_keys(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT key FROM us_progress WHERE status='done'").fetchall()}
