"""Phase 3, second half: join the GPT-6 Luna labels and valuation onto the earnings events.

    uv run python -W ignore -m features.us_earnings_labels build
      reads  data/features/us_earnings_events.parquet  (features.us_earnings build)
             data/labels/us_earnings_labels.parquet     (classify.labels build)
      writes data/features/us_earnings_model.parquet

Everything here is known before the trade (the first open after the release):
- label fields come from the release itself;
- SUE uses the release's own EPS and prior-year EPS, scaled by the company's past EPS changes
  (eps_sdiff_std, from filings made before the trade);
- guidance change compares with the same company's previous release that guided the same period;
- valuation uses the prior close (undone back to the price of that day with the split history) and
  share counts / financials filed before the trade."""
from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd

from core.config import ROOT
from core.log import get_logger
from ingest.us.common import session

log = get_logger("features")
FEAT = ROOT / "data" / "features"
LABELS = ROOT / "data" / "labels" / "us_earnings_labels.parquet"
SUE_CLIP = 10.0

LABEL_COLS = ["is_earnings_release", "fiscal_period", "vs_consensus", "guidance_given", "guidance_change_stated",
              "guidance_eps_basis", "tone", "confidence", "n_events", "revenue_m", "revenue_prior_year_m",
              "eps_diluted_gaap", "eps_diluted_gaap_prior_year", "eps_adjusted", "eps_adjusted_prior_year",
              "guidance_period", "guidance_eps_low", "guidance_eps_high", "guidance_revenue_low_m",
              "guidance_revenue_high_m", "truncated"]


# ---------------------------------------------------------------- guidance
def period_key(s) -> str | None:
    """'FY2021', 'fiscal 2021', 'full year 2021', 'Q3 2018', 'third quarter of fiscal 2019', "FY'21"
    -> 'FY2021' / 'Q3-2018'. None when no year can be found."""
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.lower()
    y = re.search(r"(?:19|20)\d{2}", t)
    if y:
        year = y.group(0)
    else:
        y2 = re.search(r"(?:fy|f|q[1-4])\s*'?\s*(\d{2})\b|'(\d{2})\b", t)
        if not y2:
            return None
        year = "20" + (y2.group(1) or y2.group(2))
    q = re.search(r"\bq([1-4])\b|([1-4])q\b", t)
    if not q:
        for k, w in enumerate(["first", "second", "third", "fourth"], 1):
            if re.search(rf"\b{w} quarter", t):
                return f"Q{k}-{year}"
        if re.search(r"\b(h1|h2|half|month|week)", t):
            return None
        return f"FY{year}"
    return f"Q{q.group(1) or q.group(2)}-{year}"


def _mid(lo, hi):
    lo, hi = pd.to_numeric(lo, errors="coerce"), pd.to_numeric(hi, errors="coerce")
    return np.where(lo.notna() & hi.notna(), (lo + hi) / 2, lo.fillna(hi))


def guidance_changes(df: pd.DataFrame) -> pd.DataFrame:
    """For each release with guidance: change vs the company's most recent EARLIER release that guided
    the same period on the same EPS basis. Relative change of the midpoint (EPS scaled by |previous|,
    floored at 5 cents, so tiny bases don't explode)."""
    g = df[["accession", "cik", "acceptance_et", "guidance_period", "guidance_eps_basis",
            "guidance_eps_low", "guidance_eps_high", "guidance_revenue_low_m", "guidance_revenue_high_m"]].copy()
    g["pkey"] = g.guidance_period.map(period_key)
    g["eps_mid"] = _mid(g.guidance_eps_low, g.guidance_eps_high)
    g["rev_mid"] = _mid(g.guidance_revenue_low_m, g.guidance_revenue_high_m)
    g = g[g.pkey.notna() & (g.eps_mid.notna() | g.rev_mid.notna())].sort_values("acceptance_et")
    prev = g.groupby(["cik", "pkey"])
    g["eps_prev"] = prev.eps_mid.shift()
    g["basis_prev"] = prev.guidance_eps_basis.shift()
    g["rev_prev"] = prev.rev_mid.shift()
    same_basis = g.basis_prev == g.guidance_eps_basis
    g["guid_eps_chg"] = ((g.eps_mid - g.eps_prev) / g.eps_prev.abs().clip(lower=0.05)).where(same_basis)
    g["guid_rev_chg"] = (g.rev_mid / g.rev_prev - 1).where(g.rev_prev > 0)
    g["guid_is_first"] = g.eps_prev.isna() & g.rev_prev.isna()
    return g[["accession", "pkey", "eps_mid", "rev_mid", "guid_eps_chg", "guid_rev_chg", "guid_is_first"]] \
        .rename(columns={"pkey": "guidance_pkey", "eps_mid": "guid_eps_mid", "rev_mid": "guid_rev_mid"})


# ---------------------------------------------------------------- valuation
def split_factor(splits: pd.DataFrame, ev: pd.DataFrame, after: str, upto: str | None = None) -> pd.Series:
    """Product of split ratios with ev[after] < split date (<= ev[upto] when given), per event row."""
    m = ev[["ticker", after] + ([upto] if upto else [])].reset_index().merge(splits, on="ticker")
    keep = m.date > m[after]
    if upto:
        keep &= m.date <= m[upto]
    f = m[keep].groupby("index").ratio.prod()
    return f.reindex(ev.index).fillna(1.0)


def valuation(con, ev: pd.DataFrame) -> pd.DataFrame:
    """Market cap at the prior close = price as it was that day x shares outstanding as last reported
    before the trade (dei cover-page count, first-filed value), with shares carried through any split
    between their date and the trade."""
    splits = con.execute("SELECT ticker, date, ratio FROM us_splits").df()
    splits["date"] = pd.to_datetime(splits["date"])
    e = ev[["accession", "cik", "ticker", "trade_date", "prev_close"]].dropna().copy()
    e["trade_date"] = pd.to_datetime(e.trade_date)
    con.register("e", e[["accession", "cik", "trade_date"]])
    sh = con.execute("""
        WITH f AS (SELECT cik, end_date, min(filed) filed, arg_min(val, filed) val FROM us_facts
                   WHERE concept = 'EntityCommonStockSharesOutstanding' AND val > 0 GROUP BY ALL)
        SELECT e.accession, arg_max(f.val, f.end_date) shares, max(f.end_date) shares_date
        FROM e JOIN f ON f.cik = e.cik AND f.filed < e.trade_date AND f.end_date > e.trade_date - INTERVAL 200 DAY
        GROUP BY 1""").df()
    e = e.merge(sh, on="accession", how="left")
    e["shares_date"] = pd.to_datetime(e.shares_date)
    e = e.reset_index(drop=True)
    # prices are adjusted for every split up to today: undo those after the day before the trade
    # (a split effective on the trade date already adjusted the prior close, so "after the day before")
    e["price_then"] = e.prev_close * split_factor(splits, e.assign(t0=e.trade_date - pd.Timedelta(days=1)), "t0")
    e["shares_then"] = e.shares * split_factor(splits, e, "shares_date", "trade_date")
    e["mcap"] = e.price_then * e.shares_then
    e["has_split_history"] = e.ticker.isin(set(splits.ticker))
    return e[["accession", "price_then", "shares_then", "shares_date", "mcap", "has_split_history"]]


# ---------------------------------------------------------------- build
def build() -> pd.DataFrame:
    ev = pd.read_parquet(FEAT / "us_earnings_events.parquet")
    lab = pd.read_parquet(LABELS)
    ev_cols = [c for c in ev.columns if c not in ("truncated",)]
    df = ev[ev_cols].merge(lab[["accession", "labelled"] + LABEL_COLS + [c for c in lab.columns if c.startswith("ev_")]],
                           on="accession", how="left")
    df["acceptance_et"] = pd.to_datetime(df.acceptance_et)
    # SUE: the release's own year-on-year EPS change over the company's usual size of such changes
    d = df.eps_diluted_gaap - df.eps_diluted_gaap_prior_year
    df["eps_yoy"] = d
    df["sue"] = (d / df.eps_sdiff_std.where(df.eps_sdiff_std > 0.005)).clip(-SUE_CLIP, SUE_CLIP)
    da = df.eps_adjusted - df.eps_adjusted_prior_year
    df["sue_adj"] = (da / df.eps_sdiff_std.where(df.eps_sdiff_std > 0.005)).clip(-SUE_CLIP, SUE_CLIP)
    df["rev_yoy"] = (df.revenue_m / df.revenue_prior_year_m - 1).where(df.revenue_prior_year_m > 0)
    df["swing_to_loss"] = ((df.eps_diluted_gaap < 0) & (df.eps_diluted_gaap_prior_year >= 0)).astype(float) \
        .where(df.eps_diluted_gaap.notna() & df.eps_diluted_gaap_prior_year.notna())
    df["swing_to_profit"] = ((df.eps_diluted_gaap > 0) & (df.eps_diluted_gaap_prior_year <= 0)).astype(float) \
        .where(df.eps_diluted_gaap.notna() & df.eps_diluted_gaap_prior_year.notna())
    df = df.merge(guidance_changes(df), on="accession", how="left")
    with session(read_only=True, memory_limit="6GB") as con:
        df = df.merge(valuation(con, df), on="accession", how="left")
    with np.errstate(divide="ignore", invalid="ignore"):
        df["log_mcap"] = np.log(df.mcap.where(df.mcap > 0))
        df["earnings_yield"] = df.get("ni_fy") / df.mcap
        df["book_to_market"] = df.get("equity") / df.mcap
        df["sales_to_price"] = df.get("rev_fy") / df.mcap
        df["eps_yoy_to_price"] = (df.eps_yoy / df.price_then).clip(-1, 1)
    df = df.replace([np.inf, -np.inf], np.nan)
    path = FEAT / "us_earnings_model.parquet"
    df.to_parquet(path)
    log.info("model table: %d rows x %d columns; labelled %d; sue %d; guidance change %d; mcap %d -> %s",
             len(df), df.shape[1], int(df.labelled.fillna(False).sum()), int(df.sue.notna().sum()),
             int(df.guid_eps_chg.notna().sum() + 0), int(df.mcap.notna().sum()), path)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.parse_args()
    build()


if __name__ == "__main__":
    main()
