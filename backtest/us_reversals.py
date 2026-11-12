"""No-news reversal backtest. Design: backtest/DESIGN_REVERSALS.md (fixed before any result).

    uv run python -W ignore -m backtest.us_reversals choose          # 2019–2021
    uv run python -W ignore -m backtest.us_reversals final --final   # 2022–2026, once, frozen, user's OK
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
FEAT = ROOT / "data" / "features" / "us_reversal_events.parquet"
CHOOSE, FINAL = [2019, 2020, 2021], [2022, 2023, 2024, 2025, 2026]
FIRST_PRED = 2015
EXITS = [f"{e}_{x}" for e in ("e1", "e2") for x in ("h1", "h3", "h5", "a5")]
BIG_CAP = 10e9
CLIP_Y = 0.3
FEATURES = ["fall_sigma", "r", "mkt_r", "ret_5d", "ret_20d", "ret_60d", "sd20", "log_dvol", "log_mcap",
            "off_high_52w", "off_low_52w", "days_since_earnings", "weekday", "day_range", "close_in_range"]


def hold_days(d: pd.DataFrame, x: str) -> pd.Series:
    if x.endswith("a5"):
        return d[f"{x}_days"]
    return pd.Series(int(x[-1]), index=d.index)


def load(final: bool = False) -> pd.DataFrame:
    d = pd.read_parquet(FEAT)
    d["trade_date"] = pd.to_datetime(d.date)
    if not final:
        d = d[d.trade_date < pd.Timestamp("2022-01-01")]
    d = d[d.e1_h5.notna() & d.ret_60d.notna()].copy()
    d["year"] = d.trade_date.dt.year
    big = d.mcap >= BIG_CAP
    for x in EXITS:
        d[f"y_{x}"] = d[x] - np.where(big, d[f"spy_{x}"], d[f"iwm_{x}"])
    d["prev_close"] = d.entry_close           # price for commission per share
    d["dollar_vol_20d"] = d.dvol20
    return d.reset_index(drop=True)


def predict(d: pd.DataFrame, years: list[int]) -> pd.Series:
    import lightgbm as lgb
    p = pd.Series(np.nan, index=d.index)
    for Y in range(FIRST_PRED, max(years) + 1):
        tr = d[(d.trade_date < pd.Timestamp(f"{Y - 1}-12-15")) & d.y_e1_h5.notna()]
        te = d.year == Y
        m = lgb.LGBMRegressor(**LGB).fit(tr[FEATURES].astype(float), tr.y_e1_h5.clip(-CLIP_Y, CLIP_Y))
        p[te] = m.predict(d.loc[te, FEATURES].astype(float))
    return p


def evaluate(d: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    d = d.copy()
    d["pred"] = predict(d, years)
    d["fall"] = -d.fall_sigma                  # S0 order: the largest fall first
    rows = []
    for Y in years:
        cut = d.pred[d.year == Y - 1].quantile(0.8)
        base = d[d.year == Y]
        for sname, s in (("S0", base), ("S1", base[base.pred > cut])):
            for x in EXITS:
                s2 = s[s[f"y_{x}"].notna()]
                days = hold_days(s2, x).astype(float)
                acct = ua.simulate(s2.assign(exit_date=s2.trade_date + pd.to_timedelta(np.ceil(days * 7 / 5) + 1, unit="D"),
                                             pred=s2.pred if sname == "S1" else s2.fall))
                k = acct[acct.taken]
                sp = spread_table(k.dollar_vol_20d.to_numpy())
                row = dict(year=Y, select=sname, exit=x, events=len(s2), taken=len(k), gross_x=s2[f"y_{x}"].mean(),
                           univ_x=base[f"y_{x}"].mean(), taken_gross_x=k[f"y_{x}"].mean())
                for br in ua.BROKERS:
                    c = ua.round_trip(br, k.prev_close.to_numpy(), sp)
                    row[f"{br}_vs_mkt_usd"] = float(((k[f"y_{x}"] - c) * ua.SIZE).sum())
                    row[f"{br}_actual_usd"] = float(((k[x] - c) * ua.SIZE).sum())
                rows.append(row)
    return pd.DataFrame(rows)


def run(stage: str, final: bool = False) -> str:
    if stage == "final" and not final:
        raise SystemExit("the test years are run once, after the user's OK: add --final")
    years = CHOOSE if stage == "choose" else FINAL
    d = load(final=(stage == "final"))
    log.info("reversal events usable: %d", len(d))
    res = evaluate(d, years)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT / f"us_reversals_{stage}.parquet")
    pool = res.groupby(["select", "exit"]).agg(
        events=("events", "sum"), taken=("taken", "sum"), gross_x=("gross_x", "mean"), univ_x=("univ_x", "mean"),
        t212=("trading212_vs_mkt_usd", "sum"), ibkr=("ibkr_fixed_vs_mkt_usd", "sum"),
        years_pos=("trading212_vs_mkt_usd", lambda s: int((s > 0).sum())))
    by_year = res.pivot_table(index=["select", "exit"], columns="year", values="trading212_vs_mkt_usd")
    return "\n".join(["# No-news reversals", (pool.assign(gross_x=pool.gross_x * 100, univ_x=pool.univ_x * 100)
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
