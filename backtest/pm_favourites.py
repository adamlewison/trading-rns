"""Prediction-market near-certain contracts on Kalshi history (backtest/DESIGN_PM_FAVOURITES.md).

    uv run python -W ignore -m backtest.pm_favourites check            # step 0: counts and the scheduled-end check
    uv run python -W ignore -m backtest.pm_favourites choose           # scheduled end up to 2025-12-31
    uv run python -W ignore -m backtest.pm_favourites final --final    # 2026 -> latest, ONCE, after the user agrees

Data: `ingest.pm.kalshi series|markets|candles` (data/pm/kalshi/). ⚠️ Not tradable from the UK today.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path("data/pm/kalshi")
CHOOSE_END = "2025-12-31 23:59:59"
BANDS = {"B95": (95, 98), "B90": (90, 94)}       # ask in whole cents, inclusive
WINDOWS = {"W2": 2, "W7": 7}                     # scheduled end is 6 hours to N days away
MIN_LEAD_S = 6 * 3600
MIN_VOLUME = 1000.0
FEE = 0.07
START_CASH, STAKE_FRAC, MAX_OPEN, SERIES_DAY_CAP = 6000.0, 0.02, 50, 0.10
NOMINAL_STAKE = START_CASH * STAKE_FRAC          # for per-bet returns (fee rounding)


def fee(contracts: int, price: float, mult: float = 1.0) -> float:
    """Kalshi taker fee for one order, rounded up to the cent."""
    return math.ceil(FEE * mult * contracts * price * (1 - price) * 100 - 1e-9) / 100


def bet_return(stake: float, price: float, payoff: float, mult: float = 1.0) -> float:
    c = math.floor(stake / price + 1e-9)
    if c < 1:
        return float("nan")
    return (c * payoff - fee(c, price, mult) - c * price) / (c * price)


def connect(final: bool) -> duckdb.DuckDBPyConnection:
    """Loads markets of ONE period only, so the other period's outcomes are never in memory."""
    con = duckdb.connect()
    series = pd.DataFrame(json.loads((ROOT / "series.json").read_text()))[["series", "category", "fee_multiplier"]]
    con.register("series_df", series)
    op = ">" if final else "<="
    con.execute(f"""CREATE TABLE m AS
        WITH raw AS (SELECT DISTINCT ON (ticker) * FROM
            read_json_auto('{ROOT}/markets/*.jsonl.gz', union_by_name=true, sample_size=-1)),
        x AS (SELECT r.*, epoch(CAST(coalesce(expected_expiration_time, latest_expiration_time) AS TIMESTAMPTZ)) AS e
              FROM raw r)
        SELECT x.*, s.category, coalesce(s.fee_multiplier, 1.0) AS mult FROM x LEFT JOIN series_df s USING (series)
        WHERE market_type = 'binary' AND CAST(volume_fp AS DOUBLE) >= {MIN_VOLUME}
          AND result IS NOT NULL AND result <> '' AND e IS NOT NULL
          AND to_timestamp(e) {op} TIMESTAMPTZ '{CHOOSE_END}+00'""")
    con.execute(f"""CREATE TABLE c AS SELECT DISTINCT ON (ticker, t) c.* FROM
        read_json_auto('{ROOT}/candles/*.jsonl.gz', union_by_name=true) c SEMI JOIN m USING (ticker)""")
    return con


def signals(con, band: tuple[int, int], window_days: int) -> pd.DataFrame:
    """First candle in the window with exactly one side's ask in the band; fill at the next candle's open."""
    lo, hi = band
    return con.execute(f"""
        WITH k AS (
            SELECT c.ticker, c.t, m.e, c.last,
                   CAST(round(c.ask_c * 100) AS INT) AS ya, CAST(round((1 - c.bid_c) * 100) AS INT) AS na,
                   lead(c.t) OVER w AS t2, lead(c.ask_o) OVER w AS ask_o2, lead(c.bid_o) OVER w AS bid_o2
            FROM c JOIN m USING (ticker) WHERE c.ask_c IS NOT NULL AND c.bid_c IS NOT NULL
            WINDOW w AS (PARTITION BY c.ticker ORDER BY c.t)),
        s AS (
            SELECT *, (ya BETWEEN {lo} AND {hi}) AS y_in, (na BETWEEN {lo} AND {hi}) AS n_in,
                   row_number() OVER (PARTITION BY ticker ORDER BY t) AS rn
            FROM k WHERE t BETWEEN e - {window_days} * 86400 AND e - {MIN_LEAD_S}
              AND ((ya BETWEEN {lo} AND {hi}) OR (na BETWEEN {lo} AND {hi})))
        SELECT s.ticker, s.t AS t_signal, s.t2 AS t_fill, s.e, y_in AND n_in AS both,
               CASE WHEN y_in THEN 'yes' ELSE 'no' END AS side,
               CASE WHEN y_in THEN ya ELSE na END / 100.0 AS p_signal,
               CASE WHEN y_in THEN ask_o2 ELSE 1 - bid_o2 END AS p_fill,
               CASE WHEN y_in THEN 1 - bid_o2 ELSE ask_o2 END AS p_other,
               CASE WHEN y_in THEN s.last ELSE 1 - s.last END AS last_side,
               m.event_ticker, m.series, m.category, m.mult, m.result, m.settlement_value_dollars,
               CAST(m.volume_fp AS DOUBLE) AS volume,
               epoch(CAST(coalesce(m.settlement_ts, m.close_time) AS TIMESTAMPTZ)) AS t_exit
        FROM s JOIN m USING (ticker) WHERE rn = 1""").df()


def make_bets(sig: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    n = {"signals": len(sig), "both_sides_in_band": int(sig.both.sum())}
    s = sig[~sig.both].copy()
    ok = s.t_fill.notna() & (s.t_fill <= s.e) & s.p_fill.notna() & (s.p_fill > 0) & (s.p_fill < 1.0) \
        & (s.p_fill <= s.p_signal + 0.01 + 1e-9)
    n["no_fill"] = int((~ok).sum())
    s = s[ok].sort_values(["t_signal", "volume"], ascending=[True, False]).drop_duplicates("event_ticker")
    n["bets"] = len(s)
    yes_pay = np.where(s.result == "yes", 1.0, np.where(s.result == "no", 0.0,
                       pd.to_numeric(s.settlement_value_dollars, errors="coerce")))
    n["not_yes_or_no"] = int((~s.result.isin(["yes", "no"])).sum())
    s["payoff"] = np.where(s.side == "yes", yes_pay, 1 - yes_pay)
    s["ret"] = [bet_return(NOMINAL_STAKE, p, w, m) for p, w, m in zip(s.p_fill, s.payoff, s.mult)]
    valid_other = s.p_other.notna() & (s.p_other > 0) & (s.p_other < 1)
    s["mirror"] = [bet_return(NOMINAL_STAKE, p, 1 - w, m) if v else np.nan
                   for p, w, m, v in zip(s.p_other, s.payoff, s.mult, valid_other)]
    s["t_entry"] = s.t_fill - 3600
    s["entry"] = pd.to_datetime(s.t_entry, unit="s")
    s["exit"] = pd.to_datetime(s.t_exit.fillna(s.e), unit="s")
    s["days_held"] = (s.exit - s.entry).dt.total_seconds() / 86400
    s["days_to_end"] = (s.e - s.t_signal) / 86400
    s["decided"] = s.last_side >= 0.99
    s["cluster"] = s.series + "|" + s.entry.dt.strftime("%Y-%m-%d")
    return s.dropna(subset=["ret"]).reset_index(drop=True), n


def tstat(r: pd.Series, cluster: pd.Series | None = None) -> float:
    """Runbook section 6: se = sqrt(sum over clusters of (sum of (r - m))^2) / N."""
    r = r.dropna()
    if len(r) < 2:
        return float("nan")
    d = r - r.mean()
    g = d.groupby(cluster.loc[r.index]).sum() if cluster is not None else d
    se = math.sqrt(float((g ** 2).sum())) / len(r)
    return float(r.mean() / se) if se > 0 else float("nan")


def account(b: pd.DataFrame) -> pd.DataFrame:
    """$6,000, 2% of current equity per bet, <= 50 open, <= 10% of equity per series per day."""
    cash, open_, day_series, rows = START_CASH, [], {}, []
    for r in b.sort_values("t_entry").itertuples():
        for o in [o for o in open_ if o[0] <= r.t_entry]:
            cash += o[1]
            open_.remove(o)
        equity = cash + sum(o[2] for o in open_)
        stake = STAKE_FRAC * equity
        key = (r.series, r.entry.date())
        if len(open_) >= MAX_OPEN or stake > cash or day_series.get(key, 0.0) + stake > SERIES_DAY_CAP * equity:
            continue
        c = math.floor(stake / r.p_fill + 1e-9)
        if c < 1:
            continue
        cost = c * r.p_fill + fee(c, r.p_fill, r.mult)
        cash -= cost
        open_.append((r.t_exit if not np.isnan(r.t_exit) else r.e, c * r.payoff, cost))
        day_series[key] = day_series.get(key, 0.0) + stake
        rows.append({"time": r.entry, "equity": equity, "taken": 1})
    for o in open_:
        cash += o[1]
    out = pd.DataFrame(rows)
    out.attrs["final_equity"] = cash
    return out


def spy_over(start: pd.Timestamp, end: pd.Timestamp) -> float:
    p = Path("data/us_min/us_etf_daily.parquet")
    q = f"SELECT date, adj_close FROM '{p}' WHERE ticker='SPY' ORDER BY 1"
    s = duckdb.sql(q).df().set_index("date").adj_close
    s.index = pd.to_datetime(s.index)
    s = s.reindex(pd.date_range(s.index[0], max(s.index[-1], end), freq="D")).ffill()
    return float(s[end.normalize()] / s[start.normalize()] - 1)


def line(b: pd.DataFrame) -> dict:
    if b.empty:
        return {"bets": 0}
    return {"bets": len(b), "clusters": b.cluster.nunique(), "avg_price": b.p_fill.mean(), "win": (b.payoff == 1).mean(),
            "lost_all": int((b.payoff == 0).sum()), "mean_ret": b.ret.mean(), "median_ret": b.ret.median(),
            "t_event": tstat(b.ret), "t_series_day": tstat(b.ret, b.cluster), "days_held": b.days_held.mean(),
            "mirror": b.mirror.mean()}


def report(final: bool) -> None:
    pd.set_option("display.width", 250, "display.max_rows", 300, "display.float_format", lambda x: f"{x:,.4f}")
    con = connect(final)
    print("⚠️ Kalshi is closed to UK residents: whatever the result, this is evidence, not a tradable strategy.")
    print(con.execute("SELECT count(*) markets, count(DISTINCT event_ticker) events, min(to_timestamp(e)) first_end, "
                      "max(to_timestamp(e)) last_end FROM m").df().to_string(index=False))
    print(con.execute("SELECT count(DISTINCT ticker) markets_with_candles, count(*) candles FROM c").df().to_string(index=False))
    summary = []
    for bn, band in BANDS.items():
        for wn, w in WINDOWS.items():
            v = f"{bn}-{wn}"
            b, n = make_bets(signals(con, band, w))
            if b.empty:
                print(v, n)
                continue
            half = b.entry.median()
            acc = account(b)
            fe = acc.attrs["final_equity"]
            spy = spy_over(b.entry.min(), b.exit.max())
            eq = acc.equity if len(acc) else pd.Series([START_CASH])
            summary.append({"variant": v, **line(b), "ret_h1": b.ret[b.entry <= half].mean(),
                            "ret_h2": b.ret[b.entry > half].mean(), "acct_bets": len(acc),
                            "acct_ret": fe / START_CASH - 1, "spy_ret": spy, "acct_dd": (eq / eq.cummax() - 1).min(),
                            **{f"n_{k}": x for k, x in n.items()}})
            print(f"\n== {v}: breakdowns (information only) ==")
            for name, key in (("category", b.category.fillna("?")), ("year", b.entry.dt.year),
                              ("days to end", pd.cut(b.days_to_end, [0, 1, 2, 4, 7.01])),
                              ("already decided (last trade >= 0.99)", b.decided),
                              ("sports", b.category.eq("Sports"))):
                t = pd.DataFrame({k: line(g) for k, g in b.groupby(key, observed=True)}).T
                print(f"-- by {name}")
                print(t[["bets", "clusters", "avg_price", "win", "lost_all", "mean_ret", "t_series_day", "mirror"]].to_string())
            if v == "B95-W2":
                cols = ["ticker", "side", "entry", "p_signal", "p_fill", "result", "payoff", "ret", "exit", "volume"]
                print("-- 5 worst and 5 best bets")
                print(pd.concat([b.sort_values("ret").head(5), b.sort_values("ret").tail(5)])[cols].to_string(index=False))
    print("\n== variants ==")
    print(pd.DataFrame(summary).set_index("variant").T.to_string())


def check() -> None:
    con = duckdb.connect()
    con.execute(f"""CREATE TABLE m AS SELECT DISTINCT ON (ticker) * FROM
        read_json_auto('{ROOT}/markets/*.jsonl.gz', union_by_name=true, sample_size=-1)""")
    print(con.execute(f"""SELECT count(*) markets, count(DISTINCT series) series,
        count(*) FILTER (WHERE market_type='binary' AND CAST(volume_fp AS DOUBLE) >= {MIN_VOLUME}) eligible,
        min(close_time) first_close, max(close_time) last_close FROM m""").df().to_string(index=False))
    print("scheduled-end check: early-closing markets whose actual close is > 1 day before the scheduled end")
    print(con.execute("""SELECT count(*) FILTER (WHERE can_close_early) can_close_early,
        count(*) FILTER (WHERE can_close_early AND CAST(close_time AS TIMESTAMPTZ) <
            CAST(coalesce(expected_expiration_time, latest_expiration_time) AS TIMESTAMPTZ) - INTERVAL 1 DAY) closed_over_a_day_early,
        count(*) FILTER (WHERE coalesce(expected_expiration_time, latest_expiration_time) IS NULL) no_scheduled_end
        FROM m WHERE market_type='binary'""").df().to_string(index=False))
    print(con.execute("""SELECT year(CAST(close_time AS TIMESTAMPTZ)) y, count(*) markets,
        count(*) FILTER (WHERE CAST(volume_fp AS DOUBLE) >= 1000) eligible FROM m GROUP BY 1 ORDER BY 1""").df().to_string(index=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        check()
    elif cmd == "final":
        if "--final" not in sys.argv:
            raise SystemExit("the final test runs once, with --final, after the user agrees")
        report(final=True)
    else:
        report(final=False)
