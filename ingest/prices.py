"""Daily OHLCV from Yahoo (yfinance) for every universe ticker + benchmark indices.

    uv run python -m ingest.prices download [--limit N] [--refresh] [--tickers DSG,TSCO]
    uv run python -m ingest.prices report

* Split-adjusted OHLC (auto_adjust=False -> OHLC adjusted for splits only; adj_close also
  for dividends). Volume is split-adjusted too, so close*volume = £ turnover is consistent.
* Failures (no data / delisted / errors) are logged to price_download_log, never fatal.
* Resumable: tickers fetched within `refresh_after_days` are skipped unless --refresh.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from core.config import date_range, load_config
from core.db import connect, session
from core.log import get_logger
from ingest.quality import flag_prices

log = get_logger("prices")

COLS = ["tidm", "date", "open", "high", "low", "close", "adj_close", "volume", "is_stale", "is_suspect"]


def _tidy(raw: pd.DataFrame, tidm: str, glitch_ratio: float) -> pd.DataFrame:
    df = raw.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                             "Adj Close": "adj_close", "Volume": "volume"})
    df = df.reset_index().rename(columns={"Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.date
    df = df.dropna(subset=["open", "high", "low", "close"], how="all")
    df = df.dropna(subset=["close"])
    if df.empty:
        return df
    df["tidm"] = tidm
    df = flag_prices(df, glitch_ratio)
    return df[COLS].drop_duplicates(["tidm", "date"])


def _download_batch(symbols: list[str], start, end, retries: int) -> pd.DataFrame | None:
    for attempt in range(retries):
        try:
            return yf.download(symbols, start=str(start), end=str(end + timedelta(days=1)),
                               auto_adjust=False, actions=False, group_by="ticker",
                               progress=False, threads=True)
        except Exception as e:  # yfinance raises a zoo of exceptions
            log.warning("batch download failed (%s), attempt %d", e, attempt + 1)
            time.sleep(5 * 2**attempt)
    return None


def download(limit: int | None = None, refresh: bool = False, tickers: list[str] | None = None) -> None:
    cfg = load_config()
    pc = cfg["prices"]
    start, end = date_range(cfg)
    with session() as con:
        u = con.execute("SELECT tidm, yahoo_ticker FROM universe ORDER BY tidm").df()
        recent = con.execute("""SELECT DISTINCT tidm FROM price_download_log WHERE attempted_at > ?""",
                             [datetime.now() - timedelta(days=pc["refresh_after_days"])]).df()
    idx = pd.DataFrame({"tidm": list(pc["index_tickers"].values()),
                        "yahoo_ticker": list(pc["index_tickers"].values())})
    todo = pd.concat([idx, u], ignore_index=True)
    if tickers:
        todo = todo[todo["tidm"].isin(tickers)]
    if not refresh:
        todo = todo[~todo["tidm"].isin(set(recent["tidm"]))]
    if limit:
        todo = todo.head(limit)
    log.info("downloading %d tickers %s..%s", len(todo), start, end)
    bs = pc["batch_size"]
    for i in range(0, len(todo), bs):
        batch = todo.iloc[i:i + bs]
        sym2tidm = dict(zip(batch["yahoo_ticker"], batch["tidm"]))
        raw = _download_batch(list(sym2tidm), start, end, pc["max_retries"])
        logs, frames = [], []
        for sym, tidm in sym2tidm.items():
            now = datetime.now()
            try:
                if raw is None:
                    raise RuntimeError("batch download failed")
                sub = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = _tidy(sub, tidm, pc["unit_glitch_ratio"])
            except Exception as e:
                logs.append((tidm, sym, now, "error", 0, None, None, str(e)[:500]))
                continue
            if df.empty:
                logs.append((tidm, sym, now, "empty", 0, None, None, "no rows (delisted / unknown symbol?)"))
                continue
            frames.append(df)
            logs.append((tidm, sym, now, "ok", len(df), df["date"].min(), df["date"].max(), None))
        with session() as con:
            if frames:
                allp = pd.concat(frames, ignore_index=True)
                con.register("allp", allp)
                con.execute("DELETE FROM prices_daily WHERE tidm IN (SELECT DISTINCT tidm FROM allp)")
                con.execute(f"INSERT INTO prices_daily SELECT {', '.join(COLS)} FROM allp")
                con.unregister("allp")
            con.executemany("INSERT INTO price_download_log VALUES (?,?,?,?,?,?,?,?)", logs)
        ok = sum(1 for l in logs if l[3] == "ok")
        log.info("batch %d/%d: %d ok, %d failed", i // bs + 1, -(-len(todo) // bs), ok, len(logs) - ok)
        time.sleep(pc["pause_between_batches_s"])


def report() -> None:
    con = connect(read_only=True)
    print(con.execute("""
        WITH last AS (SELECT *, row_number() OVER (PARTITION BY tidm ORDER BY attempted_at DESC) rn
                      FROM price_download_log)
        SELECT u.grp, u.is_current, l.status, count(*) n
        FROM last l LEFT JOIN universe u USING (tidm) WHERE rn = 1 GROUP BY ALL ORDER BY ALL""").df())
    print(con.execute("""SELECT year(date) yr, count(DISTINCT tidm) tickers, count(*) rows,
        round(avg(is_stale::INT), 3) stale_frac, sum(is_suspect::INT) suspect
        FROM prices_daily WHERE tidm NOT LIKE '^%' GROUP BY 1 ORDER BY 1""").df().to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download", "report"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--tickers", type=lambda s: s.split(","))
    a = ap.parse_args()
    if a.cmd == "download":
        download(a.limit, a.refresh, a.tickers)
    else:
        report()
