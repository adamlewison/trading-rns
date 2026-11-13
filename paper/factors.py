"""Paper trading: the four monthly factor portfolios (backtest/DESIGN_FACTORS.md), frozen rules.

    uv run python -W ignore -m paper.factors daily          # the droplet timer runs this every weekday evening
    uv run python -W ignore -m paper.factors status         # print the record so far

On the first trading day of each month (after the US close) it:
  1. refreshes the ticker map, company facts and the prices of the liquid universe + SPY;
  2. scores the 500 largest companies at the previous month's last trading day (data up to that close);
  3. records the top 30 for each of V, Q, M, QVM, entered at today's close (adj_close);
  4. marks every earlier month: its picks' return from its entry to this entry, minus trading costs,
     against SPY over the same days.
Picks and marks go to us.duckdb (`paper_picks`, `paper_marks`, backed up) and a readable summary to B2
(`trading-rns/paper/status.md`). Nothing is traded: this only records what the rules would have done."""
from __future__ import annotations

import argparse
from datetime import date, datetime

import numpy as np
import pandas as pd

from backtest import factors as fb
from backtest.us_insiders import spread_table
from core.backup import get_store
from core.log import get_logger
from ingest.us.common import session

log = get_logger("paper")
STATUS_KEY = "trading-rns/paper/status.md"
SINCE_DAYS = 420                       # price history needed for 12-month momentum + 20-day volume


def trading_days(con) -> pd.DatetimeIndex:
    d = con.execute("SELECT date FROM us_prices_daily WHERE ticker='SPY' AND NOT is_stale ORDER BY 1").df()
    return pd.DatetimeIndex(pd.to_datetime(d.date))


def is_first_trading_day_of_month(cal: pd.DatetimeIndex, day: pd.Timestamp) -> bool:
    return day in cal and cal.get_loc(day) > 0 and cal[cal.get_loc(day) - 1].month != day.month


def refresh_data() -> None:
    from ingest.us.facts import facts
    from ingest.us.filings import tickers
    from ingest.us.prices import prices
    tickers()
    facts(refresh=True)
    with session(read_only=True) as con:
        liquid = [r[0] for r in con.execute("""
            SELECT ticker FROM us_prices_daily WHERE date >= current_date - INTERVAL 40 DAY
            GROUP BY 1 HAVING avg(close * volume) >= 10e6""").fetchall()]
        held = [r[0] for r in con.execute("SELECT DISTINCT ticker FROM paper_picks").fetchall()]
    prices(refresh=True, only=sorted(set(liquid) | set(held)))


def rebalance(today: pd.Timestamp, cal: pd.DatetimeIndex) -> None:
    asof = cal[cal.get_loc(today) - 1]                         # the previous month's last trading day
    since = (asof - pd.Timedelta(days=SINCE_DAYS)).date().isoformat()
    u = fb.build_scores(since=since, memory="600MB")
    u = u[u.date == asof]
    if len(u) < 300:
        raise SystemExit(f"only {len(u)} stocks scored at {asof.date()}: data refresh incomplete, not recording")
    with session(memory_limit="300MB") as con:
        px = con.execute("SELECT ticker, adj_close FROM us_prices_daily WHERE date = ?", [today.date()]).df() \
            .set_index("ticker").adj_close
        rows = []
        for v in fb.VARIANTS:
            sel = u.nlargest(fb.N_HOLD, v)
            for k, r in enumerate(sel.itertuples(), 1):
                rows.append((v, asof.date(), today.date(), k, r.ticker, float(getattr(r, v)),
                             float(px.get(r.ticker, np.nan)), float(r.dvol20), datetime.now()))
        con.execute("DELETE FROM paper_picks WHERE entry_date = ?", [today.date()])
        con.executemany("INSERT INTO paper_picks VALUES (?,?,?,?,?,?,?,?,?)", rows)
    log.info("paper: recorded %d picks for %s (scored at %s)", len(rows), today.date(), asof.date())


def mark() -> pd.DataFrame:
    """Each month's portfolio from its entry to the next entry (or the latest close), net of costs."""
    with session(memory_limit="300MB") as con:
        picks = con.execute("SELECT * FROM paper_picks").df()
        if picks.empty:
            return picks
        latest = con.execute("SELECT max(date) FROM us_prices_daily WHERE ticker='SPY'").fetchone()[0]
        entries = sorted(picks.entry_date.unique())
        rows = []
        for i, d0 in enumerate(entries):
            d1 = entries[i + 1] if i + 1 < len(entries) else latest
            if d1 == d0:
                continue
            # prices on the entry day and the latest one up to d1, both from today's (re-adjusted) history,
            # so dividends paid in between are counted
            def at(day):
                return con.execute("""SELECT ticker, arg_max(adj_close, date) AS px FROM us_prices_daily
                                      WHERE date <= ? AND date >= ? - INTERVAL 10 DAY GROUP BY 1""",
                                   [day, day]).df().set_index("ticker").px
            p0, px = at(d0), at(d1)
            spy0, spy1 = p0.get("SPY"), px.get("SPY")
            for v in fb.VARIANTS:
                p = picks[(picks.entry_date == d0) & (picks.variant == v)]
                prev = picks[(picks.entry_date == entries[i - 1]) & (picks.variant == v)].ticker if i else pd.Series(dtype=str)
                r = px.reindex(p.ticker).to_numpy() / p0.reindex(p.ticker).fillna(pd.Series(p.entry_adj.to_numpy(), index=p.ticker)).to_numpy() - 1
                side = fb.COST_SIDE + spread_table(p.dvol20.to_numpy()) / 2
                new = ~p.ticker.isin(set(prev))
                cost = (side * new.to_numpy()).sum() / fb.N_HOLD + (~prev.isin(set(p.ticker))).sum() * side.mean() / fb.N_HOLD
                rows.append(dict(variant=v, entry=d0, until=d1, ret=float(np.nanmean(r)) - cost,
                                 spy=spy1 / spy0 - 1, open=d1 == latest and i + 1 == len(entries)))
        m = pd.DataFrame(rows)
        con.execute("DELETE FROM paper_marks")
        con.register("m", m)
        con.execute("INSERT INTO paper_marks SELECT variant, entry, until, ret, spy, open, now() FROM m")
    return m


def status_text(m: pd.DataFrame) -> str:
    if m.empty:
        return "# Paper trading: factor portfolios\n\nNo picks recorded yet (the first is at the next month start).\n"
    lines = ["# Paper trading: factor portfolios (started " + str(m.entry.min()) + ")", "",
             "Since start, after costs (the latest month is still open):", "",
             "| Portfolio | Return | SPY | Ahead by | Months |", "|---|---|---|---|---|"]
    for v, g in m.groupby("variant"):
        r, s = (1 + g.ret).prod() - 1, (1 + g.spy).prod() - 1
        lines.append(f"| {v} | {r:+.1%} | {s:+.1%} | {r - s:+.1%} | {len(g)} |")
    lines += ["", f"Updated {datetime.now():%Y-%m-%d %H:%M} UTC. Rules: backtest/DESIGN_FACTORS.md; nothing is traded."]
    return "\n".join(lines) + "\n"


def daily(force: bool = False) -> None:
    today = pd.Timestamp(date.today())
    with session(read_only=True) as con:
        cal = trading_days(con)
    if force or is_first_trading_day_of_month(cal, today):
        refresh_data()
        with session(read_only=True) as con:
            cal = trading_days(con)
        if today in cal:
            rebalance(today, cal)
        else:
            log.warning("paper: no SPY bar for %s yet; not recording", today.date())
    m = mark()
    get_store().put(STATUS_KEY, status_text(m).encode())
    log.info("paper: status updated")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily", "status"])
    ap.add_argument("--force", action="store_true", help="rebalance today even if it isn't a month start")
    a = ap.parse_args()
    if a.cmd == "daily":
        daily(a.force)
    else:
        print(status_text(mark()))


if __name__ == "__main__":
    main()
