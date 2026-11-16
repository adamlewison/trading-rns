"""UK investment trusts: prices with split history, NAVs and monthly discounts
(backtest/DESIGN_UK_TRUST_DISCOUNTS.md, step 0 and the discount table).

    uv run python -W ignore -m features.uk_trusts prices     # yfinance -> data/trusts/prices.parquet
    uv run python -W ignore -m features.uk_trusts build      # NAV labels + prices -> data/trusts/discounts.parquet
    uv run python -W ignore -m features.uk_trusts report     # step 0 counts and parser checks (no returns)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from classify import uk_nav
from ingest.uk.common import session

D = Path("data/trusts")
BASES = ["cum_income_debt_fair", "cum_income_debt_par", "cum_income_basis_unstated_debt", "nav_basis_not_stated",
         "ex_income_debt_fair", "ex_income_debt_par", "ex_income_basis_unstated_debt"]     # preference order
MIN_NAV_PER_YEAR = 40           # design: weekly or daily NAVs only
DISCOUNT_RANGE = (-0.60, 0.30)
MAX_PRICE_GAP_DAYS = 5


def tickers() -> list[str]:
    with session(read_only=True) as con:
        return [r[0] for r in con.execute(f"""SELECT DISTINCT tidm FROM ann_index
            WHERE regexp_matches(lower(headline), '{uk_nav.HEADLINE_RE}') ORDER BY 1""").fetchall()]


def actual_close(df: pd.DataFrame) -> pd.Series:
    """Yahoo's Close is adjusted for later splits and consolidations: undo them (price then = close x later ratios)."""
    ratio = df["Stock Splits"].replace(0, 1.0).fillna(1.0)
    later = ratio[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    return df.Close * later


def fetch_prices() -> None:
    import yfinance as yf
    D.mkdir(parents=True, exist_ok=True)
    frames, missing = [], []
    for i, t in enumerate(tickers(), 1):
        try:
            df = yf.Ticker(f"{t}.L").history(start="2011-06-01", auto_adjust=False, actions=True)
        except Exception:
            df = pd.DataFrame()
        if df.empty:
            missing.append(t)
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        out = pd.DataFrame({"tidm": t, "date": df.index, "open": df.Open.to_numpy(), "close": df.Close.to_numpy(),
                            "adj_close": df["Adj Close"].to_numpy(), "volume": df.Volume.to_numpy(),
                            "actual_close": actual_close(df).to_numpy(), "split": df["Stock Splits"].to_numpy()})
        frames.append(out)
        if i % 50 == 0:
            print(i, "tickers", flush=True)
        time.sleep(0.3)
    pd.concat(frames, ignore_index=True).to_parquet(D / "prices.parquet")
    print(f"prices for {len(frames)} tickers; none for {len(missing)}: {missing}")


def nav_table() -> pd.DataFrame:
    """One NAV in pence per announcement, on one basis per trust (the first basis in preference order
    present in at least 80% of that trust's announcements, else its most common one)."""
    L = uk_nav.labels()
    with session(read_only=True) as con:
        idx = con.execute(f"""SELECT ann_id, tidm, published_uk FROM ann_index
            WHERE regexp_matches(lower(headline), '{uk_nav.HEADLINE_RE}')""").df()
    d = L.merge(idx, on="ann_id")
    d = d[d.company_found & d.nav_date.notna() & d.unit.isin(["pence", "pounds"])].copy()
    mult = np.where(d.unit == "pounds", 100.0, 1.0)
    rows = []
    for tidm, g in d.groupby("tidm"):
        share = g[BASES].notna().mean()
        ok = [b for b in BASES if share[b] >= 0.8]
        basis = ok[0] if ok else share.idxmax()
        x = g[g[basis].notna()]
        rows.append(pd.DataFrame({"tidm": tidm, "published_uk": x.published_uk, "nav_date": x.nav_date,
                                  "nav_p": x[basis].to_numpy() * mult[d.index.get_indexer(x.index)], "basis": basis}))
    return pd.concat(rows, ignore_index=True)


def build() -> pd.DataFrame:
    nav = nav_table()
    px = pd.read_parquet(D / "prices.parquet")
    px = px[px.volume > 0].sort_values(["date", "tidm"])
    px["date"] = px.date.astype("datetime64[ns]")
    nav["nav_date"] = nav.nav_date.astype("datetime64[ns]")
    nav = nav[nav.nav_date.notna()].sort_values(["nav_date", "tidm"])
    m = pd.merge_asof(nav, px[["tidm", "date", "actual_close", "close"]], left_on="nav_date", right_on="date", by="tidm",
                      tolerance=pd.Timedelta(days=MAX_PRICE_GAP_DAYS))
    m["discount"] = m.actual_close / m.nav_p - 1
    m["month"] = m.published_uk.dt.to_period("M")
    m = m.sort_values("published_uk").drop_duplicates(["tidm", "month"], keep="last")     # last NAV of the month
    m.to_parquet(D / "discounts.parquet")
    return m


def report() -> None:
    pd.set_option("display.width", 220, "display.float_format", lambda x: f"{x:,.3f}")
    m = pd.read_parquet(D / "discounts.parquet")
    with session(read_only=True) as con:
        freq = con.execute(f"""SELECT tidm, year(published_uk) y, count(*) n FROM ann_index
            WHERE regexp_matches(lower(headline), '{uk_nav.HEADLINE_RE}') GROUP BY 1, 2""").df()
    frequent = set(freq.groupby("tidm").n.median().loc[lambda s: s >= MIN_NAV_PER_YEAR].index)
    L = uk_nav.labels()
    print(f"labels: {len(L)}; company found {L.company_found.mean():.3f}; units: {L.unit.value_counts().to_dict()}")
    print(f"trusts with NAV announcements: {freq.tidm.nunique()}; with >= {MIN_NAV_PER_YEAR} a year (median): {len(frequent)}")
    print(f"trust-months with a NAV in pence: {len(m)} for {m.tidm.nunique()} trusts; with a price too: {m.discount.notna().sum()} for {m[m.discount.notna()].tidm.nunique()} trusts")
    print("basis used per trust:", m.drop_duplicates("tidm").basis.value_counts().to_dict())
    d = m[m.discount.notna() & m.tidm.isin(frequent)]
    inside = d.discount.between(*DISCOUNT_RANGE)
    print(f"frequent-NAV trusts: {d.tidm.nunique()} trusts, {len(d)} trust-months; discount inside {DISCOUNT_RANGE}: {inside.mean():.4f}")
    bad = d[~inside]
    print("outside the range, by trust (top 12):", bad.tidm.value_counts().head(12).to_dict())
    print("ratio price/NAV of those outside, quantiles:", (bad.discount + 1).quantile([.05, .25, .5, .75, .95]).round(3).to_dict())
    months = d[inside].groupby("tidm").month.nunique()
    print(f"trusts with >= 60 monthly discounts: {(months >= 60).sum()}; >= 24: {(months >= 24).sum()}")
    print("trust-months per year (inside range):", d[inside].groupby(d.published_uk.dt.year).size().to_dict())
    print("median discount per year:", d[inside].groupby(d.published_uk.dt.year).discount.median().round(3).to_dict())
    cur = d[inside & (d.month == d.month.max())].sort_values("discount")
    print(f"latest month {d.month.max()}: {len(cur)} trusts; widest and narrowest five:")
    print(pd.concat([cur.head(5), cur.tail(5)])[["tidm", "nav_date", "nav_p", "actual_close", "discount", "basis"]].to_string(index=False))


if __name__ == "__main__":
    {"prices": fetch_prices, "build": build, "report": report}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
