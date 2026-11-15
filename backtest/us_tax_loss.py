"""US year-end tax-loss rebound (backtest/DESIGN_TAX_LOSS.md). One basket per December.

    uv run python -W ignore -m backtest.us_tax_loss choose           # Decembers 2012-2020
    uv run python -W ignore -m backtest.us_tax_loss final --final    # Decembers 2021-2025, ONCE, after the user agrees

Needs data/us_min/{us_prices_daily,us_splits,us_etf_daily,sec_ticker_map}.parquet (B2 `trading-rns/us/v1/db/`)
or the same tables in data/us.duckdb exported there.
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

D = Path("data/us_min")
CHOOSE_YEARS = range(2012, 2021)          # December of these years
FINAL_YEARS = range(2021, 2026)
N = 30                                    # basket size
STAKE = 150.0
MIN_PRICE = 2.0                           # actual dollars on the selection day
DVOL_MIN, DVOL_MAX = 2e6, 100e6           # 20-day average dollar volume
EXITS = (10, 20)                          # trading day of January
X80_FLOOR = -0.80                         # X80 drops stocks that fell more than this
FX, SLIP = 0.0015, 0.0015                 # Trading 212, each side
# Change 2026-11-15 (before any result): the price table has no security type, so funds and
# exchange-traded products are removed by name. Realty/bank "trusts" are lost with them.
NOT_COMMON = r"\b(ETF|ETN|ETNS|FUND|FUNDS|TRUST|SHARES|PROSHARES|DIREXION|ISHARES|PORTFOLIO|INDEX)\b"


def spread(dvol: np.ndarray) -> np.ndarray:
    """Full spread by 20-day dollar volume (CLAUDE.md section 8; $2-20m: 0.60% per the design)."""
    return np.select([dvol >= 5e8, dvol >= 1e8, dvol >= 2e7], [0.0005, 0.0010, 0.0020], 0.0060)


def round_trip(dvol: np.ndarray) -> np.ndarray:
    return 2 * (FX + SLIP) + spread(dvol)


def pick(u: pd.DataFrame, variant: str) -> pd.DataFrame:
    """u: one row per eligible stock with `ytd`. Worst N; X80 first drops falls of more than 80%."""
    if variant == "X80":
        u = u[u.ytd >= X80_FLOOR]
    return u.sort_values(["ytd", "ticker"]).head(N)


def calendar(con) -> pd.DatetimeIndex:
    d = con.execute(f"SELECT date FROM '{D}/us_etf_daily.parquet' WHERE ticker='SPY' AND date>='2012-01-01' ORDER BY 1").df()
    return pd.DatetimeIndex(d.date)


def year_dates(cal: pd.DatetimeIndex, y: int) -> dict:
    dec = cal[(cal.year == y) & (cal.month == 12)]
    nxt = cal[cal.year == y + 1]            # counted from the first trading day of January; a January
    yr = cal[cal.year == y]                 # with 19 trading days (2021) runs one day into February
    return {"first": yr[0], "S": dec[-6], "entry": dec[-5], **{f"exit{k}": nxt[k - 1] for k in EXITS}}


def etf_ret(con, t: str, entry, exit_) -> float:
    r = con.execute(f"""SELECT date, open*adj_close/close o, adj_close c FROM '{D}/us_etf_daily.parquet'
        WHERE ticker='{t}' AND date IN ('{entry.date()}','{exit_.date()}') ORDER BY 1""").df()
    return float(r.c.iloc[-1] / r.o.iloc[0] - 1)


def one_year(con, cal, y: int) -> tuple[pd.DataFrame, dict]:
    dt = year_dates(cal, y)
    S, first, entry = dt["S"], dt["first"], dt["entry"]
    last_exit = dt[f"exit{max(EXITS)}"]
    win20 = cal[cal <= S][-20:]
    px = con.execute(f"""SELECT p.* FROM '{D}/us_prices_daily.parquet' p
        WHERE date='{first.date()}' OR (date>='{win20[0].date()}' AND date<='{last_exit.date()}')""").df()
    px["date"] = pd.to_datetime(px.date)
    px["clean"] = ~(px.is_stale.fillna(False) | px.is_suspect.fillna(False))
    names = con.execute(f"""SELECT ticker, any_value(name) name FROM '{D}/sec_ticker_map.parquet'
        GROUP BY 1""").df()
    funds = set(names[names.name.str.upper().str.contains(NOT_COMMON, regex=True, na=False)].ticker)
    spl = con.execute(f"""SELECT ticker, exp(sum(ln(ratio))) later FROM '{D}/us_splits.parquet'
        WHERE date>'{S.date()}' AND ratio>0 GROUP BY 1""").df().set_index("ticker").later

    f = px[(px.date == first) & px.clean].set_index("ticker").adj_close.rename("adj_first")
    s = px[(px.date == S) & px.clean].set_index("ticker")[["close", "adj_close"]]
    w = px[px.date.isin(win20)]
    dvol = (w.close * w.volume).groupby(w.ticker).mean().rename("dvol")
    u = s.join(f, how="inner").join(dvol)
    u["actual"] = u.close * spl.reindex(u.index).fillna(1.0)
    u["ytd"] = u.adj_close / u.adj_first - 1
    n_all = len(u)
    u = u[(u.actual >= MIN_PRICE) & u.dvol.between(DVOL_MIN, DVOL_MAX) & ~u.index.isin(funds)
          & ~u.index.isin(["SPY", "IWM"])].reset_index()
    info = {"year": y, "priced": n_all, "universe": len(u), "S": S.date(), "entry": entry.date()}

    e = px[px.date == entry].set_index("ticker")
    after = px[px.date > entry]
    rows = []
    for variant in ("W30", "X80"):
        b = pick(u, variant)
        for k in EXITS:
            ex = dt[f"exit{k}"]
            for r in b.itertuples():
                row = {"year": y, "variant": f"{variant}-{k}", "ticker": r.ticker, "ytd": r.ytd, "dvol": r.dvol,
                       "entry": entry.date(), "exit": ex.date(), "status": "ok"}
                if r.ticker not in e.index or not e.clean[r.ticker] or not e.open[r.ticker] > 0:
                    rows.append({**row, "status": "no_entry_price"})      # skipped and counted
                    continue
                ent = e.open[r.ticker] * e.adj_close[r.ticker] / e.close[r.ticker]
                a = after[(after.ticker == r.ticker) & (after.date <= ex)]
                if a.empty:
                    gross, row["status"] = -1.0, "stopped_trading"
                else:
                    last = a[a.clean].iloc[-1] if a.clean.any() else a.iloc[-1]
                    gross = last.adj_close / ent - 1
                    if last.date != ex:
                        row["status"] = "exit_carried"
                cost = float(round_trip(np.array([r.dvol]))[0])
                rows.append({**row, "entry_px": e.open[r.ticker], "gross": gross, "cost": cost, "net": gross - cost})
    for k in EXITS:
        ex = dt[f"exit{k}"]
        info[f"spy{k}"] = etf_ret(con, "SPY", entry, ex)
        info[f"iwm{k}"] = etf_ret(con, "IWM", entry, ex)
    # benchmark check: the whole filtered universe, equal weight, entry open -> exit-10 close
    ex = dt["exit10"]
    c = px[(px.date == ex) & px.clean].set_index("ticker").adj_close
    eo = (e.open * e.adj_close / e.close)[e.clean]
    both = u.set_index("ticker").index.intersection(c.index).intersection(eo.index)
    info["universe_ret10"] = float((c[both] / eo[both] - 1).mean())
    return pd.DataFrame(rows), info


def run(years) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = duckdb.connect()
    cal = calendar(con)
    trades, infos = [], []
    for y in years:
        t, i = one_year(con, cal, y)
        trades.append(t)
        infos.append(i)
    return pd.concat(trades, ignore_index=True), pd.DataFrame(infos)


def summarise(trades: pd.DataFrame, info: pd.DataFrame) -> pd.DataFrame:
    out = []
    ok = trades[trades.status != "no_entry_price"]
    for v, g in ok.groupby("variant"):
        k = int(v.split("-")[1])
        yr = g.groupby("year").agg(n=("net", "size"), gross=("gross", "mean"), net=("net", "mean")).join(
            info.set_index("year")[[f"spy{k}", f"iwm{k}"]])
        yr["vs_spy"] = yr.net - yr[f"spy{k}"]
        yr["vs_iwm"] = yr.net - yr[f"iwm{k}"]
        yr["usd_vs_spy"] = STAKE * yr.n * yr.vs_spy
        out.append(yr.assign(variant=v).reset_index())
    return pd.concat(out, ignore_index=True)


def report(trades: pd.DataFrame, info: pd.DataFrame) -> None:
    pd.set_option("display.width", 220, "display.max_rows", 200, "display.float_format", lambda x: f"{x:,.4f}")
    print("== step 0 / universe ==")
    print(info[["year", "S", "entry", "priced", "universe", "universe_ret10", "iwm10", "spy10"]].to_string(index=False))
    print("\n== dropped ==")
    print(trades.groupby(["variant", "status"]).size().unstack(fill_value=0).to_string())
    s = summarise(trades, info)
    print("\n== by year ==")
    print(s[["variant", "year", "n", "gross", "net", "vs_spy", "vs_iwm", "usd_vs_spy"]].to_string(index=False))
    print("\n== by variant (each December is one observation) ==")
    for v, g in s.groupby("variant"):
        best = g.vs_spy.idxmax()
        print(f"{v}: years {len(g)}, beat SPY in {(g.vs_spy > 0).sum()}, mean net {g.net.mean():+.4f}, "
              f"mean vs SPY {g.vs_spy.mean():+.4f} (median {g.vs_spy.median():+.4f}), mean vs IWM {g.vs_iwm.mean():+.4f}, "
              f"$ vs SPY {g.usd_vs_spy.sum():+,.0f}; without best year ({int(g.year[best])}): "
              f"mean vs SPY {g.vs_spy.drop(best).mean():+.4f}")
    ok = trades[trades.status != "no_entry_price"]
    v = ok[ok.variant == "W30-10"].sort_values("net")
    print("\n== W30-10: 5 worst and 5 best trades ==")
    print(pd.concat([v.head(5), v.tail(5)])[["year", "ticker", "ytd", "dvol", "entry", "entry_px", "exit", "status",
                                             "gross", "cost", "net"]].to_string(index=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "choose"
    if cmd == "final":
        if "--final" not in sys.argv:
            raise SystemExit("the final test runs once, with --final, after the user agrees")
        years = FINAL_YEARS
    else:
        years = CHOOSE_YEARS
    report(*run(years))
