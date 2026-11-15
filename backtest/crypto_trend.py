"""Crypto trend-following on BTC and ETH (backtest/DESIGN_CRYPTO_TREND.md).

    uv run python -W ignore -m backtest.crypto_trend data             # yfinance -> data/crypto/*.parquet
    uv run python -W ignore -m backtest.crypto_trend choose           # 2018-06-01 .. 2021-12-31
    uv run python -W ignore -m backtest.crypto_trend final --final    # 2022-01-01 .. latest, ONCE, after the user agrees
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

D = Path("data/crypto")
COINS = ("BTC-USD", "ETH-USD")
CHOOSE = ("2018-06-01", "2021-12-31")
FINAL_START = "2022-01-01"
START_CASH = 6000.0
COST = 0.0045                  # of the traded amount, each way
MAX_W = 0.50                   # per coin; never above 100% in total
VOL_TARGET, VOL_DAYS = 0.40, 30
SMA_DAYS, MOM_DAYS, MIN_HISTORY = 100, 28, 100
BAND = 0.10                    # rebalance when target and current weight differ by more than this
ANN = 365


def fetch() -> None:
    import yfinance as yf
    D.mkdir(parents=True, exist_ok=True)
    for t in (*COINS, "SPY"):
        df = yf.Ticker(t).history(period="max", auto_adjust=False, actions=False)
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        df.to_parquet(D / f"{t}.parquet")
        print(t, len(df), df.index[0].date(), df.index[-1].date())


def load(end: str) -> tuple[pd.DataFrame, pd.Series]:
    """Closes up to `end` only: later data is never read into the calculation."""
    px = pd.DataFrame({c: pd.read_parquet(D / f"{c}.parquet").Close for c in COINS})
    px = px[px.index <= end]
    spy = pd.read_parquet(D / "SPY.parquet")["Adj Close"]
    spy = spy[spy.index <= end].reindex(px.index).ffill()
    return px, spy


def targets(px: pd.DataFrame, signal: str, size: str) -> pd.DataFrame:
    """Target weight per coin from data up to and including each day's close (no shift here)."""
    ret = px.pct_change()
    if signal == "M100":
        on = px > px.rolling(SMA_DAYS).mean()
    else:
        on = px / px.shift(MOM_DAYS) - 1 > 0
    on &= px.notna().rolling(MIN_HISTORY).sum() >= MIN_HISTORY
    if size == "V":
        vol = ret.rolling(VOL_DAYS).std() * np.sqrt(ANN)
        w = (MAX_W * VOL_TARGET / vol).clip(upper=MAX_W)
    else:
        w = pd.DataFrame(MAX_W, index=px.index, columns=px.columns)
    return w.where(on, 0.0).fillna(0.0)


def simulate(px: pd.DataFrame, tgt: pd.DataFrame, start: str, band: float = BAND, monthly: bool = False) -> pd.DataFrame:
    """Day loop from `start`. The target computed at yesterday's close is traded at today's close.
    `monthly`: buy-and-hold benchmark, rebalanced to the target on the first day of each month."""
    days = px.index[px.index >= start]
    cash, units = START_CASH, {c: 0.0 for c in px.columns}
    was_on = {c: False for c in px.columns}
    rows = []
    for i, d in enumerate(days):
        prev = px.index[px.index.get_loc(d) - 1]
        price = px.loc[d]
        equity = cash + sum(units[c] * price[c] for c in px.columns if units[c])
        traded = cost = 0.0
        for c in px.columns:
            if np.isnan(price[c]):
                continue
            want = float(tgt.at[prev, c])
            have = units[c] * price[c] / equity
            if monthly:
                act = i == 0 or d.month != days[i - 1].month
            else:
                act = (want > 0) != was_on[c] or abs(want - have) > band
            if act and abs(want - have) > 1e-9:
                delta = (want - have) * equity
                fee = abs(delta) * COST
                units[c] += delta / price[c]
                cash -= delta + fee
                traded += abs(delta)
                cost += fee
            was_on[c] = units[c] * price[c] / equity > 1e-6
        equity = cash + sum(units[c] * price[c] for c in px.columns if units[c])
        rows.append({"date": d, "equity": equity, "traded": traded, "cost": cost,
                     "invested": 1 - cash / equity})
    return pd.DataFrame(rows).set_index("date")


def stats(eq: pd.Series) -> dict:
    r = eq.pct_change().dropna()
    return {"total": eq.iloc[-1] / eq.iloc[0] - 1, "sharpe": r.mean() / r.std() * np.sqrt(ANN) if r.std() else 0.0,
            "max_dd": (eq / eq.cummax() - 1).min(),
            **{f"y{y}": g.iloc[-1] / g.iloc[0] - 1 for y, g in eq.groupby(eq.index.year)}}


def run(start: str, end: str, coins=COINS) -> pd.DataFrame:
    px, spy = load(end)
    px = px[list(coins)]
    out = {}
    for signal in ("M100", "R28"):
        for size in ("V", "F"):
            sim = simulate(px, targets(px, signal, size), start)
            out[f"{signal}-{size}"] = {**stats(sim.equity), "in_market": (sim.invested > 0.01).mean(),
                                       "avg_invested": sim.invested.mean(), "trades": int((sim.traded > 0).sum()),
                                       "costs": sim.cost.sum()}
    hold = pd.DataFrame(MAX_W, index=px.index, columns=px.columns).where(px.notna(), 0.0)
    b = simulate(px, hold, start, monthly=True)
    out["hold-crypto"] = {**stats(b.equity), "in_market": 1.0, "avg_invested": b.invested.mean(),
                          "trades": int((b.traded > 0).sum()), "costs": b.cost.sum()}
    s = spy[spy.index >= start]
    out["SPY"] = {**stats(START_CASH * s / s.iloc[0]), "in_market": 1.0, "avg_invested": 1.0, "trades": 0, "costs": 0.0}
    return pd.DataFrame(out).T


def report(start: str, end: str) -> None:
    pd.set_option("display.width", 220, "display.float_format", lambda x: f"{x:,.3f}")
    for coins, label in ((COINS, "BTC + ETH"), (COINS[:1], "BTC only (ETH's half stays in cash)")):
        r = run(start, end, coins)
        print(f"\n== {label}: {start} .. {end} ==")
        print(r.to_string())
        if len(coins) == 2 and end <= CHOOSE[1]:
            for v in r.index[:4]:
                x, spy, hold = r.loc[v], r.loc["SPY"], r.loc["hold-crypto"]
                yrs = sum(x[f"y{y}"] > spy[f"y{y}"] for y in (2019, 2020, 2021))
                ok = x.total > spy.total and x.sharpe > hold.sharpe and x.max_dd >= -0.50 and yrs >= 2
                print(f"{v}: beats SPY total {x.total > spy.total}; Sharpe above hold-crypto {x.sharpe > hold.sharpe} "
                      f"({x.sharpe:.2f} vs {hold.sharpe:.2f}); worst fall {x.max_dd:.0%} (limit -50%); "
                      f"beats SPY in {yrs} of 2019-21 -> {'PASS' if ok else 'fail'}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "choose"
    if cmd == "data":
        fetch()
    elif cmd == "final":
        if "--final" not in sys.argv:
            raise SystemExit("the final test runs once, with --final, after the user agrees")
        report(FINAL_START, "2100-01-01")
    else:
        report(*CHOOSE)
