"""Full daily price histories for a few broad ETFs (Yahoo), for the ETF trend test.

    uv run python -W ignore -m ingest.us.run etfs      # -> us_etf_daily (replaced each run)

Kept apart from us_prices_daily (which starts in 2012) because these need each fund's whole history.
adj_close is the total-return price (dividends reinvested)."""
from __future__ import annotations

import time

import pandas as pd

from ingest.us.common import log, session

ETFS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "VNQ", "GLD", "DBC", "TLT", "IEF", "AGG", "SHY", "^IRX"]   # ^IRX: 13-week T-bill yield, %


def etfs() -> None:
    import yfinance as yf
    frames = []
    for t in ETFS:
        for attempt in range(3):
            try:
                df = yf.Ticker(t).history(period="max", auto_adjust=False, actions=False)
                break
            except Exception as e:
                log.warning("etf %s: %s", t, e)
                time.sleep(5 * 2**attempt)
        else:
            raise SystemExit(f"etf {t}: download failed")
        df = df.reset_index().rename(columns=str.lower).rename(columns={"adj close": "adj_close"})
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.date
        df["ticker"] = t
        frames.append(df[["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]])
        log.info("etf %s: %d days from %s", t, len(df), df.date.min())
        time.sleep(1.0)
    out = pd.concat(frames, ignore_index=True)
    with session(memory_limit="300MB") as con:
        con.execute("DELETE FROM us_etf_daily")
        con.register("out", out)
        con.execute("INSERT INTO us_etf_daily SELECT * FROM out")
    log.info("ETFS COMPLETE: %d rows, %d funds", len(out), out.ticker.nunique())
