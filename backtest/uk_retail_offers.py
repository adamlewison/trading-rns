"""UK retail offers: apply at the issue price, sell on admission (backtest/DESIGN_UK_RETAIL_OFFERS.md).

    uv run python -W ignore -m backtest.uk_retail_offers choose           # launches up to 2024-12-31
    uv run python -W ignore -m backtest.uk_retail_offers final --final    # 2025 -> latest, ONCE, after the user agrees

Needs data/uk.duckdb (announcements, prices) and data/labels/uk_retail_offers.jsonl (classify.uk_retail_offers).
"""
from __future__ import annotations

import math
import sys

import numpy as np
import pandas as pd

from backtest.uk_news import spread_uk
from classify import uk_retail_offers as lab
from ingest.uk.common import session

CHOOSE_END = pd.Timestamp("2024-12-31 23:59:59")
STAKE, MAX_OPEN = 500.0, 6
MIN_CLOSE_P, MIN_TURNOVER = 5.0, 25_000.0
SLIP = 0.0025
DIV_YIELD = 0.035                      # FTSE All-Share price index + dividends, as in uk_directors
SAME_OFFER_DAYS, RESULT_DAYS, SALE_WAIT_DAYS = 30, 60, 7
ADVERSE_LIMIT, ADVERSE_FILL = 1.03, 0.25
PRICE_RANGE = (0.5, 1.05)              # issue price / last close; outside = parse error, counted
AFTER_CLOSE = pd.Timedelta(hours=16, minutes=30)


def known_and_next(dates: np.ndarray, published: pd.Timestamp) -> tuple[int | None, int | None]:
    """Row index of the last close known at the decision, and of the first trading day after the launch.
    After 16:30 the day's own close is known; before that (pre-open or intraday) only the previous close."""
    day = np.datetime64(published.normalize().date(), "D")
    after_close = published - published.normalize() >= AFTER_CLOSE
    i = int(np.searchsorted(dates, day, side="right" if after_close else "left"))
    return (i - 1 if i >= 1 else None), (i if i < len(dates) else None)


def adverse_fill(first_close: float | None, issue: float, net: float) -> float:
    """Design: full allocation unless the price is more than 3% above the issue price on the first day
    after the launch (a popular offer), then 25%. Unknown first-day price: whichever is worse."""
    if first_close is None or not first_close > 0:
        return 1.0 if net < 0 else ADVERSE_FILL
    return 1.0 if first_close <= issue * ADVERSE_LIMIT else ADVERSE_FILL


def offers(final: bool) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Launches of ONE period (the other period's rows are dropped before any price is read)."""
    L = lab.labels()
    with session(read_only=True) as con:
        idx = con.execute(f"""SELECT ann_id, tidm, headline, published_uk FROM ann_index
            WHERE regexp_matches(lower(headline), '{lab.HEADLINE_RE}')""").df()
    d = L.merge(idx, on="ann_id")
    d["published_uk"] = pd.to_datetime(d.published_uk)
    d = d[(d.published_uk > CHOOSE_END) if final else (d.published_uk <= CHOOSE_END)]
    res = d[d.kind == "result"].sort_values("published_uk")
    la = d[d.kind == "launch"].sort_values("published_uk")
    n = {"launch_announcements": len(la)}
    n["no_issue_price"] = int(la.issue_price_pence.isna().sum())
    la = la[la.issue_price_pence.notna()]
    n["existing_holders_only"] = int((la.existing_holders_only == True).sum())      # noqa: E712
    la = la[la.existing_holders_only != True]                                        # noqa: E712
    keep, last = [], {}
    for r in la.itertuples():                       # several announcements about one offer -> the first
        if r.tidm in last and (r.published_uk - last[r.tidm]).days <= SAME_OFFER_DAYS:
            continue
        last[r.tidm] = r.published_uk
        keep.append(r.Index)
    n["same_offer_repeats"] = len(la) - len(keep)
    return la.loc[keep], res, n


def outcomes(la: pd.DataFrame, res: pd.DataFrame) -> pd.DataFrame:
    with session(read_only=True) as con:
        con.register("want", pd.DataFrame({"tidm": sorted(set(la.tidm)) + ["^FTAS"]}))
        px = con.execute("""SELECT p.tidm, p.date, p.open, p.close, p.volume, p.is_stale OR p.is_suspect AS bad
            FROM prices_daily p SEMI JOIN want USING (tidm) ORDER BY 1, 2""").df()
    px["date"] = pd.to_datetime(px.date)
    by = {t: g.reset_index(drop=True) for t, g in px.groupby("tidm")}
    ft = by["^FTAS"]
    ftd = ft.date.to_numpy().astype("datetime64[D]")
    rows = []
    for r in la.itertuples():
        row = {"ann_id": r.ann_id, "tidm": r.tidm, "launch": r.published_uk, "headline": r.headline,
               "platform": r.platform, "issue": r.issue_price_pence, "gm": r.conditional_on_general_meeting}
        g = by.get(r.tidm)
        if g is None:
            rows.append({**row, "drop": "no_prices"}); continue
        dates = g.date.to_numpy().astype("datetime64[D]")
        k, nx = known_and_next(dates, r.published_uk)
        if k is None or g.bad[k] or (r.published_uk.normalize() - g.date[k]).days > 7:
            rows.append({**row, "drop": "no_clean_last_close"}); continue
        last_close = float(g.close[k])
        w = g.iloc[max(0, k - 19):k + 1]
        turnover = float((w.close * w.volume).mean() / 100)
        row.update(last_close=last_close, turnover=turnover, discount=1 - r.issue_price_pence / last_close)
        if not PRICE_RANGE[0] <= r.issue_price_pence / last_close <= PRICE_RANGE[1]:
            rows.append({**row, "drop": "price_out_of_range"}); continue
        if last_close < MIN_CLOSE_P or turnover < MIN_TURNOVER:
            rows.append({**row, "drop": "too_small_or_illiquid"}); continue
        rr = res[(res.tidm == r.tidm) & (res.published_uk > r.published_uk)
                 & (res.published_uk <= r.published_uk + pd.Timedelta(days=RESULT_DAYS))]
        cancelled = bool(rr.cancelled.any())
        adm = rr.admission_date.dropna()
        admission = adm.iloc[-1] if len(adm) else r.admission_date
        first_close = None if nx is None or g.bad[nx] else float(g.close[nx])
        row["first_day_move"] = None if first_close is None else first_close / last_close - 1
        cost = float(spread_uk(np.array([turnover]))[0]) / 2 + SLIP
        if cancelled:
            rows.append({**row, "cancelled": True, "gross_open": 0.0, "gross_close": 0.0, "net_open": 0.0,
                         "net_close": 0.0, "fill_open": 1.0, "fill_close": 1.0, "bench": 0.0, "days": 0,
                         "sale_day": rr.published_uk.iloc[-1].normalize()}); continue
        if pd.isna(admission) or admission <= g.date[k]:
            rows.append({**row, "drop": "no_admission_date"}); continue
        s = int(np.searchsorted(dates, np.datetime64(admission.date(), "D")))
        while s < len(g) and (g.bad[s] or not g.open[s] > 0) and (g.date[s] - admission).days <= SALE_WAIT_DAYS:
            s += 1
        if s >= len(g) or g.bad[s] or not g.open[s] > 0 or (g.date[s] - admission).days > SALE_WAIT_DAYS:
            rows.append({**row, "drop": "no_sale_price"}); continue
        b0 = int(np.searchsorted(ftd, dates[k], side="right")) - 1
        b1 = int(np.searchsorted(ftd, dates[s], side="right")) - 1
        days = int((g.date[s] - g.date[k]).days)
        bench = float(ft.close[b1] / ft.close[b0] - 1 + DIV_YIELD * days / 365)
        row.update(cancelled=False, admission=admission, sale_day=g.date[s], days=days, bench=bench, cost=cost)
        for name, p in (("open", float(g.open[s])), ("close", float(g.close[s]))):
            gross = p / r.issue_price_pence - 1
            row[f"gross_{name}"], row[f"net_{name}"] = gross, gross - cost
            row[f"fill_{name}"] = adverse_fill(first_close, r.issue_price_pence, gross - cost)
        rows.append(row)
    return pd.DataFrame(rows)


def tstat(x: pd.Series, cluster: pd.Series) -> float:
    d = x - x.mean()
    se = math.sqrt(float((d.groupby(cluster).sum() ** 2).sum())) / len(x)
    return float(x.mean() / se) if se > 0 else float("nan")


def account(o: pd.DataFrame, sale: str, adverse: bool) -> tuple[float, float, int]:
    """£500 per application, at most 6 outstanding. Returns (£ made, £ the index made on the same money, offers taken)."""
    open_, made, idx, n = [], 0.0, 0.0, 0
    for r in o.sort_values("launch").itertuples():
        open_ = [d for d in open_ if d > r.launch]
        if len(open_) >= MAX_OPEN:
            continue
        fill = getattr(r, f"fill_{sale}") if adverse else 1.0
        made += STAKE * fill * getattr(r, f"net_{sale}")
        idx += STAKE * fill * r.bench
        open_.append(r.sale_day)
        n += 1
    return made, idx, n


def evaluate(o: pd.DataFrame) -> pd.DataFrame:
    out = []
    for sel, sub in (("A", o), ("D5", o[o.discount >= 0.05])):
        for sale in ("open", "close"):
            if sub.empty:
                continue
            week = sub.launch.dt.strftime("%G-%V")
            full = sub[f"net_{sale}"] - sub.bench
            adv = sub[f"fill_{sale}"] * (sub[f"net_{sale}"] - sub.bench)
            no20 = adv[sub.launch.dt.year != 2020]
            made, idx, n = account(sub, sale, True)
            out.append({"variant": f"{sel}-{sale}", "offers": len(sub), "weeks": week.nunique(),
                        "win_net": (sub[f"net_{sale}"] > 0).mean(), "gross": sub[f"gross_{sale}"].mean(),
                        "net": sub[f"net_{sale}"].mean(), "median_net": sub[f"net_{sale}"].median(),
                        "excess_full": full.mean(), "t_full": tstat(full, week),
                        "excess_adverse": adv.mean(), "t_adverse": tstat(adv, week),
                        "excess_adverse_ex2020": no20.mean() if len(no20) else np.nan,
                        "excess_adverse_ex_best5": adv.drop(adv.nlargest(5).index).mean(),
                        "acct_offers": n, "acct_gbp": made, "index_gbp": idx})
    return pd.DataFrame(out).set_index("variant")


def report(final: bool) -> None:
    pd.set_option("display.width", 250, "display.max_rows", 200, "display.float_format", lambda x: f"{x:,.4f}")
    la, res, n = offers(final)
    o = outcomes(la, res)
    if "drop" not in o:
        o["drop"] = np.nan
    print("== counts ==")
    print(n, "| offers after de-duplication:", len(la))
    print(o["drop"].fillna("used").value_counts().to_string())
    ok = o[o["drop"].isna()].copy()
    print("\n== offers used, by year ==")
    print(ok.groupby(ok.launch.dt.year).agg(n=("tidm", "size"), discount=("discount", "mean"),
          first_day_move=("first_day_move", "mean"), days=("days", "mean"), cancelled=("cancelled", "sum"),
          below_issue_at_admission=("gross_open", lambda x: (x < 0).mean()), net_open=("net_open", "mean"),
          bench=("bench", "mean"), filled_25pct=("fill_open", lambda x: (x < 1).mean())).to_string())
    print("\n== by platform ==")
    print(ok.groupby("platform").agg(n=("tidm", "size"), discount=("discount", "mean"), net_open=("net_open", "mean")).to_string())
    print("\n== variants (excess = net return minus the FTSE All-Share over the same days; adverse allocation is the judge) ==")
    print(evaluate(ok).T.to_string())
    cols = ["tidm", "launch", "issue", "last_close", "discount", "first_day_move", "sale_day", "gross_open", "cost", "net_open", "fill_open", "bench"]
    s = ok[~ok.cancelled.astype(bool)].sort_values("net_open")
    print("\n== 5 worst and 5 best (sale at the open) ==")
    print(pd.concat([s.head(5), s.tail(5)])[cols].to_string(index=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "choose"
    if cmd == "final":
        if "--final" not in sys.argv:
            raise SystemExit("the final test runs once, with --final, after the user agrees")
        report(final=True)
    else:
        report(final=False)
