"""Stock-split histories from Yahoo (yfinance), one request per priced ticker.

Why: Yahoo's daily prices (us_prices_daily) are split-adjusted to today, while XBRL share counts are
as reported at the time. Market cap, P/E and P/B need the price as it was then:
    price_then = close * product(ratio of every split after that date)
Polite: one request a second; after STOP_AFTER failures in a row (likely a block) it stops and says
so rather than pushing on. Resumable: tickers already fetched are skipped."""
from __future__ import annotations

import time
from datetime import datetime

import pandas as pd

from ingest.us.common import log, session
from ingest.us.parse import yahoo_symbol

MIN_INTERVAL_S = 1.0
FLUSH_EVERY = 200
STOP_AFTER = 25


def split_rows(ticker: str, s: pd.Series) -> list[tuple]:
    """yfinance .splits (date -> ratio, e.g. 4.0 for 4-for-1, 0.1 for 1-for-10) -> table rows."""
    return [(ticker, pd.Timestamp(d).date(), float(r)) for d, r in s.items() if r and r > 0 and r != 1.0]


def splits(limit: int | None = None) -> None:
    import yfinance as yf

    with session(memory_limit="200MB") as con:          # read-write so the new tables get created
        tickers = [r[0] for r in con.execute(
            "SELECT DISTINCT ticker FROM us_prices_daily ORDER BY 1").fetchall()]
        done = {r[0] for r in con.execute("SELECT ticker FROM us_split_log WHERE status = 'ok'").fetchall()}
    todo = [t for t in tickers if t not in done][:limit]
    log.info("splits: %d tickers to fetch (%d already done)", len(todo), len(done))
    rows, logs, fails, last = [], [], 0, 0.0

    def flush():
        if not logs:
            return
        with session(memory_limit="200MB") as con:
            names = [x[0] for x in logs]
            con.execute("DELETE FROM us_splits WHERE ticker IN (SELECT unnest(?))", [names])
            con.execute("DELETE FROM us_split_log WHERE ticker IN (SELECT unnest(?))", [names])
            if rows:
                con.executemany("INSERT INTO us_splits VALUES (?, ?, ?)", rows)
            con.executemany("INSERT INTO us_split_log VALUES (?, ?, ?, ?, ?)", logs)
        rows.clear()
        logs.clear()

    for i, t in enumerate(todo):
        time.sleep(max(0.0, MIN_INTERVAL_S - (time.time() - last)))
        last = time.time()
        try:
            got = split_rows(t, yf.Ticker(yahoo_symbol(t)).splits)
            rows.extend(got)
            logs.append((t, datetime.now(), "ok", len(got), None))
            fails = 0
        except Exception as e:
            logs.append((t, datetime.now(), "error", 0, str(e)[:300]))
            fails += 1
            log.warning("splits %s: %s", t, str(e)[:200])
            if fails >= STOP_AFTER:
                flush()
                log.error("splits: STOPPED after %d failures in a row (possible block); not retrying", fails)
                raise SystemExit(3)
        if len(logs) >= FLUSH_EVERY:
            flush()
            log.info("splits %d/%d", i + 1, len(todo))
    flush()
    with session(read_only=True) as con:
        n, k = con.execute("SELECT count(*), count(DISTINCT ticker) FROM us_splits").fetchone()
        st = con.execute("SELECT status, count(*) FROM us_split_log GROUP BY 1").fetchall()
    log.info("SPLITS COMPLETE: %d splits across %d tickers; log %s", n, k, st)
