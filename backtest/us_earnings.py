"""Phase 4: walk-forward backtest of the US earnings strategy. Design: backtest/DESIGN_US_EARNINGS.md
(fixed before any result was seen; do not change it to chase results).

    uv run python -W ignore -m backtest.us_earnings choose     # 2019–2021 (the choose years)
    uv run python -W ignore -m backtest.us_earnings final --final   # 2022–2026, ONCE, after the user's OK

Writes data/backtest/us_earnings_<stage>_{trades,summary}.parquet and a markdown report."""
from __future__ import annotations

import argparse
import json
from datetime import datetime

import numpy as np
import pandas as pd

from core.config import ROOT, load_config
from core.log import get_logger

log = get_logger("backtest")
FEAT = ROOT / "data" / "features" / "us_earnings_model.parquet"
OUT = ROOT / "data" / "backtest"
TEST_START = pd.Timestamp("2022-01-01")
CHOOSE = [2019, 2020, 2021]
FIRST_PRED_YEAR = 2016
RULES = "abcdef"
HOLD_CLOSE = {"e": 20, "f": 60}          # buy at the day-1 close, hold N trading days
REACT = ["gap", "day1_ret", "day1_x"]       # known at the day-1 close: only for e/f
SIZES = [30, 100, 200, 500]
SPREAD_X = [1.0, 1.5, 2.0]
MIN_PRICE, MIN_DOLLAR_VOL = 1.0, 500_000
UNIVERSES = {"all": MIN_DOLLAR_VOL, "liquid": 20_000_000}     # design addendum 2
BIG_CAP = 10e9
SPREAD_FALLBACK = 0.01          # 1% when no estimate (fewer than 5 prior days)
CLIP_Y = 0.5
LGB = dict(n_estimators=400, learning_rate=0.03, num_leaves=15, min_child_samples=200, subsample=0.8,
           subsample_freq=1, colsample_bytree=0.8, objective="l2", random_state=7, verbose=-1, n_jobs=4)

B = ["ret_5d", "ret_20d", "vol_20d", "log_dvol", "after_close", "assets", "cash", "equity", "lt_debt", "ni_fy",
     "op_inc_fy", "rev_fy", "profitable", "rev_growth", "ni_margin", "roe", "debt_to_assets", "cash_to_assets",
     "log_assets", "cash_runway_y", "item_agreement", "item_agreement_end", "item_bankruptcy",
     "item_acquisition_done", "item_new_debt", "item_exit_costs", "item_impairment", "item_delisting_notice",
     "item_auditor_change", "item_non_reliance", "item_exec_change", "item_reg_fd", "item_other_event",
     "n_8k_90d", "n_exec_change_90d", "n_non_reliance_90d", "n_agreement_90d", "eps_sdiff_std", "eps_sdiff_n",
     "eps_last_known", "log_mcap", "earnings_yield", "book_to_market", "sales_to_price", "spread_cs"]
N = B + ["sue", "sue_adj", "eps_yoy_to_price", "rev_yoy", "swing_to_loss", "swing_to_profit", "guid_eps_chg",
         "guid_rev_chg", "guid_first"]
L = N + ["tone", "guid_stated", "consensus", "confidence_n", "is_release", "fp_q4", "fp_unknown", "n_events"] + \
    ["ev_ceo_change", "ev_cfo_change", "ev_other_exec_change", "ev_restatement_or_accounting_issue",
     "ev_impairment_or_writedown", "ev_going_concern_doubt", "ev_restructuring_or_layoffs", "ev_buyback",
     "ev_dividend_increase", "ev_dividend_cut_or_suspension", "ev_acquisition_or_merger", "ev_divestiture",
     "ev_litigation_or_regulatory", "ev_debt_financing", "ev_equity_raise", "ev_strategic_review", "ev_other"]
MODELS = {"B": B, "N": N, "L": L}


# ---------------------------------------------------------------- costs
def corwin_schultz(high: np.ndarray, low: np.ndarray) -> float:
    """Mean two-day Corwin–Schultz (2012) spread estimate over a window; negative estimates -> 0."""
    h, l = np.asarray(high, float), np.asarray(low, float)
    ok = (h > 0) & (l > 0) & (h >= l)
    if ok.sum() < 3:
        return np.nan
    k = 3 - 2 * np.sqrt(2)
    b = np.log(h[:-1] / l[:-1]) ** 2 + np.log(h[1:] / l[1:]) ** 2
    g = np.log(np.maximum(h[:-1], h[1:]) / np.minimum(l[:-1], l[1:])) ** 2
    a = (np.sqrt(2 * b) - np.sqrt(b)) / k - np.sqrt(g / k)
    s = 2 * (np.exp(a) - 1) / (1 + np.exp(a))
    s = s[ok[:-1] & ok[1:]]
    return float(np.mean(np.maximum(s, 0))) if len(s) else np.nan


def round_trip_cost(size: float, price: np.ndarray, spread: np.ndarray, spread_x: float = 1.0) -> np.ndarray:
    """Fraction of the position lost to costs, buy + sell."""
    c = load_config()["us_costs"]
    shares = size / price
    comm = np.minimum(np.maximum(c["commission_per_share_usd"] * shares, c["commission_min_usd"]),
                      c["commission_max_pct"] / 100 * size) / size
    sp = np.nan_to_num(np.asarray(spread, float), nan=SPREAD_FALLBACK)    # fixed, not a sample statistic
    per_side = comm + c["fx_pct"] / 100 + c["slippage_pct"] / 100 + spread_x * sp / 2
    return 2 * per_side


def add_spreads(df: pd.DataFrame) -> pd.Series:
    """Corwin–Schultz over the 20 trading days before each trade (prices before the trade only)."""
    from features.us_earnings import Prices
    from ingest.us.common import session
    with session(read_only=True, memory_limit="6GB") as con:
        P = Prices(con, sorted(set(df.ticker.dropna())))
    h, l = P.cols["high"], P.cols["low"]
    out = np.full(len(df), np.nan)
    for k, (t, d) in enumerate(zip(df.ticker, df.trade_date)):
        i = P.locate(t, d) if isinstance(t, str) else None
        if i is not None:
            s, _ = P.span[t]
            j = max(s, i - 20)
            if i - j >= 5:
                out[k] = corwin_schultz(h[j:i], l[j:i])
    return pd.Series(out, index=df.index)


# ---------------------------------------------------------------- data
def load(final: bool = False, min_dvol: float = MIN_DOLLAR_VOL) -> pd.DataFrame:
    d = pd.read_parquet(FEAT)
    d["trade_date"] = pd.to_datetime(d.trade_date)
    if not final:
        d = d[d.trade_date < TEST_START]                 # the test years stay unseen
    d = d[d.skip.isna() & ~d.fwd_bad.fillna(True).astype(bool) & d.prev_close.notna()]
    d = d[(d.prev_close >= MIN_PRICE) & (d.dollar_vol_20d >= min_dvol)].copy()
    d["year"] = d.trade_date.dt.year
    d["log_dvol"] = np.log(d.dollar_vol_20d)
    d["after_close"] = (d.session == "after_close").astype(int)
    d["guid_first"] = d.guid_is_first.astype(float)
    d["guid_stated"] = d.guidance_change_stated.map({"raised": 1, "maintained": 0, "mixed": 0, "lowered": -1,
                                                     "withdrawn": -2, "not_stated": np.nan})
    d["consensus"] = d.vs_consensus.map({"beat": 1, "in_line": 0, "mixed": 0, "missed": -1, "not_mentioned": np.nan})
    d["confidence_n"] = d.confidence.map({"high": 2, "medium": 1, "low": 0})
    d["is_release"] = d.is_earnings_release.astype(float)
    d["fp_q4"] = (d.fiscal_period.isin(["Q4", "FY"])).astype(float)
    d["fp_unknown"] = (d.fiscal_period.isin(["unknown", "other"])).astype(float)
    big = d.mcap >= BIG_CAP
    for r in "abcd":
        d[f"y_{r}"] = np.where(big, d[f"x_{r}_spy"], d[f"x_{r}_iwm"])
    d = add_close_entry(d, big)
    d["spread_cs"] = add_spreads(d)
    return d.reset_index(drop=True)


def close_entry(o, c, bad, i: int, end: int, hold: int):
    """Buy at the close of day i (the trade date), sell at the close `hold` days later.
    -> (return, ok); not ok if the window runs past the data or has a stale/suspect bar."""
    j = i + hold
    if j >= end or c[i] <= 0 or bad[i:j + 1].any():
        return np.nan, False
    return c[j] / c[i] - 1, True


def add_close_entry(d: pd.DataFrame, big: pd.Series) -> pd.DataFrame:
    """Rules e/f (design addendum): outcomes from the day-1 close, plus the day-1 reaction features."""
    from features.us_earnings import Prices
    from ingest.us.common import session
    with session(read_only=True, memory_limit="6GB") as con:
        P = Prices(con, sorted(set(d.ticker.dropna())) + ["SPY", "IWM"])
    o, c = P.cols["open"], P.cols["close"]
    res = {k: np.full(len(d), np.nan) for k in ["r_e", "r_f", "spy_e", "spy_f", "iwm_e", "iwm_f", "spy_1", "iwm_1"]}
    bench = {}
    for sym in ("SPY", "IWM"):
        s0, e0 = P.span[sym]
        bench[sym] = (s0, e0, {d_: k for k, d_ in enumerate(P.dates[s0:e0])})
    for k, (t, day) in enumerate(zip(d.ticker, d.trade_date)):
        i = P.locate(t, day)
        if i is None:
            continue
        _, e = P.span[t]
        for r, h in HOLD_CLOSE.items():
            res[f"r_{r}"][k] = close_entry(o, c, P.bad, i, e, h)[0]
        dd = np.datetime64(pd.Timestamp(day).date(), "D")
        for sym in ("SPY", "IWM"):
            s0, e0, pos = bench[sym]
            if dd in pos:
                bi = s0 + pos[dd]
                res[f"{sym.lower()}_1"][k] = c[bi] / o[bi] - 1
                for r, h in HOLD_CLOSE.items():
                    res[f"{sym.lower()}_{r}"][k] = close_entry(o, c, P.bad, bi, e0, h)[0]
    for k, v in res.items():
        d[k] = v
    for r in HOLD_CLOSE:
        d[f"y_{r}"] = np.where(big, d[f"r_{r}"] - d[f"spy_{r}"], d[f"r_{r}"] - d[f"iwm_{r}"])
    d["gap"] = d.entry_open / d.prev_close - 1
    d["day1_ret"] = d.r_a
    d["day1_x"] = d.r_a - np.where(big, d.spy_1, d.iwm_1)
    return d


# ---------------------------------------------------------------- walk-forward
def predict_years(d: pd.DataFrame, feats: list[str], rule: str, years: list[int]) -> pd.Series:
    import lightgbm as lgb
    pred = pd.Series(np.nan, index=d.index)
    y = f"y_{rule}"
    for Y in years:
        embargo = f"{Y - 1}-10-01" if rule == "f" else f"{Y - 1}-12-01"     # holds must end before Y
        tr = d[(d.trade_date < pd.Timestamp(embargo)) & d[y].notna()]
        te = d.year == Y
        m = lgb.LGBMRegressor(**LGB).fit(tr[feats].astype(float), tr[y].clip(-CLIP_Y, CLIP_Y))
        pred[te] = m.predict(d.loc[te, feats].astype(float))
    return pred


def evaluate(d: pd.DataFrame, years: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    from scipy.stats import spearmanr
    rows, trades = [], []
    pred_years = list(range(FIRST_PRED_YEAR, max(years) + 1))
    for name, feats in MODELS.items():
        for rule in RULES:
            p = predict_years(d, feats + (REACT if rule in HOLD_CLOSE else []), rule, pred_years)
            y = d[f"y_{rule}"]
            for Y in years:
                cur = (d.year == Y) & y.notna()
                cut = p[d.year == Y - 1].quantile(0.9)             # previous year's out-of-sample top decile
                lo_cut = p[d.year == Y - 1].quantile(0.1)
                ic = spearmanr(p[cur], y[cur]).statistic
                for size in SIZES:
                    cost1 = round_trip_cost(size, d.prev_close.to_numpy(), d.spread_cs.to_numpy())
                    for sel, mask in (("S1", cur & (p > cost1)), ("S2", cur & (p > cut))):
                        g = y[mask]
                        for sx in SPREAD_X:
                            c = round_trip_cost(size, d.prev_close.to_numpy(), d.spread_cs.to_numpy(), sx)[mask.to_numpy()]
                            net = g.to_numpy() - c
                            rows.append(dict(model=name, rule=rule, year=Y, select=sel, size=size, spread_x=sx,
                                             n=len(g), gross=g.mean(), net=net.mean() if len(g) else np.nan,
                                             hit=(net > 0).mean() if len(g) else np.nan,
                                             t=(net.mean() / net.std(ddof=1) * np.sqrt(len(net))) if len(g) > 2 else np.nan,
                                             ic=ic, all_mean=y[cur].mean(),
                                             short_gross=-y[cur & (p < lo_cut)].mean(), short_n=int((cur & (p < lo_cut)).sum())))
                        if size == 500:
                            trades.append(d.loc[mask, ["accession", "ticker", "trade_date", f"y_{rule}"]]
                                          .assign(model=name, rule=rule, select=sel, pred=p[mask],
                                                  cost_500=round_trip_cost(500, d.prev_close.to_numpy(),
                                                                           d.spread_cs.to_numpy())[mask.to_numpy()])
                                          .rename(columns={f"y_{rule}": "y"}))
            log.info("model %s rule %s done", name, rule)
    return pd.DataFrame(rows), pd.concat(trades, ignore_index=True)


def pooled(s: pd.DataFrame) -> pd.DataFrame:
    """All choose years together, weighting each trade equally."""
    s = s.assign(net_sum=s.net * s.n, gross_sum=s.gross * s.n, hit_sum=s.hit * s.n)
    g = s.groupby(["model", "rule", "select", "size", "spread_x"]).agg(
        n=("n", "sum"), gross_sum=("gross_sum", "sum"), net_sum=("net_sum", "sum"), hit_sum=("hit_sum", "sum"),
        ic=("ic", "mean"), years_net_pos=("net", lambda x: int((x > 0).sum())))
    g["gross"], g["net"], g["hit"] = g.gross_sum / g.n, g.net_sum / g.n, g.hit_sum / g.n
    return g.drop(columns=["gross_sum", "net_sum", "hit_sum"]).reset_index()


def run(stage: str, final: bool = False, universe: str = "all") -> str:
    if stage == "final" and not final:
        raise SystemExit("the test years are run once, after the user's OK: add --final")
    years = CHOOSE if stage == "choose" else [2022, 2023, 2024, 2025, 2026]
    d = load(final=(stage == "final"), min_dvol=UNIVERSES[universe])
    stage = stage if universe == "all" else f"{stage}_{universe}"
    log.info("tradable rows: %d (%s)", len(d), d.year.value_counts().sort_index().to_dict())
    summ, trades = evaluate(d, years)
    OUT.mkdir(parents=True, exist_ok=True)
    summ.to_parquet(OUT / f"us_earnings_{stage}_summary.parquet")
    trades.to_parquet(OUT / f"us_earnings_{stage}_trades.parquet")
    p = pooled(summ)
    p.to_parquet(OUT / f"us_earnings_{stage}_pooled.parquet")
    meta = {"stage": stage, "run_at": datetime.now().isoformat(), "rows": len(d), "years": years}
    (OUT / f"us_earnings_{stage}_meta.json").write_text(json.dumps(meta))
    return report(stage)


def report(stage: str) -> str:
    p = pd.read_parquet(OUT / f"us_earnings_{stage}_pooled.parquet")
    s = pd.read_parquet(OUT / f"us_earnings_{stage}_summary.parquet")
    base = p[p.spread_x == 1.0]
    lines = [f"# US earnings backtest: {stage} years", "",
             "Pooled over the years; net = after costs at spread ×1. Per trade, excess return vs SPY/IWM.", ""]
    ic = s.drop_duplicates(["model", "rule", "year"]).pivot_table(index=["model", "rule"], columns="year", values="ic")
    lines += ["## Rank correlation of prediction with outcome (all tradable rows)", ic.round(3).to_string(), ""]
    for sel in ("S1", "S2"):
        t = base[base["select"] == sel].pivot_table(index=["model", "rule"], columns="size",
                                                   values=["n", "net"], aggfunc="first")
        lines += [f"## {sel}: trades and mean net return per trade", (t.round(4)).to_string(), ""]
    g = base[base["size"] == 500].set_index(["model", "rule", "select"])[["n", "gross", "net", "hit", "years_net_pos"]]
    lines += ["## At $500 per trade", g.round(4).to_string(), ""]
    sx = p[p["size"] == 500].pivot_table(index=["model", "rule", "select"], columns="spread_x", values="net")
    lines += ["## Spread robustness at $500 (net per trade by spread multiplier)", sx.round(4).to_string(), ""]
    sh = s.drop_duplicates(["model", "rule", "year"]).groupby(["model", "rule"]).apply(
        lambda x: pd.Series({"short_gross": (x.short_gross * x.short_n).sum() / x.short_n.sum(), "n": x.short_n.sum()}))
    lines += ["## Shorts (bottom decile, gross, no borrow or margin costs)", sh.round(4).to_string(), ""]
    text = "\n".join(lines)
    (OUT / f"us_earnings_{stage}_report.md").write_text(text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["choose", "final", "report"])
    ap.add_argument("--final", action="store_true", help="allow the one-time run on 2022–2026")
    ap.add_argument("--of", default="choose", help="report: which stage")
    ap.add_argument("--universe", choices=list(UNIVERSES), default="all")
    a = ap.parse_args()
    print(report(a.of) if a.stage == "report" else run(a.stage, a.final, a.universe))


if __name__ == "__main__":
    main()
