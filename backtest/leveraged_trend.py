"""Leveraged trend-following on the S&P 500. Design: backtest/DESIGN_LEVERAGED_TREND.md.

    uv run python -W ignore -m backtest.leveraged_trend choose          # 1994-01 .. 2021-12
    uv run python -W ignore -m backtest.leveraged_trend final --final   # 2022-01 .. latest, once, frozen
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from backtest.etf_trend import stats
from backtest.us_earnings import OUT
from ingest.us.common import session

SWITCH_SIDE = 0.0010 + 0.0005 + 0.0015          # spread + slippage + FX, each side
SWAP_SPREAD, FUND_FEE = 0.0030, 0.0075
CHOOSE = (pd.Timestamp("1994-01-01"), pd.Timestamp("2021-12-31"))


def data() -> tuple[pd.Series, pd.Series]:
    with session(read_only=True) as con:
        df = con.execute("SELECT ticker, date, adj_close, close FROM us_etf_daily WHERE ticker IN ('SPY','^IRX')").df()
    spy = df[df.ticker == "SPY"].set_index("date").adj_close.sort_index()
    irx = df[df.ticker == "^IRX"].set_index("date").close.sort_index()
    spy.index, irx.index = pd.to_datetime(spy.index), pd.to_datetime(irx.index)
    rf = (irx.reindex(spy.index).ffill() / 100).fillna(0)          # annual T-bill yield
    return spy, rf


def fund_returns(spy: pd.Series, rf: pd.Series, lev: float) -> pd.Series:
    r = spy.pct_change().fillna(0)
    if lev == 1:
        return r
    return lev * r - (lev - 1) * (rf + SWAP_SPREAD) / 252 - FUND_FEE / 252


def signal(spy: pd.Series, kind: str) -> pd.Series:
    """1 = invested, decided at a close; the trade happens at the NEXT close, so the exposure on day d
    is the signal from two closes earlier."""
    if kind == "monthly":
        me = spy.groupby(spy.index.to_period("M")).tail(1)
        up = (me > me.rolling(10).mean()).astype(float)
        s = up.reindex(spy.index).ffill()
    elif kind == "daily":
        s = (spy > spy.rolling(200).mean()).astype(float)
    else:
        s = pd.Series(1.0, index=spy.index)
    return s.shift(2).fillna(0)


def simulate(spy: pd.Series, rf: pd.Series, lev: float, kind: str, start, end) -> pd.Series:
    fr = fund_returns(spy, rf, lev)
    pos = signal(spy, kind)
    cash = rf / 252
    d = (spy.index >= start) & (spy.index <= end)
    fr, pos, cash = fr[d], pos[d], cash[d]
    ret = pos * fr + (1 - pos) * cash
    changes = pos.diff().abs().fillna(pos.iloc[0])          # entering at the start costs one side
    ret = ret - changes * SWITCH_SIDE
    return (1 + ret).cumprod()


STRATS = {"L1 2x monthly": (2.0, "monthly"), "L2 2x 200-day": (2.0, "daily"), "L3 1.5x monthly": (1.5, "monthly"),
          "SPY held": (1.0, "hold"), "2x held": (2.0, "hold"), "1x monthly (V4)": (1.0, "monthly")}


def run(stage: str, final: bool = False) -> str:
    if stage == "final" and not final:
        raise SystemExit("the test period is run once, after the user's OK: add --final")
    spy, rf = data()
    start, end = CHOOSE if stage == "choose" else (pd.Timestamp("2022-01-01"), spy.index[-1])
    rows, yearly = {}, {}
    for name, (lev, kind) in STRATS.items():
        v = simulate(spy, rf, lev, kind, start, end)
        rows[name] = stats(v)
        rows[name]["switches"] = int(signal(spy, kind)[(spy.index >= start) & (spy.index <= end)].diff().abs().sum())
        yearly[name] = v.groupby(v.index.year).last() / v.groupby(v.index.year).first() - 1
    res, yr = pd.DataFrame(rows).T, pd.DataFrame(yearly)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / f"leveraged_trend_{stage}.csv")
    yr.to_csv(OUT / f"leveraged_trend_{stage}_yearly.csv")
    pct = res.drop(columns="switches") * 100
    return "\n".join([f"# Leveraged trend {stage}: {start.date()} .. {end.date()} (after costs)",
                      pct.round(2).join(res.switches).to_string(), "", "Yearly returns %:", (yr * 100).round(1).to_string()])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["choose", "final"])
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    print(run(a.stage, a.final))


if __name__ == "__main__":
    main()
