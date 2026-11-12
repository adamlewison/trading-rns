"""Pre-earnings run-up events (design: backtest/DESIGN_RUNUP.md).

    uv run python -W ignore -m features.us_runup build   # -> data/features/us_runup_events.parquet

For every earnings release X, the next year's same-quarter reaction day R is expected 364 days later.
Entry: the close 6 trading days before R (index R-6). Outcomes (each also for SPY and IWM):
  w1 = close R-1 / close R-6 - 1     (run-up only)
  w2 = close R+1 / close R-6 - 1     (through the release)"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from core.config import ROOT
from core.log import get_logger
from features.us_earnings import Prices, _pick_ticker, _tickers
from features.us_earnings_labels import valuation
from ingest.us.common import session

log = get_logger("features")
OUT = ROOT / "data" / "features" / "us_runup_events.parquet"
LEAD = 6                     # entry = close of trading day R - LEAD
BIG_CAP = 10e9


def reaction_index(cal: np.ndarray, when: pd.Series, session_: pd.Series) -> np.ndarray:
    """Index in `cal` of the first trading session that sees a release made at `when` (ET)."""
    d = when.dt.normalize().to_numpy().astype("datetime64[D]")
    after = (session_ == "after_close").to_numpy() | (when.dt.hour >= 16).to_numpy()
    k = np.searchsorted(cal, d, side="left")              # first trading day >= the date
    same_day_trading = (k < len(cal)) & (cal[np.minimum(k, len(cal) - 1)] == d)
    return np.where(same_day_trading & after, k + 1, k)


def build() -> pd.DataFrame:
    with session(read_only=True, memory_limit="8GB") as con:
        cal = con.execute("SELECT date FROM us_prices_daily WHERE ticker='SPY' AND NOT is_stale ORDER BY 1") \
            .df()["date"].to_numpy().astype("datetime64[D]")
        rel = con.execute("""SELECT accession, cik, acceptance_et, session FROM us_filing_docs
                             WHERE item_group = 'earnings' AND status = 'ok' AND acceptance_et IS NOT NULL""").df()
        rel["acceptance_et"] = pd.to_datetime(rel.acceptance_et)
        rel = rel.sort_values("acceptance_et").drop_duplicates(["cik", "acceptance_et"])
        rel["r_idx"] = reaction_index(cal, rel.acceptance_et, rel.session)
        # expected next-year release: same clock time, 364 days later
        exp = rel.assign(acceptance_et=rel.acceptance_et + pd.Timedelta(days=364))
        exp["R"] = reaction_index(cal, exp.acceptance_et, exp.session)
        exp = exp[(exp.R >= LEAD) & (exp.R + 1 < len(cal))].copy()
        exp["entry_date"] = pd.to_datetime(cal[exp.R - LEAD])
        exp["R_date"] = pd.to_datetime(cal[exp.R])
        # how close the forecast was: the company's actual next release around R
        act = rel[["cik", "r_idx"]].rename(columns={"r_idx": "actual_idx"}).sort_values("actual_idx")
        exp = pd.merge_asof(exp.sort_values("R"), act, left_on="R", right_on="actual_idx", by="cik",
                            direction="nearest", tolerance=30)
        exp["date_error"] = exp.actual_idx - exp.R
        exp = exp.rename(columns={"entry_date": "trade_date"})
        tk = _tickers(con)
        exp["ticker"] = _pick_ticker(exp.reset_index(drop=True), tk).to_numpy()
        exp = exp[exp.ticker.notna()].reset_index(drop=True)
        P = Prices(con, sorted(set(exp.ticker)) + ["SPY", "IWM"])
        rows = []
        o, c, v = P.cols["open"], P.cols["close"], P.cols["volume"]
        for r in exp.itertuples():
            i = P.locate(r.ticker, r.trade_date)                 # entry day (R-6)
            row = {"accession": r.accession}
            if i is None:
                rows.append(row); continue
            s, e = P.span[r.ticker]
            j1, j2 = i + LEAD - 1, i + LEAD + 1                  # R-1, R+1 (by the stock's own bars)
            if i - s < 61 or j2 >= e or P.bad[i - 20:j2 + 1].any() or c[i] <= 0:
                rows.append(row); continue
            row.update(prev_close=c[i], w1=c[j1] / c[i] - 1, w2=c[j2] / c[i] - 1,
                       ret_20d=c[i] / c[i - 20] - 1, ret_60d=c[i] / c[i - 60] - 1,
                       vol_20d=float(np.std(np.diff(np.log(c[i - 20:i + 1])))),
                       dollar_vol_20d=float(np.mean(c[i - 19:i + 1] * v[i - 19:i + 1])))
            for sym in ("SPY", "IWM"):
                b = P.locate(sym, r.trade_date)
                if b is not None:
                    row[f"{sym.lower()}_w1"] = c[b + LEAD - 1] / c[b] - 1
                    row[f"{sym.lower()}_w2"] = c[b + LEAD + 1] / c[b] - 1
            rows.append(row)
        exp = exp.merge(pd.DataFrame(rows), on="accession", how="left")
        ok = exp.w2.notna()
        val = valuation(con, exp.loc[ok, ["accession", "cik", "ticker", "trade_date", "prev_close"]])
    exp = exp.merge(val[["accession", "mcap"]], on="accession", how="left")
    big = exp.mcap >= BIG_CAP
    for w in ("w1", "w2"):
        exp[f"y_{w}"] = exp[w] - np.where(big, exp[f"spy_{w}"], exp[f"iwm_{w}"])
    # the company's past windows, only ones finished before this entry
    exp = exp.sort_values(["cik", "trade_date"]).reset_index(drop=True)
    exp["exit_date"] = pd.to_datetime(cal[np.minimum(exp.R + 1, len(cal) - 1)])
    for w in ("w1", "w2"):
        past = []
        for _, g in exp.groupby("cik", sort=False):
            vals = []
            for td, y, ex in zip(g.trade_date, g[f"y_{w}"], g.exit_date):
                done = [vy for vy, vex in vals if vex < td][-4:]
                past.append(np.mean(done) if done else np.nan)
                if not np.isnan(y):
                    vals.append((y, ex))
        exp[f"past_{w}"] = past
    exp["n_past"] = exp.groupby("cik").cumcount().clip(upper=4)
    exp["log_mcap"] = np.log(exp.mcap.where(exp.mcap > 0))
    exp["log_dvol"] = np.log(exp.dollar_vol_20d)
    exp["month"] = exp.trade_date.dt.month
    OUT.parent.mkdir(parents=True, exist_ok=True)
    exp.replace([np.inf, -np.inf], np.nan).to_parquet(OUT)
    log.info("run-up events: %d; with outcomes %d; forecast within ±3 days: %.0f%% -> %s", len(exp),
             int(exp.w2.notna().sum()), 100 * (exp.date_error.abs() <= 3).mean(), OUT)
    return exp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.parse_args()
    build()


if __name__ == "__main__":
    main()
