"""UK investment trust discounts (backtest/DESIGN_UK_TRUST_DISCOUNTS.md).

    uv run python -W ignore -m backtest.uk_trust_discounts choose           # 2015-2021
    uv run python -W ignore -m backtest.uk_trust_discounts final --final    # 2022 -> latest, ONCE, after the user agrees

Needs data/trusts/{prices,discounts}.parquet (features.uk_trusts, or B2 trading-rns/trusts/v1/) and data/uk.duckdb (^FTAS).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.uk_news import spread_uk
from features.uk_trusts import DISCOUNT_RANGE, MIN_NAV_PER_YEAR
from ingest.uk.common import session

D = Path("data/trusts")
CHOOSE = ("2015-01", "2021-12")
FINAL_START = "2022-01"
ACCOUNT, STAKE, SLOTS = 5000.0, 1000.0, 5
Z_BUY, Z_SELL, MAX_CHECKS = -1.5, 0.0, 6
CATALYST = -0.08
MIN_STD, HISTORY = 0.01, 12
MIN_PRICE_P, MIN_TURNOVER = 20.0, 100_000.0
STAMP, SLIP, DIV_YIELD = 0.005, 0.0025, 0.035


def zscores(d: pd.DataFrame) -> pd.DataFrame:
    """d: tidm, month (Period), discount, one row per trust-month. z uses the 12 months BEFORE each
    month, all 12 present; the month's own discount is not in the mean or the standard deviation."""
    d = d.sort_values(["tidm", "month"]).copy()
    out = []
    for tidm, g in d.groupby("tidm"):
        s = g.set_index("month").discount
        full = s.reindex(pd.period_range(s.index.min(), s.index.max(), freq="M"))
        prev = full.shift(1)
        mean, std = prev.rolling(HISTORY, min_periods=HISTORY).mean(), prev.rolling(HISTORY, min_periods=HISTORY).std()
        z = ((full - mean) / std).where(std >= MIN_STD)
        out.append(pd.DataFrame({"tidm": tidm, "month": full.index, "discount": full.to_numpy(), "z": z.to_numpy()}))
    return pd.concat(out, ignore_index=True).dropna(subset=["discount"])


def load(start: str, end: str):
    """Everything is cut at `end` before any calculation."""
    last_day = pd.Period(end, "M").end_time.normalize()
    disc = pd.read_parquet(D / "discounts.parquet")
    with session(read_only=True) as con:
        freq = con.execute("""SELECT tidm, year(published_uk) y, count(*) n FROM ann_index
            WHERE regexp_matches(lower(headline), '^net asset value') GROUP BY 1, 2""").df()
        ft = con.execute("SELECT date, close FROM prices_daily WHERE tidm = '^FTAS' ORDER BY 1").df()
    frequent = set(freq.groupby("tidm").n.median().loc[lambda s: s >= MIN_NAV_PER_YEAR].index)
    disc = disc[disc.tidm.isin(frequent) & disc.discount.between(*DISCOUNT_RANGE) & (disc.month <= pd.Period(end, "M"))]
    z = zscores(disc[["tidm", "month", "discount"]])
    px = pd.read_parquet(D / "prices.parquet")
    px = px[(px.date <= last_day) & (px.volume > 0) & (px.close > 0) & px.tidm.isin(frequent)].copy()
    px["tr_open"] = px.open * px.adj_close / px.close            # total-return price at the open
    px["gbp"] = px.close * px.volume / 100
    px["turnover"] = px.groupby("tidm").gbp.transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean())
    ft["date"] = pd.to_datetime(ft.date)
    ft = ft[ft.date <= last_day].set_index("date").close
    ft_tr = ft * (1 + DIV_YIELD) ** ((ft.index - ft.index[0]).days / 365)
    firsts = ft_tr.groupby(ft_tr.index.to_period("M")).head(1)
    month_start = {d.to_period("M"): d for d in firsts.index}
    return z, px.set_index(["tidm", "date"]).sort_index(), ft_tr, month_start


def simulate(variant: str, z, px, ft_tr, month_start, start: str, end: str):
    sig = {m: g.set_index("tidm") for m, g in z.groupby("month")}
    months = pd.period_range(start, end, freq="M")
    idx_units = ACCOUNT / ft_tr[month_start[months[0]]]
    hold: dict[str, dict] = {}
    trades, marks = [], []

    def row(tidm, day):
        try:
            return px.loc[(tidm, day)]
        except KeyError:
            return None

    def last_tr(tidm, day):
        s = px.loc[tidm].tr_open
        s = s[s.index <= day]
        return float(s.iloc[-1]) if len(s) else None

    for m in months:
        day = month_start.get(m)
        if day is None:
            continue
        s = sig.get(m - 1, pd.DataFrame(columns=["discount", "z"]))      # last NAV of the previous month
        ix = float(ft_tr[day])
        # ---- sells
        if variant == "W":
            cand = s[s.discount.notna()].sort_values("discount")
            target = []
            for t in cand.index:
                r = row(t, day)
                if r is not None and r.close >= MIN_PRICE_P and r.turnover >= MIN_TURNOVER:
                    target.append(t)
                if len(target) == SLOTS:
                    break
        for t in list(hold):
            h = hold[t]
            h["checks"] += 1
            zt = s.z.get(t, np.nan) if len(s) else np.nan
            sell = (t not in target) if variant == "W" else ((zt >= Z_SELL) or h["checks"] >= MAX_CHECKS)
            r = row(t, day)
            if sell and r is not None:
                cost = float(spread_uk(np.array([r.turnover]))[0]) / 2 + SLIP
                value = h["units"] * r.tr_open * (1 - cost)
                d_exit = s.discount.get(t, np.nan) if len(s) else np.nan
                trades.append({"tidm": t, "entry": h["day"], "exit": day, "ret": value / STAKE - 1,
                               "index_ret": ix / h["ix"] - 1, "d_entry": h["d"], "d_exit": d_exit,
                               "capture": (1 + d_exit) / (1 + h["d"]) - 1, "z_entry": h["z"]})
                idx_units += value / ix
                del hold[t]
        # ---- buys
        if variant == "W":
            buys = [t for t in target if t not in hold]
        else:
            c = s[(s.z <= Z_BUY)] if len(s) else s
            if variant == "ZC":
                c = c[c.discount <= CATALYST]
            buys = [t for t in c.sort_values("z").index if t not in hold]
        for t in buys:
            if len(hold) >= SLOTS:
                break
            r = row(t, day)
            if r is None or r.close < MIN_PRICE_P or not r.turnover >= MIN_TURNOVER:
                continue
            cost = float(spread_uk(np.array([r.turnover]))[0]) / 2 + SLIP + STAMP
            hold[t] = {"units": STAKE * (1 - cost) / r.tr_open, "day": day, "ix": ix, "checks": 0,
                       "d": float(s.discount[t]), "z": float(s.z.get(t, np.nan))}
            idx_units -= STAKE / ix
        pos = sum(h["units"] * (last_tr(t, day) or 0.0) for t, h in hold.items())
        marks.append({"month": m, "equity": idx_units * ix + pos, "index": ACCOUNT * ix / ft_tr[month_start[months[0]]],
                      "n_held": len(hold)})
    return pd.DataFrame(trades), pd.DataFrame(marks).set_index("month")


def report(start: str, end: str) -> None:
    pd.set_option("display.width", 220, "display.float_format", lambda x: f"{x:,.4f}")
    z, px, ft_tr, month_start = load(start, end)
    zz = z[(z.month >= pd.Period(start, "M") - 1) & z.z.notna()]
    print(f"trusts with a z-score in the period: {zz.tidm.nunique()}; trust-months: {len(zz)}; share with z <= {Z_BUY}: {(zz.z <= Z_BUY).mean():.3f}")
    res = {}
    for v in ("Z", "ZC", "W"):
        tr, mk = simulate(v, z, px, ft_tr, month_start, start, end)
        res[v] = (tr, mk)
        yr = mk.groupby(mk.index.year).agg(equity=("equity", "last"), index=("index", "last"))
        prev = pd.concat([pd.DataFrame({"equity": [ACCOUNT], "index": [ACCOUNT]}), yr.iloc[:-1]]).to_numpy()
        yr["ret"], yr["index_ret"] = yr.equity / prev[:, 0] - 1, yr["index"] / prev[:, 1] - 1
        won = int((yr.ret > yr.index_ret).sum())
        print(f"\n== {v}: £{mk.equity.iloc[-1]:,.0f} vs index £{mk['index'].iloc[-1]:,.0f}; beat the index in {won} of {len(yr)} years; "
              f"completed holdings {len(tr)}; average slots used {mk.n_held.mean():.1f} ==")
        print(yr[["ret", "index_ret"]].T.to_string())
        if len(tr):
            ex = tr.ret - tr.index_ret
            wk = tr.entry.dt.to_period("M")
            se = np.sqrt(((ex - ex.mean()).groupby(wk).sum() ** 2).sum()) / len(ex)
            print(f"per holding: mean return {tr.ret.mean():+.4f}, vs index {ex.mean():+.4f} (median {ex.median():+.4f}, t by entry month {ex.mean() / se:.2f}), "
                  f"winners vs index {(ex > 0).mean():.2f}; discount at entry {tr.d_entry.mean():+.3f}, at exit {tr.d_exit.mean():+.3f}; "
                  f"discount capture {tr.capture.mean():+.4f} (median {tr.capture.median():+.4f}); months held {((tr.exit - tr.entry).dt.days / 30.4).mean():.1f}; "
                  f"vs index without the best 5 holdings {ex.drop(ex.nlargest(5).index).mean():+.4f}")
    tr = res["Z"][0]
    if len(tr):
        print("\n== Z: 5 worst and 5 best holdings vs the index ==")
        t = tr.assign(excess=tr.ret - tr.index_ret).sort_values("excess")
        print(pd.concat([t.head(5), t.tail(5)])[["tidm", "entry", "exit", "z_entry", "d_entry", "d_exit", "capture", "ret", "index_ret", "excess"]].to_string(index=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "choose"
    if cmd == "final":
        if "--final" not in sys.argv:
            raise SystemExit("the final test runs once, with --final, after the user agrees")
        report(FINAL_START, str(pd.Timestamp.today().to_period("M")))
    else:
        report(*CHOOSE)
