"""Insider-buying events (design: backtest/DESIGN_INSIDERS.md).

    uv run python -W ignore -m features.us_insiders build    # -> data/features/us_insider_events.parquet

One row per company per filing date with qualifying open-market purchases by officers/directors.
Trade = the OPEN of the first trading day after the filing date (only the date is known).
Features use prices up to the close before the trade and filings made before it. Outcomes:
  h20 / h60  open of day 1 -> close of day 20 / 60
  t60        up to 60 days; trailing stop 3 x ATR20 below the highest close (checked on closes,
             sold at the next open), else the close of day 60
each also for SPY and IWM over the same days. Windows touching a stale/suspect bar get no outcome."""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from core.config import ROOT
from core.log import get_logger
from features.us_earnings import Prices, _pick_ticker, _tickers, fundamentals
from features.us_earnings_labels import split_factor, valuation
from ingest.us.common import session

log = get_logger("features")
OUT = ROOT / "data" / "features" / "us_insider_events.parquet"
MAX_LAG_D, NEAR_MKT, CLUSTER_D, PLACEMENT_D = 10, 0.10, 30, 10
HOLDS = {"h20": 20, "h60": 60}
TRAIL_ATR, TRAIL_MAX = 3.0, 60
ROLE_BIG = r"(?i)\b(ceo|cfo|chief executive|chief financial|chair)"


# ---------------------------------------------------------------- qualifying purchases
def purchases(con) -> pd.DataFrame:
    """Rules 1–3 and 5 of the design, amendments de-duplicated (earliest filing kept)."""
    return con.execute(f"""
        WITH p AS (
            SELECT *, row_number() OVER (PARTITION BY issuer_cik, owner_ciks, trans_date, shares, price
                                         ORDER BY filing_date, accession) AS dup
            FROM us_insider_tx
            WHERE trans_code = 'P' AND acq_disp = 'A' AND doc_type IN ('4', '4/A')
              AND (is_director = 1 OR is_officer = 1) AND shares > 0 AND price > 0
              AND regexp_matches(lower(security_title), 'common|ordinary|class a')
              AND coalesce(aff10b5one, false) = false
              AND date_diff('day', trans_date, filing_date) BETWEEN 0 AND {MAX_LAG_D})
        SELECT accession, filing_date, issuer_cik AS cik, issuer_ticker, owner_ciks, titles, trans_date,
               shares, price, shares_after FROM p WHERE dup = 1""").df()


def routine_owners(con) -> set[tuple[str, int, int]]:
    """(owner, year, month) of every qualifying-looking purchase, to flag 'routine' buyers."""
    rows = con.execute("""SELECT DISTINCT owner_ciks, year(trans_date), month(trans_date) FROM us_insider_tx
                          WHERE trans_code = 'P' AND acq_disp = 'A'""").fetchall()
    return {(o, y, m) for o, y, m in rows}


def build_events(con, px: pd.DataFrame, cal: np.ndarray) -> pd.DataFrame:
    px = px.copy()
    px["filing_date"] = pd.to_datetime(px.filing_date)
    px["trans_date"] = pd.to_datetime(px.trans_date)
    px["dollars"] = px.shares * px.price
    px["bet"] = (px.shares / px.shares_after).where(px.shares_after > 0)
    px["big_role"] = px.titles.fillna("").str.contains(ROLE_BIG)
    routine = routine_owners(con)
    px["routine"] = [all((o, y - k, m) in routine for k in (1, 2, 3))
                     for o, y, m in zip(px.owner_ciks, px.trans_date.dt.year, px.trans_date.dt.month)]
    ev = px.groupby(["cik", "filing_date"]).agg(
        dollars=("dollars", "sum"), n_buyers_today=("owner_ciks", "nunique"), big_role=("big_role", "max"),
        bet=("bet", "median"), all_routine=("routine", "min"), any_routine=("routine", "max"),
        trans_date=("trans_date", "min"), avg_price=("price", "mean"), symbol=("issuer_ticker", "first"),
        n_tx=("accession", "size")).reset_index()
    # cluster: distinct buyers in the 30 days up to the filing date
    b = px[["cik", "filing_date", "owner_ciks"]].drop_duplicates()
    con.register("b", b)
    cl = con.execute(f"""
        SELECT e.cik, e.filing_date, count(DISTINCT x.owner_ciks) AS n_buyers_30d
        FROM (SELECT DISTINCT cik, filing_date FROM b) e JOIN b x ON x.cik = e.cik
             AND x.filing_date BETWEEN e.filing_date - INTERVAL {CLUSTER_D} DAY AND e.filing_date
        GROUP BY 1, 2""").df()
    ev = ev.merge(cl, on=["cik", "filing_date"], how="left")
    # private placements: an 8-K item 3.02 in the 10 days before the filing
    con.register("e", ev[["cik", "filing_date"]])
    pl = con.execute(f"""
        SELECT DISTINCT e.cik, e.filing_date FROM e JOIN sec_filings f ON f.cik = e.cik
            AND list_contains(string_split(f.items, ','), '3.02')
            AND f.filing_date BETWEEN e.filing_date - INTERVAL {PLACEMENT_D} DAY AND e.filing_date""").df()
    ev = ev.merge(pl.assign(placement_8k=True), on=["cik", "filing_date"], how="left")
    ev["placement_8k"] = ev.placement_8k.fillna(False).astype(bool)
    # trade: the first trading day after the filing date
    k = np.searchsorted(cal, ev.filing_date.to_numpy().astype("datetime64[D]"), side="right")
    ev["trade_date"] = pd.to_datetime(np.where(k < len(cal), cal[np.minimum(k, len(cal) - 1)],
                                               np.datetime64("NaT")))
    ev["event_id"] = ev.cik.astype(str) + "-" + ev.filing_date.dt.strftime("%Y%m%d")
    return ev


# ---------------------------------------------------------------- prices and outcomes
def trail_exit(o, h, l, c, entry_i: int, end: int, atr: float, k: float = TRAIL_ATR, max_d: int = TRAIL_MAX):
    """-> (exit index, exit price, 'open'|'close'). Stop = highest close - k*ATR, checked on closes;
    breached -> sell at the next open; otherwise the close of day max_d."""
    last = entry_i + max_d - 1
    if last >= end:
        return None
    hi = c[entry_i]
    for j in range(entry_i, last + 1):
        hi = max(hi, c[j])
        if c[j] < hi - k * atr and j < last:
            return j + 1, o[j + 1], "open"
    return last, c[last], "close"


def price_block(P: Prices, t: str, i: int) -> dict:
    o, h, l, c, v = (P.cols[x] for x in ["open", "high", "low", "close", "volume"])
    s, e = P.span[t]
    out: dict = {}
    if i - s < 61:
        return {"skip": "short_history"}
    hist = slice(i - 252 if i - s >= 252 else s, i)
    ch = c[hist]
    out.update(prev_close=c[i - 1], ret_5d=c[i - 1] / c[i - 6] - 1, ret_20d=c[i - 1] / c[i - 21] - 1,
               ret_60d=c[i - 1] / c[i - 61] - 1, vol_20d=float(np.std(np.diff(np.log(c[i - 21:i])))),
               dollar_vol_20d=float(np.mean(c[i - 20:i] * v[i - 20:i])),
               off_high_52w=c[i - 1] / ch.max() - 1, off_low_52w=c[i - 1] / ch.min() - 1,
               hist_bad=bool(P.bad[i - 21:i].any()))
    tr = np.maximum(h[i - 20:i], c[i - 21:i - 1]) - np.minimum(l[i - 20:i], c[i - 21:i - 1])
    out["atr_20d"] = float(np.mean(tr))
    out["entry_open"] = o[i]
    for name, n in HOLDS.items():
        if i + n - 1 < e and not P.bad[i:i + n].any() and o[i] > 0:
            out[f"r_{name}"] = c[i + n - 1] / o[i] - 1
            out[f"end_{name}"] = i + n - 1
    tx = trail_exit(o, h, l, c, i, e, out["atr_20d"])
    if tx and not P.bad[i:tx[0] + 1].any() and o[i] > 0:
        out["r_t60"], out["end_t60"], out["t60_how"] = tx[1] / o[i] - 1, tx[0], tx[2]
        out["t60_days"] = tx[0] - i + 1
    return out


def bench(P: Prices, sym: str, day, row: dict, t_i: int, t_end: dict) -> dict:
    """Benchmark over the same trading days (aligned by count from its own entry day)."""
    i = P.locate(sym, day)
    if i is None:
        return {}
    o, c = P.cols["open"], P.cols["close"]
    _, e = P.span[sym]
    out = {}
    for name, n in HOLDS.items():
        if f"r_{name}" in row and i + n - 1 < e:
            out[f"{sym.lower()}_{name}"] = c[i + n - 1] / o[i] - 1
    if "r_t60" in row:
        k = row["t60_days"] - 1
        if i + k < e:
            px_ = o[i + k] if row["t60_how"] == "open" else c[i + k]
            out[f"{sym.lower()}_t60"] = px_ / o[i] - 1
    return out


def build() -> pd.DataFrame:
    with session(read_only=True, memory_limit="6GB") as con:
        cal = con.execute("SELECT date FROM us_prices_daily WHERE ticker='SPY' AND NOT is_stale ORDER BY 1") \
            .df()["date"].to_numpy().astype("datetime64[D]")
        px = purchases(con)
        log.info("qualifying purchases: %d", len(px))
        ev = build_events(con, px, cal)
        ev = ev[ev.trade_date.notna()].reset_index(drop=True)
        tk = _tickers(con)
        ev["ticker"] = _pick_ticker(ev, tk)
        # fall back on the filing's own symbol when it has prices covering the date
        miss = ev.ticker.isna() & ev.symbol.isin(set(tk.ticker))
        ev.loc[miss, "ticker"] = ev.loc[miss, "symbol"]
        P = Prices(con, sorted(set(ev.ticker.dropna())) + ["SPY", "IWM"])
        rows = []
        for r in ev.itertuples():
            i = P.locate(r.ticker, r.trade_date) if isinstance(r.ticker, str) else None
            row = {"event_id": r.event_id}
            if i is None or P.stale[i]:
                row["skip"] = "no_price_on_trade_date" if isinstance(r.ticker, str) else "no_ticker"
            else:
                row.update(price_block(P, r.ticker, i))
                if "skip" not in row:
                    for sym in ("SPY", "IWM"):
                        row.update(bench(P, sym, r.trade_date, row, i, {}))
            rows.append(row)
        ev = ev.merge(pd.DataFrame(rows), on="event_id", how="left")
        ok = ev.skip.isna()
        # near-market check: the reported price vs that day's close, undone back to the raw price
        splits = con.execute("SELECT ticker, date, ratio FROM us_splits").df()
        splits["date"] = pd.to_datetime(splits.date)
        d = ev.loc[ok, ["event_id", "ticker", "trans_date"]].copy()
        con.register("d", d)
        cl = con.execute("""SELECT d.event_id, arg_max(p.close, p.date) AS close_td FROM d JOIN us_prices_daily p
                            ON p.ticker = d.ticker AND p.date <= d.trans_date AND p.date > d.trans_date - INTERVAL 7 DAY
                            GROUP BY 1""").df()
        d = d.merge(cl, on="event_id", how="left").reset_index(drop=True)
        d["close_raw"] = d.close_td * split_factor(splits, d, "trans_date")
        ev = ev.merge(d[["event_id", "close_raw"]], on="event_id", how="left")
        ev["px_vs_mkt"] = ev.avg_price / ev.close_raw - 1
        ev["at_market"] = ev.px_vs_mkt.abs() <= NEAR_MKT
        # size and value, from filings before the trade (reuses the earnings code)
        v = ev.loc[ok, ["event_id", "cik", "ticker", "trade_date", "prev_close"]].rename(columns={"event_id": "accession"})
        val = valuation(con, v).rename(columns={"accession": "event_id"})
        fin = fundamentals(con, v[["accession", "cik", "trade_date"]]).rename(columns={"accession": "event_id"})
    ev = ev.merge(val, on="event_id", how="left").merge(
        fin[["event_id", "ni_fy", "equity", "rev_fy", "profitable"]], on="event_id", how="left")
    with np.errstate(divide="ignore", invalid="ignore"):
        ev["log_mcap"] = np.log(ev.mcap.where(ev.mcap > 0))
        ev["earnings_yield"] = (ev.ni_fy / ev.mcap).clip(-1, 1)
        ev["book_to_market"] = (ev.equity / ev.mcap).clip(-1, 10)
        ev["dollars_to_mcap"] = (ev.dollars / ev.mcap).clip(0, 0.2)
    ev["log_dollars"] = np.log(ev.dollars.clip(lower=1))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ev.replace([np.inf, -np.inf], np.nan).to_parquet(OUT)
    log.info("insider events: %d; priced %d; at market %d; placements flagged %d -> %s", len(ev), int(ok.sum()),
             int(ev.at_market.sum()), int(ev.placement_8k.sum()), OUT)
    return ev


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.parse_args()
    build()


if __name__ == "__main__":
    main()
