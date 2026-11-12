"""US data collection from SEC EDGAR (+ Yahoo prices). DATA ONLY: no LLM, features or backtests.

    uv run python -m ingest.us.run index          # EDGAR quarterly full-index -> every 8-K / 8-K/A
    uv run python -m ingest.us.run tickers        # company_tickers_exchange.json (current only)
    uv run python -m ingest.us.run submissions    # bulk submissions.zip -> items + acceptance times
    uv run python -m ingest.us.run estimate       # counts + disk estimate for the document download
    uv run python -m ingest.us.run docs --group earnings [--limit N] [--yes]   # Item 2.02 -> Ex-99.1
    uv run python -m ingest.us.run docs --group news     [--limit N] [--yes]   # 1.01/2.01/7.01/8.01
    uv run python -m ingest.us.run facts          # bulk companyfacts.zip -> selected XBRL concepts
    uv run python -m ingest.us.run prices         # yfinance daily OHLCV, NYSE/Nasdaq + SPY/IWM
    uv run python -m ingest.us.run etfs           # whole-history ETF prices (us_etf_daily)
    uv run python -m ingest.us.run insiders       # SEC Form 4 data sets 2006 on (us_insider_tx)
    uv run python -m ingest.us.run splits         # yfinance split histories (for valuation ratios)
    uv run python -m ingest.us.run report

Everything is resumable (progress table + raw cache in data/raw/us), rate-limited to at most 8 req/s
against SEC (limit: 10), and failures are logged, never fatal. Uses data/us.duckdb only.
"""
from __future__ import annotations

import argparse

from ingest.us.documents import docs, estimate
from ingest.us.facts import facts
from ingest.us.filings import index, submissions, tickers
from ingest.us.prices import prices
from ingest.us.report import report
from ingest.us.splits import splits
from ingest.us.insiders import insiders
from ingest.us.etfs import etfs

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index", "tickers", "submissions", "estimate", "docs", "facts", "prices", "splits", "insiders", "etfs", "report"])
    ap.add_argument("--group", choices=["earnings", "news"], default="earnings")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--yes", action="store_true", help="approve a download estimated above the disk warning threshold")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--year", type=int, help="docs: only filings from this year (pilots)")
    ap.add_argument("--random", action="store_true", help="docs: random order instead of newest first")
    a = ap.parse_args()
    if a.cmd == "docs":
        docs(a.group, a.limit, a.yes, a.year, a.random)
    elif a.cmd in ("submissions", "facts"):
        {"submissions": submissions, "facts": facts}[a.cmd](a.refresh)
    elif a.cmd == "etfs":
        etfs()
    elif a.cmd == "insiders":
        insiders(a.refresh)
    elif a.cmd == "splits":
        splits(a.limit)
    elif a.cmd == "prices":
        prices(a.limit, a.refresh)
    else:
        {"index": index, "tickers": tickers, "estimate": estimate, "report": report}[a.cmd]()
