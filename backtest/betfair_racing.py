"""Betfair Exchange racing, Aus & NZ thoroughbreds (backtest/DESIGN_BETFAIR_RACING.md).

    uv run python -W ignore -m backtest.betfair_racing choose              # 2020-2023
    uv run python -W ignore -m backtest.betfair_racing final VARIANT --final   # 2024 -> Oct 2026, ONCE, after the user agrees

Data: data/betfair/raw/t*/ANZ_Thoroughbreds_*.csv (betfair-datascientists.github.io, free).
"""
from __future__ import annotations

import math
import sys
from itertools import permutations

import duckdb
import numpy as np
import pandas as pd

RAW = "data/betfair/raw/t*/*.csv"
CHOOSE, FINAL = (2020, 2023), (2024, 2026)
COMMISSIONS = (0.05, 0.08)
MIN_WIN_VOL, MIN_PLACE_VOL = 1000.0, 500.0


def load(years: tuple[int, int]) -> pd.DataFrame:
    """Rows of one period only (the other period's results are never read into memory)."""
    con = duckdb.connect()
    day = "coalesce(TRY_CAST(LOCAL_MEETING_DATE AS DATE), TRY_STRPTIME(LOCAL_MEETING_DATE, '%d/%m/%Y')::DATE)"
    d = con.execute(f"""SELECT {day} AS day, TRACK AS track, WIN_MARKET_ID AS market,
            SELECTION_ID AS sel, WIN_RESULT = 'WINNER' AS won, PLACE_RESULT = 'WINNER' AS placed,
            TRY_CAST(WIN_BSP AS DOUBLE) AS win_bsp, TRY_CAST(PLACE_BSP AS DOUBLE) AS place_bsp,
            TRY_CAST(WIN_BSP_VOLUME AS DOUBLE) AS win_vol, TRY_CAST(PLACE_BSP_VOLUME AS DOUBLE) AS place_vol,
            TRY_CAST(BEST_AVAIL_BACK_AT_SCHEDULED_OFF AS DOUBLE) AS back_off,
            TRY_CAST(BEST_AVAIL_LAY_AT_SCHEDULED_OFF AS DOUBLE) AS lay_off
        FROM read_csv('{RAW}', header=true, all_varchar=true, union_by_name=true)
        WHERE year({day}) BETWEEN {years[0]} AND {years[1]}""").df()
    d = d.drop_duplicates(["market", "sel"])
    d["year"] = pd.to_datetime(d.day).dt.year
    d["cluster"] = d.day.astype(str) + "|" + d.track
    d["mid"] = (d.back_off + d.lay_off) / 2
    d.loc[~((d.back_off > 1) & (d.lay_off > 1) & (d.lay_off >= d.back_off)), "mid"] = np.nan
    d["starters"] = d.groupby("market").win_bsp.transform(lambda s: s.notna().sum())
    return d


def harville_place(p: np.ndarray, k: int) -> np.ndarray:
    """Probability each runner finishes in the first k (k = 2 or 3), from win probabilities p (sum 1)."""
    n = len(p)
    out = p.copy()                                               # first place
    for j in range(n):                                           # second place behind j
        rest = 1 - p[j]
        if rest <= 0:
            continue
        q = p * p[j] / rest
        q[j] = 0
        out += q
    if k == 3:
        for j, l in permutations(range(n), 2):                   # third place behind j then l
            r1, r2 = 1 - p[j], 1 - p[j] - p[l]
            if r1 <= 0 or r2 <= 0:
                continue
            q = p * p[j] * (p[l] / r1) / r2
            q[[j, l]] = 0
            out += q
    return np.clip(out, 0, 1)


def add_model(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["k"] = np.select([d.starters >= 8, d.starters >= 5], [3, 2], 0)
    d["p_win"] = np.nan
    d["p_place"] = np.nan
    ok = d[d.win_bsp.notna() & d.mid.notna()]
    for m, g in ok.groupby("market"):
        k = int(g.k.iloc[0])
        if len(g) != g.starters.iloc[0]:                         # a runner without a price at the off: skip race
            continue
        p = (1 / g.mid.to_numpy())
        p = p / p.sum()
        d.loc[g.index, "p_win"] = p
        if k:
            d.loc[g.index, "p_place"] = harville_place(p, k)
    return d


def back_ret(price, hit, c):
    return np.where(hit, (price - 1) * (1 - c), -1.0)


def lay_ret(price, hit, c):
    """Per £1 of liability: lay stake 1 / (price - 1)."""
    return np.where(hit, -1.0, (1 / (price - 1)) * (1 - c))


def variants(d: pd.DataFrame, c: float) -> dict[str, pd.DataFrame]:
    w = d[d.win_bsp.notna() & (d.win_vol >= MIN_WIN_VOL) & d.mid.notna()]
    pl = d[d.place_bsp.notna() & (d.place_vol >= MIN_PLACE_VOL) & d.p_place.notna() & (d.place_bsp > 1)]
    out = {}
    a1 = w[w.mid <= 2.5]
    out["A1 back mid<=2.5 at win BSP"] = a1.assign(ret=back_ret(a1.win_bsp, a1.won, c))
    a2 = w[(w.mid >= 50) & (w.mid <= 200)]
    out["A2 lay mid 50-200 at win BSP"] = a2.assign(ret=lay_ret(a2.win_bsp, a2.won, c))
    b1 = pl[(pl.p_place >= 0.5) & (pl.mid >= 4.0)]
    out["B1 back place: p>=0.5, mid>=4"] = b1.assign(ret=back_ret(b1.place_bsp, b1.placed, c))
    b2 = pl[(pl.p_place <= 0.15) & (pl.mid <= 15)]
    out["B2 lay place: p<=0.15, mid<=15"] = b2.assign(ret=lay_ret(b2.place_bsp, b2.placed, c))
    naive = np.minimum(pl.k * pl.p_win, 0.95)
    b4 = pl[pl.p_place >= naive + 0.10]
    out["B4 back place: Harville >= naive+0.10"] = b4.assign(ret=back_ret(b4.place_bsp, b4.placed, c))
    return out


def tstat(r: pd.Series, cl: pd.Series) -> float:
    dev = r - r.mean()
    se = math.sqrt(float((dev.groupby(cl).sum() ** 2).sum())) / len(r)
    return float(r.mean() / se) if se > 0 else float("nan")


def report(years: tuple[int, int], only: str | None = None) -> None:
    pd.set_option("display.width", 220, "display.float_format", lambda x: f"{x:,.4f}")
    d = add_model(load(years))
    print(f"{years}: {d.market.nunique():,} races, {len(d):,} runners; with a price at the off and a model: "
          f"{d.p_win.notna().sum():,}")
    rows = []
    for c in COMMISSIONS:
        for name, b in variants(d, c).items():
            if only and not name.startswith(only):
                continue
            by_year = b.groupby("year").ret.mean()
            rows.append({"variant": name, "commission": c, "bets": len(b), "hit_rate": b.won.mean() if name.startswith("A") else b.placed.mean(),
                         "mean_ret": b.ret.mean(), "t": tstat(b.ret, b.cluster),
                         "years_positive": f"{(by_year > 0).sum()}/{len(by_year)}",
                         **{f"y{y}": v for y, v in by_year.items()}})
    print(pd.DataFrame(rows).to_string(index=False))
    if not only:
        cal = d[d.place_bsp.notna() & d.win_bsp.notna() & (d.k > 0)].copy()     # B3: diagnostic only
        m = add_model(cal.assign(mid=cal.win_bsp))
        m = m[m.p_place.notna()]
        m["bucket"] = pd.cut(1 / m.place_bsp, [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0])
        print("\nB3 diagnostic (same-race BSPs, not tradable): place BSP implied probability vs actual placing rate and Harville")
        print(m.groupby("bucket", observed=True).agg(runners=("placed", "size"), implied=("place_bsp", lambda s: (1 / s).mean()),
                                                     actual=("placed", "mean"), harville=("p_place", "mean")).to_string())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "choose"
    if cmd == "final":
        if "--final" not in sys.argv:
            raise SystemExit("the final test runs once, with --final, after the user agrees")
        report(FINAL, only=sys.argv[2])
    else:
        report(CHOOSE)
