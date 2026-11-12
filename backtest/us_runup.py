"""Pre-earnings run-up backtest. Design: backtest/DESIGN_RUNUP.md (fixed before any result).

    uv run python -W ignore -m backtest.us_runup choose          # 2019–2021
    uv run python -W ignore -m backtest.us_runup final --final   # 2022–2026, once, frozen, user's OK
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from backtest import us_account as ua
from backtest.us_earnings import LGB, OUT
from backtest.us_insiders import spread_table
from core.config import ROOT
from core.log import get_logger

log = get_logger("backtest")
FEAT = ROOT / "data" / "features" / "us_runup_events.parquet"
CHOOSE, FINAL = [2019, 2020, 2021], [2022, 2023, 2024, 2025, 2026]
FIRST_PRED = 2015
WINDOWS = {"w1": 5, "w2": 7}                      # trading days held
UNIVERSES = {"U1": 5e6, "U2": 20e6}
FEATURES = ["past_w1", "past_w2", "n_past", "ret_20d", "ret_60d", "vol_20d", "log_dvol", "log_mcap", "month"]


def load(final: bool = False) -> pd.DataFrame:
    d = pd.read_parquet(FEAT)
    if not final:
        d = d[d.trade_date < pd.Timestamp("2022-01-01")]
    d = d[d.y_w2.notna() & (d.prev_close >= 5) & (d.dollar_vol_20d >= min(UNIVERSES.values()))].copy()
    d["year"] = d.trade_date.dt.year
    return d.reset_index(drop=True)


def predict(d: pd.DataFrame, years: list[int]) -> pd.Series:
    import lightgbm as lgb
    p = pd.Series(np.nan, index=d.index)
    for Y in range(FIRST_PRED, max(years) + 1):
        tr = d[d.trade_date < pd.Timestamp(f"{Y - 1}-12-15")]
        te = d.year == Y
        m = lgb.LGBMRegressor(**LGB).fit(tr[FEATURES].astype(float), tr.y_w2.clip(-0.3, 0.3))
        p[te] = m.predict(d.loc[te, FEATURES].astype(float))
    return p


def evaluate(d: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    d = d.copy()
    d["pred"] = predict(d, years)
    rows = []
    for uname, dv in UNIVERSES.items():
        u = d[d.dollar_vol_20d >= dv]
        for Y in years:
            cut = u.pred[u.year == Y - 1].quantile(0.8)
            base = u[u.year == Y]
            for sname, s in (("S0", base), ("S1", base[base.pred > cut])):
                for w, n in WINDOWS.items():
                    s2 = s[s[f"y_{w}"].notna()]
                    acct = ua.simulate(s2.assign(exit_date=s2.trade_date + pd.Timedelta(days=int(np.ceil(n * 7 / 5)) + 1),
                                                 pred=s2.pred if sname == "S1" else s2.past_w2.fillna(-1)))
                    k = acct[acct.taken]
                    sp = spread_table(k.dollar_vol_20d.to_numpy())
                    row = dict(universe=uname, year=Y, select=sname, window=w, events=len(s2), taken=len(k),
                               gross_x=s2[f"y_{w}"].mean(), taken_gross_x=k[f"y_{w}"].mean())
                    for br in ua.BROKERS:
                        c = ua.round_trip(br, k.prev_close.to_numpy(), sp)
                        row[f"{br}_vs_mkt_usd"] = float(((k[f"y_{w}"] - c) * ua.SIZE).sum())
                        row[f"{br}_actual_usd"] = float(((k[w] - c) * ua.SIZE).sum())
                    rows.append(row)
    return pd.DataFrame(rows)


def run(stage: str, final: bool = False) -> str:
    if stage == "final" and not final:
        raise SystemExit("the test years are run once, after the user's OK: add --final")
    years = CHOOSE if stage == "choose" else FINAL
    d = load(final=(stage == "final"))
    log.info("run-up events usable: %d", len(d))
    res = evaluate(d, years)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT / f"us_runup_{stage}.parquet")
    pool = res.groupby(["universe", "select", "window"]).agg(
        events=("events", "sum"), taken=("taken", "sum"), gross_x=("gross_x", "mean"),
        taken_gross_x=("taken_gross_x", "mean"), t212=("trading212_vs_mkt_usd", "sum"),
        ibkr=("ibkr_fixed_vs_mkt_usd", "sum"), years_pos=("trading212_vs_mkt_usd", lambda s: int((s > 0).sum())))
    by_year = res.pivot_table(index=["universe", "select", "window"], columns="year", values="trading212_vs_mkt_usd")
    return "\n".join(["# Pre-earnings run-up", (pool.assign(gross_x=pool.gross_x * 100, taken_gross_x=pool.taken_gross_x * 100)
                                                  .round(2)).to_string(), "", "Trading 212 $ vs market by year:",
                      by_year.round(0).to_string()])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["choose", "final"])
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    print(run(a.stage, a.final))


if __name__ == "__main__":
    main()
