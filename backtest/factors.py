"""Monthly large-company factor portfolio. Design: backtest/DESIGN_FACTORS.md (fixed before any result).

    uv run python -W ignore -m backtest.factors choose          # 2013-01 .. 2021-12
    uv run python -W ignore -m backtest.factors final --final   # 2022-01 .. latest, once, frozen
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from backtest.us_earnings import OUT
from backtest.us_insiders import spread_table
from core.log import get_logger
from features.us_earnings import fundamentals
from features.us_earnings_labels import valuation
from ingest.us.common import session

log = get_logger("backtest")
N_UNIVERSE, N_HOLD = 500, 30
COST_SIDE = 0.0015 + 0.0010                      # FX + slippage; plus half the spread
CHOOSE = (pd.Timestamp("2013-01-01"), pd.Timestamp("2021-12-31"))
VARIANTS = ["V", "Q", "M", "QVM"]


def month_end_panel(con) -> pd.DataFrame:
    """Every liquid stock at every month-end: price, 20-day dollar volume, 12-1 month momentum, its CIK."""
    df = con.execute("""
        WITH p AS (
            SELECT ticker, date, close, adj_close, volume,
                   avg(close * volume) OVER w20 AS dvol20,
                   lag(adj_close, 21) OVER w AS adj_1m, lag(adj_close, 252) OVER w AS adj_12m,
                   date_trunc('month', date) AS m,
                   row_number() OVER (PARTITION BY ticker, date_trunc('month', date) ORDER BY date DESC) AS last_in_month
            FROM us_prices_daily WHERE NOT is_stale AND NOT is_suspect AND ticker NOT IN ('SPY','IWM')
            WINDOW w AS (PARTITION BY ticker ORDER BY date),
                   w20 AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW))
        SELECT p.ticker, p.date, p.close, p.adj_close, p.dvol20, adj_1m / adj_12m - 1 AS mom_12_1, t.cik
        FROM p JOIN (SELECT DISTINCT ticker, cik FROM sec_ticker_map) t USING (ticker)
        WHERE last_in_month = 1 AND p.close >= 5 AND p.dvol20 >= 20e6
          -- only the market's real month-end trading day (a stock that didn't trade then is skipped that month)
          AND p.date IN (SELECT max(date) FROM us_prices_daily WHERE ticker = 'SPY' AND NOT is_stale
                         GROUP BY date_trunc('month', date))""").df()
    df["date"] = pd.to_datetime(df.date)
    # one share class per company: the most traded
    return df.sort_values("dvol20", ascending=False).drop_duplicates(["cik", "date"]).reset_index(drop=True)


def rank(s: pd.Series, by: pd.Series) -> pd.Series:
    """Percentile rank within each month; missing = 0.5."""
    return s.groupby(by).rank(pct=True).fillna(0.5)


def build_scores() -> pd.DataFrame:
    with session(read_only=True, memory_limit="10GB") as con:
        pan = month_end_panel(con)
        log.info("month-end panel: %d rows", len(pan))
        pan["key"] = pan.ticker + "|" + pan.date.dt.strftime("%Y%m%d")
        v = pan.rename(columns={"key": "accession", "close": "prev_close"}) \
            .assign(trade_date=lambda x: x.date + pd.Timedelta(days=1))[["accession", "cik", "ticker", "trade_date", "prev_close"]]
        val = valuation(con, v)[["accession", "mcap"]].rename(columns={"accession": "key"})
        pan = pan.merge(val, on="key", how="left")
        pan = pan[pan.mcap.notna()]
        pan["size_rank"] = pan.groupby("date").mcap.rank(ascending=False)
        uni = pan[pan.size_rank <= N_UNIVERSE].copy()
        f = fundamentals(con, uni.assign(accession=uni.key, trade_date=uni.date + pd.Timedelta(days=1))
                         [["accession", "cik", "trade_date"]]).rename(columns={"accession": "key"})
    u = uni.merge(f[["key", "ni_fy", "equity", "rev_fy", "op_inc_fy", "lt_debt", "assets"]], on="key", how="left")
    with np.errstate(divide="ignore", invalid="ignore"):
        u["ey"] = u.ni_fy / u.mcap
        u["bm"] = (u.equity / u.mcap).where(u.equity > 0)
        u["sp"] = u.rev_fy / u.mcap
        u["roe"] = (u.ni_fy / u.equity).where(u.equity > 0)
        u["opm"] = (u.op_inc_fy / u.rev_fy).where(u.rev_fy > 0)
        u["lowlev"] = -(u.lt_debt.fillna(0) / u.assets)
    u = u.replace([np.inf, -np.inf], np.nan)
    g = u.date
    u["V"] = (rank(u.ey, g) + rank(u.bm, g) + rank(u.sp, g)) / 3
    u["Q"] = (rank(u.roe, g) + rank(u.opm, g) + rank(u.lowlev, g)) / 3
    u["M"] = rank(u.mom_12_1, g)
    u["QVM"] = (u.V + u.Q + u.M) / 3
    return u


def simulate(u: pd.DataFrame, px: pd.DataFrame, spy: pd.Series, variant: str, start, end) -> pd.DataFrame:
    """Monthly holding-period returns. Picks at month-end m, traded at the next day's close, held to the
    next rebalance's trade day."""
    dates = sorted(d for d in u.date.unique() if start - pd.Timedelta(days=40) <= d <= end)
    cal = px.index
    rows, prev = [], set()
    for m0, m1 in zip(dates[:-1], dates[1:]):
        t0 = cal[cal.searchsorted(m0, side="right")]
        t1 = cal[min(cal.searchsorted(m1, side="right"), len(cal) - 1)]
        if t0 < start:
            continue
        sel = u[u.date == m0].nlargest(N_HOLD, variant)
        names = list(sel.ticker)
        r = (px.loc[t1, names] / px.loc[t0, names] - 1).fillna(0)
        new, gone = set(names) - prev, prev - set(names)
        sp = spread_table(sel.set_index("ticker").dvol20.reindex(names).to_numpy())
        side = COST_SIDE + sp / 2
        turnover_cost = (side * np.isin(names, list(new))).sum() / N_HOLD + len(gone) * side.mean() / N_HOLD
        rows.append(dict(t0=t0, t1=t1, ret=r.mean() - turnover_cost, spy=spy.loc[t1] / spy.loc[t0] - 1,
                         turnover=len(new) / N_HOLD))
        prev = set(names)
    return pd.DataFrame(rows)


def survivorship(con) -> pd.DataFrame:
    """Share of large filers (public float >= $5bn in their latest 10-K) with no price in our data."""
    return con.execute("""
        WITH f AS (SELECT cik, year(filed) AS y, max(val) AS pf FROM us_facts
                   WHERE concept = 'EntityPublicFloat' AND form LIKE '10-K%' GROUP BY 1, 2),
        priced AS (SELECT DISTINCT t.cik, year(p.date) AS y FROM us_prices_daily p
                   JOIN (SELECT DISTINCT ticker, cik FROM sec_ticker_map) t USING (ticker))
        SELECT f.y, count(*) AS large_filers, count(*) FILTER (WHERE pr.cik IS NULL) AS no_price
        FROM f LEFT JOIN priced pr ON pr.cik = f.cik AND pr.y = f.y
        WHERE f.pf >= 5e9 AND f.y BETWEEN 2013 AND 2026 GROUP BY 1 ORDER BY 1""").df()


def run(stage: str, final: bool = False) -> str:
    if stage == "final" and not final:
        raise SystemExit("the test period is run once, after the user's OK: add --final")
    u = build_scores()
    with session(read_only=True, memory_limit="10GB") as con:
        px = con.execute("SELECT ticker, date, adj_close FROM us_prices_daily WHERE NOT is_stale").df()
        surv = survivorship(con)
    px["date"] = pd.to_datetime(px.date)
    px = px.pivot(index="date", columns="ticker", values="adj_close").sort_index().ffill(limit=5)
    spy = px["SPY"]
    start, end = CHOOSE if stage == "choose" else (pd.Timestamp("2022-01-01"), px.index[-1])
    out, yearly = {}, {}
    for v in VARIANTS:
        s = simulate(u, px, spy, v, start, end)
        s["year"] = s.t1.dt.year
        y = s.groupby("year").apply(lambda g: pd.Series({"strat": (1 + g.ret).prod() - 1, "spy": (1 + g.spy).prod() - 1}))
        yearly[v] = y
        tot, tot_spy = (1 + s.ret).prod() - 1, (1 + s.spy).prod() - 1
        n_years = len(y)
        pre20 = s[s.year <= 2019]
        out[v] = dict(total=tot, spy_total=tot_spy, cagr=(1 + tot) ** (1 / n_years) - 1,
                      spy_cagr=(1 + tot_spy) ** (1 / n_years) - 1, years_beat=int((y.strat > y.spy).sum()), n_years=n_years,
                      beat_to_2019=((1 + pre20.ret).prod() - (1 + pre20.spy).prod()) if len(pre20) else np.nan,
                      turnover=s.turnover.mean())
    res = pd.DataFrame(out).T
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / f"factors_{stage}.csv")
    yr = pd.concat({v: yearly[v].strat for v in VARIANTS}, axis=1).assign(SPY=yearly["QVM"].spy)
    surv["missing_pct"] = (100 * surv.no_price / surv.large_filers).round(1)
    return "\n".join([f"# Factor portfolio {stage}: {start.date()} .. {end.date()} (after costs, $6k, top {N_HOLD} of {N_UNIVERSE})",
                      res.round(4).to_string(), "", "Yearly returns %:", (yr * 100).round(1).to_string(), "",
                      "Survivorship check: large filers (public float >= $5bn) with no price in our data:",
                      surv.to_string(index=False)])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["choose", "final"])
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    print(run(a.stage, a.final))


if __name__ == "__main__":
    main()
