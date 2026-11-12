"""UK RNS news drift backtest, no LLM. Design: backtest/DESIGN_UK_NEWS.md (fixed before any result).

    uv run python -W ignore -m backtest.uk_news choose          # 2019–2021
    uv run python -W ignore -m backtest.uk_news final --final   # 2022–2026, once, frozen, user's OK
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from backtest import us_account as ua
from backtest.us_earnings import LGB, OUT
from core.config import ROOT
from core.log import get_logger
from features.uk_news import CATS

log = get_logger("backtest")
FEAT = ROOT / "data" / "features" / "uk_news_events.parquet"
CHOOSE, FINAL = [2019, 2020, 2021], [2022, 2023, 2024, 2025, 2026]
FIRST_PRED = 2015
EXITS = {"o1": 1, "h3": 3, "h5": 5, "h20": 20, "a5": None}
SIZE_GBP, SLIP, STAMP = 120.0, 0.0025, 0.005
S0_CATS = [c for c in CATS if c != "profit_warning"]
FEATURES = [f"cat_{c}" for c in CATS] + ["n_ann", "body_chars", "is_aim", "is_sets", "ret_5d", "ret_20d", "ret_60d",
                                         "vol_20d", "log_turnover", "off_high_52w", "off_low_52w", "n_ann_90d", "weekday"]


def spread_uk(turnover: np.ndarray) -> np.ndarray:
    """Full spread by 20-day £ turnover (design; an estimate)."""
    return np.select([turnover >= 5e6, turnover >= 1e6, turnover >= 2.5e5], [0.004, 0.008, 0.015], 0.03)


def round_trip(d: pd.DataFrame) -> np.ndarray:
    return 2 * (spread_uk(d.turnover_20d.to_numpy()) / 2 + SLIP) + STAMP * (1 - d.is_aim.to_numpy())


def load(final: bool = False) -> pd.DataFrame:
    d = pd.read_parquet(FEAT)
    d["trade_date"] = d.day
    if not final:
        d = d[d.trade_date < pd.Timestamp("2022-01-01")]
    d = d[d.skip.isna() & (d.prev_close >= 5) & (d.turnover_20d >= 50_000) & d.y_h5.notna()].copy()
    d["year"] = d.trade_date.dt.year
    return d.reset_index(drop=True)


def predict(d: pd.DataFrame, years: list[int]) -> pd.Series:
    import lightgbm as lgb
    p = pd.Series(np.nan, index=d.index)
    for Y in range(FIRST_PRED, max(years) + 1):
        tr = d[d.trade_date < pd.Timestamp(f"{Y - 1}-12-01")]
        te = d.year == Y
        m = lgb.LGBMRegressor(**LGB).fit(tr[FEATURES].astype(float), tr.y_h5.clip(-0.4, 0.4))
        p[te] = m.predict(d.loc[te, FEATURES].astype(float))
    return p


def evaluate(d: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    d = d.copy()
    d["pred"] = predict(d, years)
    d["cost"] = round_trip(d)
    rows = []
    for Y in years:
        base = d[d.year == Y]
        cut = d.pred[d.year == Y - 1].quantile(0.8)
        sels = {f"S0_{c}": base[base[f"cat_{c}"] == 1] for c in S0_CATS}
        sels["S1_model"] = base[base.pred > cut]
        for sname, s in sels.items():
            for x, n in EXITS.items():
                s2 = s[s[f"y_{x}"].notna()]
                days = s2.a5_days if x == "a5" else pd.Series(n, index=s2.index)
                acct = ua.simulate(s2.assign(exit_date=s2.trade_date + pd.to_timedelta(np.ceil(days.astype(float) * 7 / 5), unit="D"),
                                             pred=s2.pred if sname == "S1_model" else s2.turnover_20d))
                k = acct[acct.taken]
                rows.append(dict(year=Y, select=sname, exit=x, events=len(s2), taken=len(k),
                                 gross_x=s2[f"y_{x}"].mean(), cost=k.cost.mean(),
                                 vs_mkt_gbp=float(((k[f"y_{x}"] - k.cost) * SIZE_GBP).sum()),
                                 actual_gbp=float(((k[x] - k.cost) * SIZE_GBP).sum())))
    return pd.DataFrame(rows)


def run(stage: str, final: bool = False) -> str:
    if stage == "final" and not final:
        raise SystemExit("the test years are run once, after the user's OK: add --final")
    years = CHOOSE if stage == "choose" else FINAL
    d = load(final=(stage == "final"))
    log.info("UK news events usable: %d", len(d))
    res = evaluate(d, years)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT / f"uk_news_{stage}.parquet")
    pool = res.groupby(["select", "exit"]).agg(events=("events", "sum"), taken=("taken", "sum"),
                                               gross_x=("gross_x", "mean"), cost=("cost", "mean"),
                                               vs_mkt_gbp=("vs_mkt_gbp", "sum"),
                                               years_pos=("vs_mkt_gbp", lambda s: int((s > 0).sum())))
    return "\n".join(["# UK news drift (no LLM)", (pool.assign(gross_x=pool.gross_x * 100, cost=pool.cost * 100)
                                                   .round(2)).to_string()])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["choose", "final"])
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    print(run(a.stage, a.final))


if __name__ == "__main__":
    main()
