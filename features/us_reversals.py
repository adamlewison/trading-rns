"""No-news reversal events (design: backtest/DESIGN_REVERSALS.md).

    uv run python -W ignore -m features.us_reversals build   # -> data/features/us_reversal_events.parquet

Day t = a big fall vs the market without an 8-K on t or the previous trading day. Features use
data up to the close of t (the fall itself is known at the close; E1 buys at that close).
Outcomes (each also for SPY and IWM over the same span):
  E1 (entry close t):    e1_h1/h3/h5 = close t+n / close t - 1;  e1_a5 = ATR bracket over t+1..t+5
  E2 (entry open t+1):   e2_h1/h3/h5 = close t+n / open t+1 - 1;  e2_a5 = ATR bracket from open t+1"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from core.config import ROOT
from core.log import get_logger
from features.us_earnings import Prices, bracket_exit
from features.us_earnings_labels import valuation
from ingest.us.common import session

log = get_logger("features")
OUT = ROOT / "data" / "features" / "us_reversal_events.parquet"
K_SIGMA, MIN_FALL, MIN_PRICE, MIN_DVOL, GAP_DAYS = 2.5, -0.04, 5.0, 20e6, 5
HOLDS = (1, 3, 5)
ATR_K, A_DAYS = 1.5, 5
BIG_CAP = 10e9


def candidates(con) -> pd.DataFrame:
    """Every stock-day falling >= K_SIGMA sigmas vs SPY or IWM (benchmark fixed later by size)."""
    return con.execute(f"""
        WITH b AS (
            SELECT date, max(CASE WHEN ticker='SPY' THEN r END) spy_r, max(CASE WHEN ticker='IWM' THEN r END) iwm_r
            FROM (SELECT ticker, date, close / lag(close) OVER (PARTITION BY ticker ORDER BY date) - 1 AS r
                  FROM us_prices_daily WHERE ticker IN ('SPY','IWM') AND NOT is_stale) GROUP BY 1),
        p AS (
            SELECT ticker, date, close, volume, (is_stale OR is_suspect) AS bad,
                   close / lag(close) OVER w - 1 AS r,
                   lag(close) OVER w AS prev_close
            FROM us_prices_daily WINDOW w AS (PARTITION BY ticker ORDER BY date)),
        s AS (
            SELECT p.*, stddev_samp(r) OVER w20 AS sd20, avg(close * volume) OVER w20 AS dvol20,
                   sum(bad::INT) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 21 PRECEDING AND CURRENT ROW) AS n_bad
            FROM p
            WINDOW w20 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING))
        SELECT s.ticker, s.date, s.r, s.prev_close, s.sd20, s.dvol20, b.spy_r, b.iwm_r
        FROM s JOIN b USING (date)
        WHERE s.ticker NOT IN ('SPY','IWM') AND s.n_bad = 0 AND s.prev_close >= {MIN_PRICE}
          AND s.dvol20 >= {MIN_DVOL} AND s.r <= {MIN_FALL} AND s.sd20 > 0
          AND (s.r - b.spy_r <= -{K_SIGMA} * s.sd20 OR s.r - b.iwm_r <= -{K_SIGMA} * s.sd20)""").df()


def thin(df: pd.DataFrame, cal: np.ndarray, gap: int = GAP_DAYS) -> pd.DataFrame:
    """Keep an event only if the same stock had no kept event in the previous `gap` trading days."""
    df = df.sort_values(["ticker", "date"]).copy()
    pos = np.searchsorted(cal, df.date.to_numpy().astype("datetime64[D]"))
    keep, last_t, last_p = [], None, -10**9
    for t, p in zip(df.ticker, pos):
        if t != last_t:
            last_t, last_p = t, -10**9
        ok = p - last_p > gap
        keep.append(ok)
        if ok:
            last_p = p
    return df[np.array(keep)]


def outcomes(P: Prices, t: str, i: int, atr: float) -> dict:
    o, h, l, c = (P.cols[x] for x in ["open", "high", "low", "close"])
    _, e = P.span[t]
    out = {}
    if i + max(HOLDS) >= e or P.bad[i:i + max(HOLDS) + 1].any():
        return out
    for n in HOLDS:
        out[f"e1_h{n}"] = c[i + n] / c[i] - 1
        out[f"e2_h{n}"] = c[i + n] / o[i + 1] - 1
    w = slice(i + 1, i + 1 + A_DAYS)
    up1, dn1 = ATR_K * atr / c[i], -ATR_K * atr / c[i]
    r, d = bracket_exit(c[i], o[w], h[w], l[w], c[w], up1, dn1, A_DAYS)
    out["e1_a5"], out["e1_a5_days"] = r, d
    up2, dn2 = ATR_K * atr / o[i + 1], -ATR_K * atr / o[i + 1]
    r, d = bracket_exit(o[i + 1], o[w], h[w], l[w], c[w], up2, dn2, A_DAYS)
    out["e2_a5"], out["e2_a5_days"] = r, d
    out["entry_close"], out["entry_open_next"] = c[i], o[i + 1]
    return out


def bench(P: Prices, sym: str, day, row: dict) -> dict:
    i = P.locate(sym, day)
    if i is None or "e1_h1" not in row:
        return {}
    o, c = P.cols["open"], P.cols["close"]
    s = sym.lower()
    out = {}
    for n in HOLDS:
        out[f"{s}_e1_h{n}"] = c[i + n] / c[i] - 1
        out[f"{s}_e2_h{n}"] = c[i + n] / o[i + 1] - 1
    out[f"{s}_e1_a5"] = c[i + int(row["e1_a5_days"])] / c[i] - 1
    out[f"{s}_e2_a5"] = c[i + int(row["e2_a5_days"])] / o[i + 1] - 1
    return out


def context(P: Prices, t: str, i: int) -> dict:
    o, h, l, c, v = (P.cols[x] for x in ["open", "high", "low", "close", "volume"])
    s, _ = P.span[t]
    if i - s < 61:
        return {}
    ch = c[max(s, i - 252):i]
    tr = np.maximum(h[i - 19:i + 1], c[i - 20:i]) - np.minimum(l[i - 19:i + 1], c[i - 20:i])
    return dict(ret_5d=c[i - 1] / c[i - 6] - 1, ret_20d=c[i - 1] / c[i - 21] - 1, ret_60d=c[i - 1] / c[i - 61] - 1,
                off_high_52w=c[i] / ch.max() - 1, off_low_52w=c[i] / ch.min() - 1, atr_20d=float(np.mean(tr)),
                day_range=(h[i] - l[i]) / c[i - 1], close_in_range=(c[i] - l[i]) / (h[i] - l[i]) if h[i] > l[i] else 0.5)


def build() -> pd.DataFrame:
    with session(read_only=True, memory_limit="10GB") as con:
        cal = con.execute("SELECT date FROM us_prices_daily WHERE ticker='SPY' AND NOT is_stale ORDER BY 1") \
            .df()["date"].to_numpy().astype("datetime64[D]")
        ev = candidates(con)
        ev["date"] = pd.to_datetime(ev.date)
        log.info("candidate falls: %d", len(ev))
        # company (CIK) for the ticker, for news and size
        tk = con.execute("SELECT DISTINCT ticker, cik FROM sec_ticker_map").df().drop_duplicates("ticker")
        ev = ev.merge(tk, on="ticker", how="left")
        k = np.searchsorted(cal, ev.date.to_numpy().astype("datetime64[D]"))
        ev["prev_day"] = pd.to_datetime(cal[np.maximum(k - 1, 0)])
        con.register("ev", ev[["ticker", "date", "prev_day", "cik"]])
        news = con.execute("""
            SELECT e.ticker, e.date, count(*) AS n_8k,
                   max(CASE WHEN list_contains(string_split(f.items, ','), '2.02') THEN 1 ELSE 0 END) AS earnings_8k
            FROM ev e JOIN sec_filings f ON f.cik = e.cik AND f.form LIKE '8-K%'
                 AND f.filing_date BETWEEN e.prev_day AND e.date
            LEFT JOIN us_filing_docs d ON d.accession = f.accession
            -- a same-day 8-K known to be filed at/after the 16:00 close was not known when buying at the close
            WHERE NOT (f.filing_date = e.date AND d.acceptance_et IS NOT NULL AND extract(hour FROM d.acceptance_et) >= 16)
            GROUP BY 1, 2""").df()
        news["date"] = pd.to_datetime(news.date)
        last_er = con.execute("""
            SELECT e.ticker, e.date, max(f.filing_date) AS last_earnings
            FROM ev e JOIN sec_filings f ON f.cik = e.cik AND list_contains(string_split(f.items, ','), '2.02')
                 AND f.filing_date < e.prev_day GROUP BY 1, 2""").df()
        last_er["date"] = pd.to_datetime(last_er.date)
        ev = ev.merge(news, on=["ticker", "date"], how="left").merge(last_er, on=["ticker", "date"], how="left")
        ev["n_8k"] = ev.n_8k.fillna(0).astype(int)
        ev["no_cik"] = ev.cik.isna()
        ev["days_since_earnings"] = (ev.date - pd.to_datetime(ev.last_earnings)).dt.days
        # size, as of the close before the fall (reuses the earnings valuation code)
        v = ev[ev.cik.notna()].assign(accession=lambda x: x.ticker + "|" + x.date.dt.strftime("%Y%m%d"),
                                      trade_date=lambda x: x.date)[["accession", "cik", "ticker", "trade_date", "prev_close"]]
        val = valuation(con, v)[["accession", "mcap"]]
        ev["key"] = ev.ticker + "|" + ev.date.dt.strftime("%Y%m%d")
        ev = ev.merge(val.rename(columns={"accession": "key"}), on="key", how="left")
        big = ev.mcap >= BIG_CAP
        ev["mkt_r"] = np.where(big, ev.spy_r, ev.iwm_r)
        ev["fall_sigma"] = (ev.r - ev.mkt_r) / ev.sd20
        ev = ev[(ev.fall_sigma <= -K_SIGMA)]
        ev["no_news"] = (ev.n_8k == 0) & ~ev.no_cik
        n_all = len(ev)
        ev = thin(ev[ev.no_news], cal)
        log.info("falls vs the right benchmark: %d; without news (and a known company): %d after thinning", n_all, len(ev))
        P = Prices(con, sorted(set(ev.ticker)) + ["SPY", "IWM"])
    rows = []
    for r in ev.itertuples():
        i = P.locate(r.ticker, r.date)
        row = {"key": r.key}
        if i is not None:
            row.update(context(P, r.ticker, i))
            if "atr_20d" in row:
                row.update(outcomes(P, r.ticker, i, row["atr_20d"]))
                for sym in ("SPY", "IWM"):
                    row.update(bench(P, sym, r.date, row))
        rows.append(row)
    ev = ev.merge(pd.DataFrame(rows), on="key", how="left")
    ev["weekday"] = ev.date.dt.weekday
    ev["log_mcap"] = np.log(ev.mcap.where(ev.mcap > 0))
    ev["log_dvol"] = np.log(ev.dvol20)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ev.replace([np.inf, -np.inf], np.nan).to_parquet(OUT)
    log.info("reversal events: %d, with outcomes %d -> %s", len(ev), int(ev.e1_h5.notna().sum()), OUT)
    return ev


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.parse_args()
    build()


if __name__ == "__main__":
    main()
