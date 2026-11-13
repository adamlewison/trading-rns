"""UK director buying. Design: backtest/DESIGN_UK_DIRECTORS.md (fixed before any result).

    uv run python -W ignore -m backtest.uk_directors choose          # 2019–2021
    uv run python -W ignore -m backtest.uk_directors final --final   # 2022 → latest, once, frozen, user's OK
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from backtest import us_account as ua
from backtest.uk_news import SIZE_GBP, round_trip
from backtest.us_earnings import OUT
from core.config import ROOT
from core.log import get_logger
from features.uk_news import OUT as EVENTS
from features.uk_news import UKPrices
from features.us_insiders import trail_exit
from ingest.uk.common import session

log = get_logger("backtest")
LABELS = ROOT / "data" / "labels" / "uk-rns-full-v1-20261113T064938.parquet"
CHOOSE, FINAL = [2019, 2020, 2021], [2022, 2023, 2024, 2025, 2026]
HOLDS = {"h20": 20, "h60": 60}
TRAIL_ATR, TRAIL_MAX = 3.0, 60
DIV_YIELD = 0.035
MIN_BUY_K = 20.0


def director_buys() -> pd.DataFrame:
    lab = pd.read_parquet(LABELS, columns=["ann_id", "director_dealing_side", "director_dealing_value_k"])
    lab = lab[lab.director_dealing_side == "buy"]
    with session(read_only=True) as con:
        con.register("l", lab[["ann_id"]])
        keys = con.execute("""SELECT a.ann_id, a.tidm, CAST(a.published_uk AS DATE) AS day
                              FROM announcements a SEMI JOIN l USING (ann_id)""").df()
    lab = lab.merge(keys, on="ann_id")
    lab["day"] = pd.to_datetime(lab.day)
    return lab.groupby(["tidm", "day"]).agg(buy_k=("director_dealing_value_k", lambda s: s.fillna(0).sum()),
                                            n_buy_ann=("ann_id", "size")).reset_index()


def outcomes(ev: pd.DataFrame) -> pd.DataFrame:
    with session(read_only=True) as con:
        P = UKPrices(con, sorted(set(ev.tidm)) + ["^FTAS"])
    o, h, l, c = (P.cols[k] for k in ["open", "high", "low", "close"])
    rows = []
    for r in ev.itertuples():
        i = P.locate(r.tidm, r.day)
        b = P.locate("^FTAS", r.day)
        row = {"tidm": r.tidm, "day": r.day}
        if i is None or b is None:
            rows.append(row); continue
        s, e = P.span[r.tidm]
        _, be = P.span["^FTAS"]
        tr = np.maximum(h[i - 20:i], c[i - 21:i - 1]) - np.minimum(l[i - 20:i], c[i - 21:i - 1])
        atr = float(np.mean(tr)) if i - s > 21 else np.nan

        def bench(k: int, at_open: bool) -> float:        # FTSE All-Share over the same days, plus dividends
            px = o[b + k] if at_open else c[b + k]
            days = (P.dates[b + k] - P.dates[b]).astype(int)
            return px / o[b] - 1 + DIV_YIELD * days / 365

        for name, n in HOLDS.items():
            if i + n - 1 < e and b + n - 1 < be and not P.bad[i:i + n].any():
                row[name] = c[i + n - 1] / o[i] - 1
                row[f"b_{name}"] = bench(n - 1, False)
                row[f"{name}_days"] = n
        if atr == atr:
            tx = trail_exit(o, h, l, c, i, e, atr, TRAIL_ATR, TRAIL_MAX)
            if tx and not P.bad[i:tx[0] + 1].any() and b + (tx[0] - i) < be:
                row["t60"] = tx[1] / o[i] - 1
                row["b_t60"] = bench(tx[0] - i, tx[2] == "open")
                row["t60_days"] = tx[0] - i + 1
        rows.append(row)
    return pd.DataFrame(rows)


def load(final: bool = False) -> pd.DataFrame:
    ev = pd.read_parquet(EVENTS)
    ev = ev[ev.skip.isna() & (ev.prev_close >= 5) & (ev.turnover_20d >= 50_000)]
    d = ev.merge(director_buys(), on=["tidm", "day"])
    if not final:
        d = d[d.day < pd.Timestamp("2022-01-01")]
    d = d[["tidm", "day", "buy_k", "n_buy_ann", "turnover_20d", "is_aim", "prev_close"]] \
        .merge(outcomes(d), on=["tidm", "day"], how="left")
    for x in list(HOLDS) + ["t60"]:
        d[f"y_{x}"] = d[x] - d[f"b_{x}"]
    d["year"] = d.day.dt.year
    d["trade_date"] = d.day
    d["cost"] = round_trip(d)
    return d.reset_index(drop=True)


def evaluate(d: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    rows = []
    for Y in years:
        base = d[d.year == Y]
        for sname, s in (("S0_all", base), ("S1_20k", base[base.buy_k >= MIN_BUY_K])):
            for x in list(HOLDS) + ["t60"]:
                s2 = s[s[f"y_{x}"].notna()]
                acct = ua.simulate(s2.assign(exit_date=s2.trade_date + pd.to_timedelta(np.ceil(s2[f"{x}_days"] * 7 / 5), unit="D"),
                                             pred=s2.buy_k))
                k = acct[acct.taken]
                rows.append(dict(year=Y, select=sname, exit=x, events=len(s2), taken=len(k),
                                 gross_vs_ftas=s2[f"y_{x}"].mean(), cost=k.cost.mean(),
                                 vs_ftas_gbp=float(((k[f"y_{x}"] - k.cost) * SIZE_GBP).sum()),
                                 actual_gbp=float(((k[x] - k.cost) * SIZE_GBP).sum())))
    return pd.DataFrame(rows)


def run(stage: str, final: bool = False) -> str:
    if stage == "final" and not final:
        raise SystemExit("the test years are run once, after the user's OK: add --final")
    years = CHOOSE if stage == "choose" else FINAL
    d = load(final=(stage == "final"))
    log.info("director-buy events usable: %d (%s)", len(d), d.year.value_counts().sort_index().to_dict())
    res = evaluate(d, years)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT / f"uk_directors_{stage}.parquet")
    pool = res.groupby(["select", "exit"]).agg(events=("events", "sum"), taken=("taken", "sum"),
                                               gross_vs_ftas=("gross_vs_ftas", "mean"), cost=("cost", "mean"),
                                               vs_ftas_gbp=("vs_ftas_gbp", "sum"), actual_gbp=("actual_gbp", "sum"),
                                               years_pos=("vs_ftas_gbp", lambda s: int((s > 0).sum())))
    by_year = res.pivot_table(index=["select", "exit"], columns="year", values="vs_ftas_gbp")
    return "\n".join([f"# UK director buying ({stage})",
                      pool.assign(gross_vs_ftas=pool.gross_vs_ftas * 100, cost=pool.cost * 100).round(2).to_string(),
                      "", "£ vs FTSE All-Share (with dividends) by year:", by_year.round(0).to_string()])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["choose", "final"])
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    print(run(a.stage, a.final))


if __name__ == "__main__":
    main()
