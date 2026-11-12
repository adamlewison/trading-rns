"""UK pre-open RNS events (design: backtest/DESIGN_UK_NEWS.md).

    uv run python -W ignore -m features.uk_news build   # -> data/features/uk_news_events.parquet

One row per company per day with pre-open announcements. Trade at that day's open. Outcomes are
returns from the open (o1, h3, h5, h20, a5) and the benchmark's over the same days (^FTAI for AIM,
^FTSC otherwise). Windows touching a stale/suspect bar get no outcome."""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from core.config import ROOT
from core.log import get_logger
from features.us_earnings import bracket_exit
from ingest.uk.common import session

log = get_logger("features")
OUT = ROOT / "data" / "features" / "uk_news_events.parquet"
CATS = ["results", "trading_update", "contract_win", "fundraise", "director_dealing", "acquisition_disposal",
        "board_change", "operational", "dividend", "takeover", "profit_warning", "other"]
HOLDS = {"o1": 1, "h3": 3, "h5": 5, "h20": 20}
A_UP, A_DN, A_DAYS = 2.0, 1.0, 5


class UKPrices:
    def __init__(self, con, tidms: list[str]):
        con.register("want", pd.DataFrame({"tidm": tidms}))
        df = con.execute("""SELECT p.tidm, p.date, p.open, p.high, p.low, p.close, p.volume, p.is_stale, p.is_suspect
                            FROM prices_daily p SEMI JOIN want USING (tidm) ORDER BY p.tidm, p.date""").df()
        self.cols = {k: df[k].to_numpy(float) for k in ["open", "high", "low", "close", "volume"]}
        self.bad = (df.is_stale | df.is_suspect).to_numpy()
        self.dates = df.date.to_numpy().astype("datetime64[D]")
        t = df.tidm.to_numpy()
        s = np.flatnonzero(np.r_[True, t[1:] != t[:-1]])
        e = np.r_[s[1:], len(t)]
        self.span = {t[a]: (a, b) for a, b in zip(s, e)}

    def locate(self, tidm, day):
        if tidm not in self.span:
            return None
        s, e = self.span[tidm]
        d = np.datetime64(pd.Timestamp(day).date(), "D")
        i = s + np.searchsorted(self.dates[s:e], d)
        return int(i) if i < e and self.dates[i] == d else None


def events(con) -> pd.DataFrame:
    flags = ", ".join(f"max((headline_category = '{c}')::INT) AS cat_{c}" for c in CATS)
    return con.execute(f"""
        SELECT tidm, CAST(published_uk AS DATE) AS day, count(*) AS n_ann, sum(coalesce(body_chars, 0)) AS body_chars,
               any_value(universe_market) AS market, {flags}
        FROM announcements
        WHERE pre_open AND is_regulatory AND NOT is_admin AND NOT coalesce(is_replacement, false)
          AND dayofweek(published_uk) BETWEEN 1 AND 5
        GROUP BY 1, 2""").df()


def build() -> pd.DataFrame:
    with session(read_only=True) as con:
        ev = events(con)
        ev["day"] = pd.to_datetime(ev.day)
        svc = con.execute("""SELECT tidm, arg_max(trading_service, snapshot_date) AS service
                             FROM uk_trading_service GROUP BY 1""").df()
        con.register("e", ev[["tidm", "day"]])
        past = con.execute("""SELECT e.tidm, e.day, count(*) AS n_ann_90d FROM e JOIN announcements a
                              ON a.tidm = e.tidm AND CAST(a.published_uk AS DATE) < e.day
                             AND CAST(a.published_uk AS DATE) >= e.day - INTERVAL 90 DAY AND NOT a.is_admin
                              GROUP BY 1, 2""").df()
        past["day"] = pd.to_datetime(past.day)
        P = UKPrices(con, sorted(set(ev.tidm)) + ["^FTAI", "^FTSC", "^FTAS"])
    ev = ev.merge(svc, on="tidm", how="left").merge(past, on=["tidm", "day"], how="left")
    ev["n_ann_90d"] = ev.n_ann_90d.fillna(0)
    o, h, l, c, v = (P.cols[k] for k in ["open", "high", "low", "close", "volume"])
    rows = []
    for r in ev.itertuples():
        row = {"tidm": r.tidm, "day": r.day}
        i = P.locate(r.tidm, r.day)
        if i is None:
            row["skip"] = "no_price"; rows.append(row); continue
        s, e = P.span[r.tidm]
        if i - s < 61 or P.bad[i - 60:i + 1].any() or o[i] <= 0:
            row["skip"] = "history"; rows.append(row); continue
        row.update(prev_close=c[i - 1], entry_open=o[i], ret_5d=c[i - 1] / c[i - 6] - 1, ret_20d=c[i - 1] / c[i - 21] - 1,
                   ret_60d=c[i - 1] / c[i - 61] - 1, vol_20d=float(np.std(np.diff(np.log(c[i - 21:i])))),
                   turnover_20d=float(np.mean(c[i - 20:i] * v[i - 20:i])) / 100,       # pence -> pounds
                   off_high_52w=c[i - 1] / c[max(s, i - 252):i].max() - 1, off_low_52w=c[i - 1] / c[max(s, i - 252):i].min() - 1,
                   gap=o[i] / c[i - 1] - 1)
        tr = np.maximum(h[i - 20:i], c[i - 21:i - 1]) - np.minimum(l[i - 20:i], c[i - 21:i - 1])
        atr = float(np.mean(tr))
        bsym = "^FTAI" if r.market == "AIM" else "^FTSC"
        b = P.locate(bsym, r.day)
        for name, n in HOLDS.items():
            if i + n - 1 < e and not P.bad[i:i + n].any():
                row[name] = c[i + n - 1] / o[i] - 1
                if b is not None and b + n - 1 < P.span[bsym][1]:
                    row[f"b_{name}"] = c[b + n - 1] / o[b] - 1
        if i + A_DAYS - 1 < e and not P.bad[i:i + A_DAYS].any() and atr > 0:
            w = slice(i, i + A_DAYS)
            rr, dd = bracket_exit(o[i], o[w], h[w], l[w], c[w], A_UP * atr / o[i], -A_DN * atr / o[i], A_DAYS)
            row["a5"], row["a5_days"] = rr, dd
            if b is not None:
                row["b_a5"] = c[b + dd - 1] / o[b] - 1
        rows.append(row)
    ev = ev.merge(pd.DataFrame(rows), on=["tidm", "day"], how="left")
    for x in list(HOLDS) + ["a5"]:
        ev[f"y_{x}"] = ev[x] - ev[f"b_{x}"]
    ev["is_aim"] = (ev.market == "AIM").astype(int)
    ev["is_sets"] = (ev.service == "SETS").astype(int)
    ev["log_turnover"] = np.log(ev.turnover_20d.where(ev.turnover_20d > 0))
    ev["weekday"] = ev.day.dt.weekday
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ev.replace([np.inf, -np.inf], np.nan).to_parquet(OUT)
    log.info("UK news events: %d; priced %d; with 5-day outcome %d -> %s", len(ev), int(ev.skip.isna().sum()),
             int(ev.y_h5.notna().sum()), OUT)
    return ev


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.parse_args()
    build()


if __name__ == "__main__":
    main()
