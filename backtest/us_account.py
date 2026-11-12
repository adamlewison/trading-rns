"""Phase 4, design addendum 3: the user's own account. Rule (g) = buy at the day-1 close, hold 10
trading days; $150 per trade, at most 40 open ($6,000); two brokers' costs.

    uv run python -W ignore -m backtest.us_account choose          # 2019–2021
    uv run python -W ignore -m backtest.us_account final --final   # 2022–2026, once, after the user's OK
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from backtest import us_earnings as bt
from core.log import get_logger

log = get_logger("backtest")
SIZE, SLOTS, HOLD = 150.0, 40, 10
BROKERS = {
    "trading212": dict(min_comm=0.0, per_share=0.0, max_pct=0.0, fx=0.0015, slip=0.0015),
    "ibkr_fixed": dict(min_comm=1.0, per_share=0.005, max_pct=0.01, fx=0.0003, slip=0.0010),
}


def spread_table(dvol: np.ndarray) -> np.ndarray:
    """Full bid-ask spread assumed by 20-day dollar volume (design addendum 3)."""
    return np.select([dvol >= 5e8, dvol >= 1e8], [0.0005, 0.0010], 0.0020)


def round_trip(broker: str, price: np.ndarray, spread: np.ndarray, size: float = SIZE) -> np.ndarray:
    b = BROKERS[broker]
    comm = np.maximum(b["per_share"] * size / price, b["min_comm"])
    if b["max_pct"]:
        comm = np.minimum(comm, b["max_pct"] * size)
    return 2 * (comm / size + b["fx"] + b["slip"] + spread / 2)


def simulate(x: pd.DataFrame, slots: int = SLOTS) -> pd.DataFrame:
    """Signals in date order, best prediction first within a day; skip when `slots` are open."""
    x = x.sort_values(["trade_date", "pred"], ascending=[True, False])
    open_, take = [], []
    for r in x.itertuples():
        open_ = [e for e in open_ if e > r.trade_date]
        ok = len(open_) < slots
        if ok:
            open_.append(r.exit_date)
        take.append(ok)
    return x.assign(taken=take)


def run(stage: str, final: bool = False) -> str:
    if stage == "final" and not final:
        raise SystemExit("the test years are run once, after the user's OK: add --final")
    years = bt.CHOOSE if stage == "choose" else [2022, 2023, 2024, 2025, 2026]
    bt.HOLD_CLOSE["g"] = HOLD
    d = bt.load(final=(stage == "final"), min_dvol=bt.UNIVERSES["liquid"])
    p = bt.predict_years(d, bt.L + bt.REACT, "g", list(range(bt.FIRST_PRED_YEAR, max(years) + 1)))
    d["pred"] = p
    rows = []
    for Y in years:
        cut = p[d.year == Y - 1].quantile(0.9)
        rows.append(d[(d.year == Y) & d.y_g.notna() & (p > cut)])
    x = pd.concat(rows)
    x["exit_date"] = x.trade_date + pd.offsets.BDay(HOLD)
    x = simulate(x)
    tab = spread_table(x.dollar_vol_20d.to_numpy())
    cs = x.spread_cs.fillna(bt.SPREAD_FALLBACK).to_numpy()
    for br in BROKERS:
        x[f"cost_{br}"] = round_trip(br, x.prev_close.to_numpy(), tab)
        x[f"cost_{br}_cs"] = round_trip(br, x.prev_close.to_numpy(), cs)
    out = []
    k = x[x.taken]
    for Y in years:
        s, a = k[k.year == Y], x[x.year == Y]
        row = {"year": Y, "signals": len(a), "taken": len(s), "gross_x_pct": 100 * s.y_g.mean(),
               "market_pct": 100 * (s.r_g - s.y_g).mean()}
        for br in BROKERS:
            for tag, c in ((br, s[f"cost_{br}"]), (br + "_cs", s[f"cost_{br}_cs"])):
                row[f"{tag}_vs_mkt_usd"] = float(((s.y_g - c) * SIZE).sum())
                row[f"{tag}_actual_usd"] = float(((s.r_g - c) * SIZE).sum())
        out.append(row)
    res = pd.DataFrame(out)
    bt.OUT.mkdir(parents=True, exist_ok=True)
    res.to_parquet(bt.OUT / f"us_account_{stage}.parquet")
    x.to_parquet(bt.OUT / f"us_account_{stage}_signals.parquet")
    return report(res)


def report(res: pd.DataFrame) -> str:
    acct = SIZE * SLOTS
    lines = [f"# Account test: ${SIZE:.0f}/trade, max {SLOTS} open (${acct:,.0f}), hold {HOLD} days", ""]
    for _, r in res.iterrows():
        lines.append(f"{int(r.year)}: {int(r.taken)} trades of {int(r.signals)} signals; picks vs market before "
                     f"costs {r.gross_x_pct:+.2f}% per trade")
        for br in BROKERS:
            lines.append(f"   {br:11s} vs market ${r[f'{br}_vs_mkt_usd']:+8,.0f} ({r[f'{br}_vs_mkt_usd'] / acct:+.1%})  "
                         f"actual ${r[f'{br}_actual_usd']:+8,.0f} ({r[f'{br}_actual_usd'] / acct:+.1%})  "
                         f"[pessimistic spread: vs market ${r[f'{br}_cs_vs_mkt_usd']:+,.0f}]")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["choose", "final"])
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    print(run(a.stage, a.final))


if __name__ == "__main__":
    main()
