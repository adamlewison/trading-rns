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
        fin = fundamentals(con, ev.dropna(subset=["trade_date"]))
        ev = ev.merge(items, on="accession", how="left")
        flags = item_flags(con, ev.dropna(subset=["trade_date"]))
        hist = eps_history(con, ev)
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
    out = (ev.merge(pd.DataFrame(rows), on="accession", how="left").merge(fin, on="accession", how="left")
             .merge(flags, on="accession", how="left").merge(hist, on="accession", how="left"))
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



# ---------------------------------------------------------------- financials (point in time)
INSTANT = {"StockholdersEquity": "equity", "CashAndCashEquivalentsAtCarryingValue": "cash", "Assets": "assets",
           "LongTermDebt": "lt_debt", "EntityCommonStockSharesOutstanding": "shares_out"}
ANNUAL = {"NetIncomeLoss": "ni", "OperatingIncomeLoss": "op_inc", "Revenues": "rev",
          "RevenueFromContractWithCustomerExcludingAssessedTax": "rev", "SalesRevenueNet": "rev"}


def fundamentals(con, ev: pd.DataFrame) -> pd.DataFrame:
    """Latest balance-sheet values and the last two fiscal years of income-statement values, using only
    facts filed before the trade date and, for each period, the first filed value. No price is used:
    our prices are split-adjusted to today while filings report the share counts of their time, so
    valuation ratios wait for split histories."""
    con.register("ev", ev[["accession", "cik", "trade_date"]])
    con.execute(f"""CREATE OR REPLACE TEMP TABLE ff AS
        SELECT cik, concept, start_date, end_date, min(filed) AS filed, arg_min(val, filed) AS val,
               date_diff('day', start_date, end_date) AS dur
        FROM us_facts
        WHERE concept IN ({','.join(repr(c) for c in list(INSTANT) + list(ANNUAL))})
          AND unit IN ('USD', 'shares')
        GROUP BY ALL""")
    inst = con.execute(f"""
        SELECT e.accession, f.concept, arg_max(f.val, f.end_date) AS val, max(f.end_date) AS end_date
        FROM ev e JOIN ff f ON f.cik = e.cik AND f.filed < e.trade_date
             AND f.end_date > e.trade_date - INTERVAL 500 DAY
        WHERE f.start_date IS NULL AND f.concept IN ({','.join(repr(c) for c in INSTANT)})
        GROUP BY ALL""").df()
    ann = con.execute(f"""
        SELECT e.accession, f.concept, arg_max(f.val, f.end_date, 2) AS vals, max(f.end_date) AS end_date
        FROM ev e JOIN ff f ON f.cik = e.cik AND f.filed < e.trade_date
             AND f.end_date > e.trade_date - INTERVAL 900 DAY
        WHERE f.dur BETWEEN 350 AND 380 AND f.concept IN ({','.join(repr(c) for c in ANNUAL)})
        GROUP BY ALL""").df()
    out = ev[["accession"]].copy()
    if len(inst):
        wide = inst.assign(name=inst.concept.map(INSTANT)).pivot_table(index="accession", columns="name",
                                                                          values="val", aggfunc="first")
        out = out.merge(wide, left_on="accession", right_index=True, how="left")
    if len(ann):
        ann["name"] = ann.concept.map(ANNUAL)
        ann["last"] = ann.vals.map(lambda v: v[0] if len(v) else np.nan)
        ann["prev"] = ann.vals.map(lambda v: v[1] if len(v) > 1 else np.nan)
        # several revenue concepts: keep the one with the most recent fiscal year
        ann = ann.sort_values("end_date").drop_duplicates(["accession", "name"], keep="last")
        last = ann.pivot(index="accession", columns="name", values="last").add_suffix("_fy")
        prev = ann.pivot(index="accession", columns="name", values="prev").add_suffix("_fy_prev")
        out = out.merge(last, left_on="accession", right_index=True, how="left") \
                 .merge(prev, left_on="accession", right_index=True, how="left")
    f = out
    with np.errstate(divide="ignore", invalid="ignore"):
        f["profitable"] = (f.get("ni_fy") > 0).astype("float").where(f.get("ni_fy").notna())
        f["rev_growth"] = f.get("rev_fy") / f.get("rev_fy_prev") - 1
        f["ni_margin"] = f.get("ni_fy") / f.get("rev_fy")
        f["roe"] = f.get("ni_fy") / f.get("equity")
        f["debt_to_assets"] = f.get("lt_debt") / f.get("assets")
        f["cash_to_assets"] = f.get("cash") / f.get("assets")
        f["log_assets"] = np.log(f.get("assets").where(f.get("assets") > 0))
        f["cash_runway_y"] = (f.get("cash") / -f.get("ni_fy")).where(f.get("ni_fy") < 0)
    return f.replace([np.inf, -np.inf], np.nan)



# ---------------------------------------------------------------- 8-K items (this filing and recent ones)
ITEMS = {"1.01": "agreement", "1.02": "agreement_end", "1.03": "bankruptcy", "2.01": "acquisition_done",
         "2.03": "new_debt", "2.05": "exit_costs", "2.06": "impairment", "3.01": "delisting_notice",
         "4.01": "auditor_change", "4.02": "non_reliance", "5.02": "exec_change", "7.01": "reg_fd",
         "8.01": "other_event"}


def item_flags(con, ev: pd.DataFrame) -> pd.DataFrame:
    """Items on the release's own 8-K, and counts of the company's other 8-K items in the 90 days before."""
    own = ev[["accession", "items"]].copy()
    for code, name in ITEMS.items():
        own[f"item_{name}"] = own["items"].fillna("").str.split(",").map(lambda xs, c=code: c in xs).astype(int)
    con.register("ev2", ev[["accession", "cik", "trade_date"]])
    prior = con.execute("""
        SELECT e.accession, count(*) AS n_8k_90d,
               sum(list_contains(string_split(f.items, ','), '5.02')::INT) AS n_exec_change_90d,
               sum(list_contains(string_split(f.items, ','), '4.02')::INT) AS n_non_reliance_90d,
               sum(list_contains(string_split(f.items, ','), '1.01')::INT) AS n_agreement_90d
        FROM ev2 e JOIN sec_filings f ON f.cik = e.cik AND f.form LIKE '8-K%'
             AND f.filing_date < e.trade_date AND f.filing_date >= e.trade_date - INTERVAL 90 DAY
             AND f.accession <> e.accession
        GROUP BY 1""").df()
    out = own.drop(columns="items").merge(prior, on="accession", how="left")
    return out.fillna({c: 0 for c in prior.columns if c != "accession"})


# ---------------------------------------------------------------- EPS history (for the earnings surprise)
def eps_quarters(con) -> pd.DataFrame:
    """First-filed quarterly diluted EPS per company: Q1-Q3 from 10-Q durations of ~3 months, Q4 as the
    fiscal year minus the three quarters (approximate: per-share figures are not exactly additive).
    `filed` is when the value became public (for Q4, the 10-K)."""
    df = con.execute("""
        SELECT cik, start_date, end_date, min(filed) AS filed, arg_min(val, filed) AS eps,
               date_diff('day', start_date, end_date) AS dur
        FROM us_facts
        WHERE concept = 'EarningsPerShareDiluted' AND unit = 'USD/shares' AND start_date IS NOT NULL
        GROUP BY ALL HAVING dur BETWEEN 80 AND 100 OR dur BETWEEN 350 AND 380""").df()
    q = df[df.dur <= 100].drop(columns="dur")
    fy = df[df.dur >= 350]
    # Q4: quarters ending inside each fiscal year, before its end
    m = fy.merge(q, on="cik", suffixes=("", "_q"))
    m = m[(m.start_date_q >= m.start_date - pd.Timedelta(days=5)) & (m.end_date_q < m.end_date - pd.Timedelta(days=20))]
    g = m.groupby(["cik", "start_date", "end_date"]).agg(n=("eps_q", "size"), s=("eps_q", "sum")).reset_index()
    q4 = fy.merge(g[g.n == 3], on=["cik", "start_date", "end_date"])
    q4 = q4.assign(eps=q4.eps - q4.s, start_date=q4.end_date - pd.Timedelta(days=91))[["cik", "start_date", "end_date", "filed", "eps"]]
    out = pd.concat([q.assign(q4_derived=False), q4.assign(q4_derived=True)], ignore_index=True)
    return out.sort_values(["cik", "end_date", "filed"]).drop_duplicates(["cik", "end_date"])


def eps_history(con, ev: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    """Per release: the scale of the company's past year-on-year EPS changes (std of the last `n`
    seasonal differences known before the trade), the input for SUE = (EPS - EPS a year ago) / scale."""
    q = eps_quarters(con)
    q["end_date"] = pd.to_datetime(q["end_date"]); q["filed"] = pd.to_datetime(q["filed"])
    q = q.sort_values(["cik", "end_date"])
    # seasonal difference: same quarter a year earlier (end dates 350-380 days apart)
    prev = q[["cik", "end_date", "eps"]].rename(columns={"end_date": "end_prev", "eps": "eps_prev"})
    q["key"] = q["end_date"] - pd.Timedelta(days=365)
    d = pd.merge_asof(q.sort_values("key"), prev.sort_values("end_prev"), left_on="key", right_on="end_prev",
                      by="cik", tolerance=pd.Timedelta(days=20), direction="nearest")
    d["sdiff"] = d["eps"] - d["eps_prev"]
    d = d.dropna(subset=["sdiff"]).sort_values(["cik", "filed"])
    out = []
    by = {k: g for k, g in d.groupby("cik")}
    for r in ev[["accession", "cik", "trade_date"]].dropna().itertuples():
        g = by.get(r.cik)
        if g is None:
            continue
        known = g[g.filed < r.trade_date].tail(n)
        if len(known) >= 4:
            out.append((r.accession, float(known.sdiff.std(ddof=1)), len(known),
                        float(known.eps.iloc[-1]), known.end_date.iloc[-1]))
    return pd.DataFrame(out, columns=["accession", "eps_sdiff_std", "eps_sdiff_n", "eps_last_known", "eps_last_end"])


if __name__ == "__main__":
    main()
