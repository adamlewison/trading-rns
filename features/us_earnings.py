"""Phase 3 (US earnings): one row per release we could trade, with what was knowable before the trade
and what happened after it. Started 2026-11-11 at the user's request while the label run finishes.

    uv run python -W ignore -m features.us_earnings build      # -> data/features/us_earnings_events.parquet

Point in time, conservatively:
- trade = the OPEN of the first trading day after the release (pre-market release: that morning if it
  is a trading day; after-close release: the next trading day). Entry price = that open.
- price features use closes up to the day BEFORE the trade (the trade-day open is not used as a feature:
  a market-on-open order doesn't know it).
- financials use only facts FILED before the trade date, and for each period the FIRST filed value
  (later restatements ignored).

Outcomes (pre-registered in PLAN.md), each also net of the market over the same days (SPY and IWM):
  r_a  open -> close of the trade day
  r_b  bracket +5% / -3% / 3 days (if a day's range touches both, the stop is assumed hit first; an
       opening gap through a level fills at the open)
  r_c  hold 5 trading days (open of day 1 -> close of day 5)
  r_d  hold 20 trading days with a -8% stop (gap through the stop fills at the open)
Rows whose windows touch a stale (no-trade) or suspect price are flagged; outcomes there are left empty."""
from __future__ import annotations

import argparse
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import ROOT
from core.log import get_logger
from ingest.us.common import session

log = get_logger("features")

OUT = ROOT / "data" / "features"
INDEX = ROOT / "data" / "llm" / "us-earnings-full-v4-20261110T123421" / "index.parquet"
BEFORE, AFTER = 21, 20          # trading days of history / of outcome window
OPEN_T = dtime(9, 30)
BRACKET = (0.05, -0.03, 3)
STOP_D, HOLD_D = -0.08, 20


# ---------------------------------------------------------------- timing
def trade_dates(acceptance: pd.Series, sessions: pd.Series, calendar: np.ndarray) -> pd.Series:
    """First trading day on which a market-on-open order placed after the release can fill."""
    d = acceptance.dt.normalize().values.astype("datetime64[D]")
    before_open = (sessions == "pre_market").values & (acceptance.dt.time < OPEN_T).values
    # pre-market: the same day if it trades, else the next; after-close (and anything else): the next day
    pos = np.where(before_open, np.searchsorted(calendar, d, side="left"),
                   np.searchsorted(calendar, d, side="right"))
    out = np.full(len(d), np.datetime64("NaT"), dtype="datetime64[D]")
    ok = pos < len(calendar)
    out[ok] = calendar[pos[ok]]
    return pd.Series(pd.to_datetime(out), index=acceptance.index)


# ---------------------------------------------------------------- exit rules (pure, tested)
def bracket_exit(entry: float, o, h, l, c, up: float, down: float, days: int) -> tuple[float, int]:
    """Return and holding day (1-based) for a take-profit/stop bracket over `days` days of OHLC."""
    tp, sl = entry * (1 + up), entry * (1 + down)
    for i in range(min(days, len(o))):
        if i > 0 and o[i] <= sl:
            return o[i] / entry - 1, i + 1
        if i > 0 and o[i] >= tp:
            return o[i] / entry - 1, i + 1
        if l[i] <= sl:                       # both touched in one day: stop first (conservative)
            return down, i + 1
        if h[i] >= tp:
            return up, i + 1
    n = min(days, len(c))
    return c[n - 1] / entry - 1, n


def stop_exit(entry: float, o, l, c, stop: float, days: int) -> tuple[float, int]:
    sl = entry * (1 + stop)
    for i in range(min(days, len(o))):
        if i > 0 and o[i] <= sl:
            return o[i] / entry - 1, i + 1
        if l[i] <= sl:
            return stop, i + 1
    n = min(days, len(c))
    return c[n - 1] / entry - 1, n


# ---------------------------------------------------------------- data
def _events() -> pd.DataFrame:
    ev = pd.read_parquet(INDEX, columns=["accession", "cik", "acceptance_et", "session"])
    ev["acceptance_et"] = pd.to_datetime(ev["acceptance_et"])
    return ev


def _tickers(con) -> pd.DataFrame:
    """Every (cik, ticker) with a price series, its date range and median dollar volume."""
    return con.execute("""
        WITH t AS (SELECT DISTINCT cik, ticker FROM sec_ticker_map),
        pr AS (SELECT ticker, min(date) d0, max(date) d1, median(close * volume) dv
               FROM us_prices_daily WHERE NOT is_stale GROUP BY 1)
        SELECT t.cik, pr.* FROM t JOIN pr USING (ticker)""").df()


def _pick_ticker(ev: pd.DataFrame, tk: pd.DataFrame) -> pd.Series:
    """For each event the ticker covering its trade date; with several share classes, the most traded."""
    m = ev[["cik", "trade_date"]].reset_index().merge(tk, on="cik")
    m = m[(m.trade_date >= pd.to_datetime(m.d0)) & (m.trade_date <= pd.to_datetime(m.d1))]
    m = m.sort_values(["index", "dv"], ascending=[True, False]).drop_duplicates("index")
    return m.set_index("index")["ticker"].reindex(ev.index)


class Prices:
    """All daily bars in numpy, sliced per ticker by binary search."""

    def __init__(self, con, tickers: list[str]):
        con.register("want", pd.DataFrame({"ticker": tickers}))
        df = con.execute("""SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume,
                                   p.is_stale, p.is_suspect
                            FROM us_prices_daily p SEMI JOIN want USING (ticker)
                            ORDER BY p.ticker, p.date""").df()
        self.cols = {k: df[k].to_numpy() for k in ["open", "high", "low", "close", "volume"]}
        self.bad = (df["is_stale"] | df["is_suspect"]).to_numpy()
        self.stale = df["is_stale"].to_numpy()
        self.dates = df["date"].to_numpy().astype("datetime64[D]")
        t = df["ticker"].to_numpy()
        starts = np.flatnonzero(np.r_[True, t[1:] != t[:-1]])
        ends = np.r_[starts[1:], len(t)]
        self.span = {t[s]: (s, e) for s, e in zip(starts, ends)}

    def locate(self, ticker: str, day) -> int | None:
        if ticker not in self.span:
            return None
        s, e = self.span[ticker]
        d = np.datetime64(pd.Timestamp(day).date(), "D")
        i = s + np.searchsorted(self.dates[s:e], d)
        return int(i) if i < e and self.dates[i] == d else None

    def window(self, i: int, ticker: str, before: int, after: int):
        s, e = self.span[ticker]
        return max(s, i - before), min(e, i + after)


# ---------------------------------------------------------------- build
def _price_block(P: Prices, ticker: str, i: int) -> dict:
    s, e = P.window(i, ticker, BEFORE, AFTER)
    o, h, l, c, v = (P.cols[k] for k in ["open", "high", "low", "close", "volume"])
    out: dict = {}
    hist = slice(s, i)                                  # days before the trade
    if i - s >= BEFORE:
        ch = c[hist]
        out["prev_close"] = ch[-1]
        out["ret_5d"] = ch[-1] / ch[-6] - 1
        out["ret_20d"] = ch[-1] / ch[-21] - 1
        out["vol_20d"] = float(np.std(np.diff(np.log(ch[-21:]))))
        out["dollar_vol_20d"] = float(np.mean(ch[-20:] * v[i - 20:i]))
        out["hist_bad"] = bool(P.bad[hist].any())
    fwd = slice(i, e)
    n = e - i
    entry = o[i]
    out["entry_open"] = entry
    out["fwd_bad"] = bool(P.bad[fwd].any())
    if entry and entry > 0 and n >= 1:
        out["r_a"] = c[i] / entry - 1
        out["r_b"], out["r_b_days"] = bracket_exit(entry, o[fwd], h[fwd], l[fwd], c[fwd], *BRACKET)
        if n >= 5:
            out["r_c"] = c[i + 4] / entry - 1
        if n >= HOLD_D:
            out["r_d"], out["r_d_days"] = stop_exit(entry, o[fwd], l[fwd], c[fwd], STOP_D, HOLD_D)
    return out


def _bench(P: Prices, sym: str, day, days_by_rule: dict) -> dict:
    """Market return over the same days as each rule (open of day 1 -> close of the exit day)."""
    i = P.locate(sym, day)
    if i is None:
        return {}
    o, c = P.cols["open"], P.cols["close"]
    s, e = P.span[sym]
    out = {}
    for rule, nd in days_by_rule.items():
        if nd and not np.isnan(nd) and i + int(nd) - 1 < e:
            out[f"{sym.lower()}_{rule}"] = c[i + int(nd) - 1] / o[i] - 1
    return out


def build(limit: int | None = None) -> Path:
    ev = _events()
    if limit:
        ev = ev.head(limit)
    with session(read_only=True, memory_limit="6GB") as con:
        cal = con.execute("SELECT date FROM us_prices_daily WHERE ticker='SPY' AND NOT is_stale ORDER BY 1") \
            .df()["date"].to_numpy().astype("datetime64[D]")
        ev["trade_date"] = trade_dates(ev["acceptance_et"], ev["session"], cal)
        ev["ticker"] = _pick_ticker(ev, _tickers(con))
        items = con.execute("SELECT accession, items FROM sec_filings WHERE list_contains(?, accession)",
                            [ev.accession.tolist()]).df()
        P = Prices(con, sorted(set(ev.ticker.dropna())) + ["SPY", "IWM"])
    ev = ev.merge(items, on="accession", how="left")
    rows = []
    for r in ev.itertuples():
        row = {"accession": r.accession}
        i = P.locate(r.ticker, r.trade_date) if isinstance(r.ticker, str) and pd.notna(r.trade_date) else None
        if i is None or P.stale[i]:
            row["skip"] = "no_price_on_trade_date"
        else:
            row.update(_price_block(P, r.ticker, i))
            days = {"a": 1, "b": row.get("r_b_days"), "c": 5 if "r_c" in row else None, "d": row.get("r_d_days")}
            for sym in ("SPY", "IWM"):
                row.update(_bench(P, sym, r.trade_date, days))
        rows.append(row)
    out = ev.merge(pd.DataFrame(rows), on="accession", how="left")
    for rule in "abcd":
        for sym in ("spy", "iwm"):
            if f"r_{rule}" in out and f"{sym}_{rule}" in out:
                out[f"x_{rule}_{sym}"] = out[f"r_{rule}"] - out[f"{sym}_{rule}"]
    bad = out["fwd_bad"].fillna(False).astype(bool)
    out.loc[bad, [c for c in out.columns if c.startswith(("r_", "x_"))]] = np.nan
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "us_earnings_events.parquet"
    out.to_parquet(path)
    log.info("events: %d rows, %d without a price on the trade date, %d with a bad forward window -> %s",
             len(out), int(out["skip"].notna().sum()), int(bad.sum()), path)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--limit", type=int)
    a = ap.parse_args()
    print(build(a.limit))


if __name__ == "__main__":
    main()
