"""ETF trend-following / momentum backtest. Design: backtest/DESIGN_ETF_TREND.md (fixed before any result).

    uv run python -W ignore -m backtest.etf_trend choose          # 2007-03 .. 2021-12
    uv run python -W ignore -m backtest.etf_trend final --final   # 2022-01 .. latest, once, frozen

Monthly signals on month-end closes (total-return prices); trades at the next trading day's close;
costs on the traded amount. Returns are of the whole portfolio."""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from backtest.us_earnings import OUT
from core.log import get_logger
from ingest.us.common import session

log = get_logger("backtest")
COST_SIDE = 0.0005 + 0.0005 + 0.0015           # spread + slippage + FX, each side
CASH = "SHY"
START, CHOOSE_END = pd.Timestamp("2007-03-01"), pd.Timestamp("2021-12-31")
V3_SET = ["SPY", "QQQ", "IWM", "EFA", "EEM", "VNQ", "GLD", "DBC", "TLT", "IEF"]


def prices() -> pd.DataFrame:
    with session(read_only=True) as con:
        df = con.execute("SELECT ticker, date, adj_close FROM us_etf_daily").df()
    p = df.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    p.index = pd.to_datetime(p.index)
    return p


# ---------------------------------------------------------------- signals: target weights per month end
def month_ends(p: pd.DataFrame) -> pd.DatetimeIndex:
    s = p.index.to_series()
    return pd.DatetimeIndex(s.groupby(s.dt.to_period("M")).max().values)


def sma10(m: pd.DataFrame) -> pd.DataFrame:
    """10-month average of month-end prices (includes the current month)."""
    return m.rolling(10).mean()


def ret(m: pd.DataFrame, k: int) -> pd.DataFrame:
    return m / m.shift(k) - 1


def weights(m: pd.DataFrame, strategy: str) -> pd.DataFrame:
    """Target weights decided at each month-end close (m = month-end prices)."""
    w = pd.DataFrame(0.0, index=m.index, columns=m.columns)
    up = m > sma10(m)
    if strategy == "V1":
        for a in ["SPY", "EFA", "IEF", "VNQ", "DBC"]:
            w[a] = np.where(up[a], 0.2, 0.0)
        w[CASH] += 1 - w.sum(axis=1)
    elif strategy == "V2":
        r12 = ret(m, 12)
        risk_on = r12["SPY"] > r12[CASH]
        best = np.where(r12["SPY"] >= r12["EFA"], "SPY", "EFA")
        for i, d in enumerate(m.index):
            w.loc[d, best[i] if risk_on.iloc[i] else "AGG"] = 1.0
    elif strategy == "V3":
        score = sum(ret(m[V3_SET], k) for k in (1, 3, 6, 12)) / 4
        for d in m.index:
            top = score.loc[d].dropna().nlargest(3).index
            for a in top:
                w.loc[d, a if up.loc[d, a] else CASH] += 1 / 3
    elif strategy == "V4":
        w["SPY"] = np.where(up["SPY"], 1.0, 0.0)
        w[CASH] = 1 - w["SPY"]
    elif strategy == "SPY":
        w["SPY"] = 1.0
    elif strategy == "60/40":
        w["SPY"], w["IEF"] = 0.6, 0.4
    return w


def simulate(p: pd.DataFrame, strategy: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Daily portfolio value (start = 1). Trades the day after each month end, at the close."""
    me = month_ends(p)
    m = p.loc[me]
    w_target = weights(m, strategy)
    days = p.loc[(p.index >= start) & (p.index <= end)].index
    trade_on = {p.index[p.index.get_loc(d) + 1]: d for d in me if p.index.get_loc(d) + 1 < len(p.index)}
    daily = p.pct_change()
    value, hold = 1.0, pd.Series(0.0, index=p.columns)
    out = []
    first = True
    for d in days:
        if not first:
            r = (hold * daily.loc[d].fillna(0)).sum()
            value *= 1 + r
            hold = hold * (1 + daily.loc[d].fillna(0))
            hold = hold / hold.sum() if hold.sum() > 0 else hold
        past = me[me <= d]
        if (d in trade_on or first) and len(past):
            src = trade_on.get(d, past[-1])
            tgt = w_target.loc[src].fillna(0)
            if tgt.sum() > 0:
                turnover = (tgt - hold).abs().sum()
                value *= 1 - turnover * COST_SIDE
                hold = tgt.copy()
        first = False
        out.append((d, value))
    return pd.Series(dict(out))


def stats(v: pd.Series) -> dict:
    r = v.pct_change().dropna()
    years = (v.index[-1] - v.index[0]).days / 365.25
    dd = (v / v.cummax() - 1).min()
    return dict(total=v.iloc[-1] / v.iloc[0] - 1, cagr=(v.iloc[-1] / v.iloc[0]) ** (1 / years) - 1,
                vol=r.std() * np.sqrt(252), sharpe=(r.mean() / r.std()) * np.sqrt(252) if r.std() else np.nan,
                max_dd=dd)


def run(stage: str, final: bool = False) -> str:
    if stage == "final" and not final:
        raise SystemExit("the test period is run once, after the user's OK: add --final")
    p = prices()
    start, end = (START, CHOOSE_END) if stage == "choose" else (pd.Timestamp("2022-01-01"), p.index[-1])
    rows, yearly = {}, {}
    for s in ["V1", "V2", "V3", "V4", "SPY", "60/40"]:
        v = simulate(p, s, start, end)
        rows[s] = stats(v)
        yearly[s] = v.groupby(v.index.year).last() / v.groupby(v.index.year).first() - 1
    res = pd.DataFrame(rows).T
    yr = pd.DataFrame(yearly)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / f"etf_trend_{stage}.csv")
    yr.to_csv(OUT / f"etf_trend_{stage}_yearly.csv")
    return "\n".join([f"# ETF trend {stage}: {start.date()} .. {end.date()} (after costs)",
                      (res * 100).round(2).to_string(), "", "Yearly returns %:", (yr * 100).round(1).to_string()])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["choose", "final"])
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    print(run(a.stage, a.final))


if __name__ == "__main__":
    main()
