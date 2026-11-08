"""US (SEC EDGAR) plumbing: config, separate DuckDB (data/us.duckdb), SEC client, bulk downloads,
disk checks. Nothing here touches the UK database."""
from __future__ import annotations

import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from core.config import ROOT, load_config
from core.db import session as _session
from core.http import PoliteClient
from core.log import get_logger

log = get_logger("us")

US_SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_form_index (       -- EDGAR quarterly full-index, 8-K & 8-K/A only
    accession VARCHAR PRIMARY KEY, cik BIGINT, company VARCHAR, form VARCHAR, date_filed DATE,
    filename VARCHAR, year INTEGER, qtr INTEGER
);
CREATE TABLE IF NOT EXISTS sec_companies (        -- from submissions.zip (current metadata)
    cik BIGINT PRIMARY KEY, name VARCHAR, tickers VARCHAR, exchanges VARCHAR, sic VARCHAR,
    sic_description VARCHAR, state_of_inc VARCHAR, category VARCHAR, entity_type VARCHAR,
    fiscal_year_end VARCHAR, former_names VARCHAR
);
CREATE TABLE IF NOT EXISTS sec_filings (          -- 8-K/8-K/A rows from submissions.zip (has items)
    accession VARCHAR PRIMARY KEY, cik BIGINT, form VARCHAR, filing_date DATE, report_date DATE,
    acceptance_utc_sub TIMESTAMP,                  -- as published in submissions JSON ("...Z")
    items VARCHAR, primary_doc VARCHAR, is_xbrl BOOLEAN
);
CREATE TABLE IF NOT EXISTS sec_ticker_map (       -- company_tickers_exchange.json (CURRENT only)
    snapshot_date DATE, cik BIGINT, name VARCHAR, ticker VARCHAR, exchange VARCHAR
);
CREATE TABLE IF NOT EXISTS us_filing_docs (       -- one row per fetched filing
    accession VARCHAR PRIMARY KEY, cik BIGINT, item_group VARCHAR,   -- earnings | news
    form VARCHAR, items_header VARCHAR,
    acceptance_et TIMESTAMP, acceptance_utc TIMESTAMP,
    session VARCHAR,                               -- pre_market | intraday | after_close | non_trading_day
    doc_type VARCHAR, doc_name VARCHAR, doc_url VARCHAR,
    text VARCHAR, chars INTEGER,
    status VARCHAR,                                -- ok | no_document | empty | not_found | error
    fetched_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS us_facts (             -- selected XBRL facts from companyfacts.zip
    cik BIGINT, taxonomy VARCHAR, concept VARCHAR, unit VARCHAR, val DOUBLE,
    start_date DATE, end_date DATE, fy INTEGER, fp VARCHAR, form VARCHAR, filed DATE,
    accn VARCHAR, frame VARCHAR
);
CREATE TABLE IF NOT EXISTS us_prices_daily (
    ticker VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    adj_close DOUBLE, volume DOUBLE, is_stale BOOLEAN, is_suspect BOOLEAN,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS us_price_download_log (
    ticker VARCHAR, yahoo_ticker VARCHAR, attempted_at TIMESTAMP, status VARCHAR,
    n_rows INTEGER, first_date DATE, last_date DATE, error VARCHAR
);
CREATE TABLE IF NOT EXISTS us_progress (
    key VARCHAR PRIMARY KEY, status VARCHAR, n INTEGER, updated_at TIMESTAMP
);
"""


def us_cfg() -> dict:
    cfg = dict(load_config()["us"])
    for k in ("db", "raw_dir"):
        p = Path(os.environ.get(f"US_{k.upper()}", cfg[k]))
        cfg[k] = p if p.is_absolute() else ROOT / p
    cfg["raw_dir"].mkdir(parents=True, exist_ok=True)
    cfg["db"].parent.mkdir(parents=True, exist_ok=True)
    return cfg


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip().strip('"')
    if not ua or "@" not in ua:
        raise SystemExit("SEC_USER_AGENT missing in .env (SEC requires 'Name email'). Not guessing one.")
    return ua


@contextmanager
def session(read_only: bool = False):
    with _session(read_only=read_only, db=us_cfg()["db"], schema=US_SCHEMA) as con:
        yield con


def sec_client() -> PoliteClient:
    c = us_cfg()
    client = PoliteClient(cache_subdir="sec", min_interval_s=c["min_interval_s"],
                          user_agent=user_agent(), cache_root=c["raw_dir"])
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
