"""Insider-buying backtest. Design: backtest/DESIGN_INSIDERS.md (fixed before any result).

    uv run python -W ignore -m backtest.us_insiders choose          # 2019–2021
    uv run python -W ignore -m backtest.us_insiders final --final   # 2022–2026, once, frozen, user's OK
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from backtest import us_account as ua
from backtest.us_earnings import LGB, OUT
from core.config import ROOT
from core.log import get_logger

log = get_logger("backtest")
FEAT = ROOT / "data" / "features" / "us_insider_events.parquet"
CHOOSE, FINAL = [2019, 2020, 2021], [2022, 2023, 2024, 2025, 2026]
FIRST_PRED = 2015
EXITS = {"h20": 20, "h60": 60, "t60": 60}
UNIVERSES = {"U1": 2e6, "U2": 20e6}
BIG_CAP = 10e9
CLIP_Y = 0.6
FEATURES = ["log_dollars", "n_buyers_today", "n_buyers_30d", "big_role", "bet", "all_routine", "any_routine",
            "ret_5d", "ret_20d", "ret_60d", "vol_20d", "dollar_vol_20d", "off_high_52w", "off_low_52w",
            "log_mcap", "earnings_yield", "book_to_market", "dollars_to_mcap", "profitable", "px_vs_mkt"]


def spread_table(dvol: np.ndarray) -> np.ndarray:
    """Full spread by 20-day dollar volume (design; an estimate)."""
    return np.select([dvol >= 5e8, dvol >= 1e8, dvol >= 2e7, dvol >= 5e6], [0.0005, 0.0010, 0.0020, 0.0040], 0.0080)


def load(final: bool = False) -> pd.DataFrame:
    d = pd.read_parquet(FEAT)
    d["trade_date"] = pd.to_datetime(d.trade_date)
    if not final:
        d = d[d.trade_date < pd.Timestamp("2022-01-01")]
    d = d[d.skip.isna() & d.at_market.fillna(False) & ~d.placement_8k & ~d.hist_bad.fillna(True).astype(bool)]
    d = d[(d.prev_close >= 1) & (d.dollar_vol_20d >= min(UNIVERSES.values()))].copy()
    d["year"] = d.trade_date.dt.year
    big = d.mcap >= BIG_CAP
    for x in EXITS:
        d[f"y_{x}"] = np.where(big, d[f"r_{x}"] - d[f"spy_{x}"], d[f"r_{x}"] - d[f"iwm_{x}"])
    d["mkt_t60"] = np.where(big, d.spy_t60, d.iwm_t60)
    for c in ("big_role", "all_routine", "any_routine", "profitable"):
        d[c] = d[c].astype(float)
    d["s1"] = (d.n_buyers_30d >= 2) | (d.big_role.astype(bool) & (d.dollars >= 50_000))
    return d.reset_index(drop=True)


def predict(d: pd.DataFrame, years: list[int]) -> pd.Series:
    """Walk-forward LightGBM on the 60-day excess return; training rows end before the year starts."""
    import lightgbm as lgb
    p = pd.Series(np.nan, index=d.index)
    for Y in range(FIRST_PRED, max(years) + 1):
        tr = d[(d.trade_date < pd.Timestamp(f"{Y - 1}-09-15")) & d.y_h60.notna()]
        te = d.year == Y
        m = lgb.LGBMRegressor(**LGB).fit(tr[FEATURES].astype(float), tr.y_h60.clip(-CLIP_Y, CLIP_Y))
        p[te] = m.predict(d.loc[te, FEATURES].astype(float))
    return p


def account(x: pd.DataFrame, exit_: str, order: str) -> pd.DataFrame:
    """The $6k account: $150 each, 40 slots, best first within a day (by `order`), skipped when full."""
    days = x[f"{exit_}_days"] if exit_ == "t60" else EXITS[exit_]
    x = x.assign(exit_date=x.trade_date + pd.to_timedelta(np.ceil(np.asarray(days, float) * 7 / 5), unit="D"),
                 pred=x[order])
    return ua.simulate(x)


def evaluate(d: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    d = d.copy()
    d["pred"] = predict(d, years)
    d["t60_days"] = d["t60_days"].fillna(60)
    rows = []
    for uname, dv in UNIVERSES.items():
        u = d[d.dollar_vol_20d >= dv]
        for Y in years:
            cut = u.pred[u.year == Y - 1].quantile(0.8)
            base = u[u.year == Y]
            sels = {"S0": base, "S1": base[base.s1], "S2": base[base.pred > cut]}
            for sname, s in sels.items():
                for x in EXITS:
                    s2 = s[s[f"y_{x}"].notna()]
                    acct = account(s2, x, "pred" if sname == "S2" else "dollars")
                    k = acct[acct.taken]
                    sp = spread_table(k.dollar_vol_20d.to_numpy())
                    row = dict(universe=uname, year=Y, select=sname, exit=x, events=len(s2), taken=len(k),
                               gross_x=s2[f"y_{x}"].mean(), univ_x=base[f"y_{x}"].mean(),
                               taken_gross_x=k[f"y_{x}"].mean())
                    for br in ua.BROKERS:
                        c = ua.round_trip(br, k.prev_close.to_numpy(), sp)
                        row[f"{br}_vs_mkt_usd"] = float(((k[f"y_{x}"] - c) * ua.SIZE).sum())
                        row[f"{br}_actual_usd"] = float(((k[f"r_{x}"] - c) * ua.SIZE).sum())
                    rows.append(row)
    return pd.DataFrame(rows)


def run(stage: str, final: bool = False) -> str:
    if stage == "final" and not final:
        raise SystemExit("the test years are run once, after the user's OK: add --final")
    years = CHOOSE if stage == "choose" else FINAL
    d = load(final=(stage == "final"))
    log.info("insider events usable: %d (%s)", len(d), d.year.value_counts().sort_index().to_dict())
    res = evaluate(d, years)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT / f"us_insiders_{stage}.parquet")
    return report(res)


def report(res: pd.DataFrame) -> str:
    acct = ua.SIZE * ua.SLOTS
    pool = res.groupby(["universe", "select", "exit"]).agg(
        events=("events", "sum"), taken=("taken", "sum"),
        gross_x=("gross_x", "mean"), univ_x=("univ_x", "mean"),
        t212=("trading212_vs_mkt_usd", "sum"), ibkr=("ibkr_fixed_vs_mkt_usd", "sum"),
        t212_actual=("trading212_actual_usd", "sum"),
        years_pos=("trading212_vs_mkt_usd", lambda s: int((s > 0).sum())))
    pool["t212_pct_per_year"] = pool.t212 / acct / res.year.nunique() * 100
    by_year = res.pivot_table(index=["universe", "select", "exit"], columns="year", values="trading212_vs_mkt_usd")
    lines = ["# Insider buying backtest", "",
             "gross_x = mean excess return per event (vs SPY/IWM); univ_x = the same for every event in the universe;",
             f"t212/ibkr = the ${acct:,.0f} account's dollars vs the market, summed over the years.", "",
             (pool.assign(gross_x=pool.gross_x * 100, univ_x=pool.univ_x * 100).round(2)).to_string(), "",
             "Trading 212, $ vs market by year:", by_year.round(0).to_string()]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["choose", "final"])
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    print(run(a.stage, a.final))


if __name__ == "__main__":
    main()
