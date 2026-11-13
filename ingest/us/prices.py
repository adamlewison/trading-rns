"""Daily prices from Yahoo (yfinance): NYSE + Nasdaq tickers, plus SPY and IWM."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd

from ingest.shared.prices import tidy_yahoo
from ingest.us.common import (
    log,
    session,
    us_cfg,
)
from ingest.us.parse import (
    yahoo_symbol,
)


# ---------------------------------------------------------------- 6. prices
def prices(limit: int | None = None, refresh: bool = False, only: list[str] | None = None) -> None:
    import yfinance as yf

    c = us_cfg()
    with session(read_only=True) as con:
        tk = con.execute(f"""SELECT DISTINCT ticker FROM sec_ticker_map
            WHERE snapshot_date = (SELECT max(snapshot_date) FROM sec_ticker_map)
              AND exchange IN ({','.join(repr(e) for e in c['price_exchanges'])})""").df()["ticker"].tolist()
        recent = set() if refresh else {r[0] for r in con.execute(
            "SELECT DISTINCT ticker FROM us_price_download_log WHERE attempted_at > ?",
            [datetime.now() - timedelta(days=3)]).fetchall()}
    todo = [t for t in list(c["benchmarks"]) + sorted(tk) if t not in recent]
    if only is not None:                        # paper trading: just these (full history, so adj_close stays consistent)
        todo = list(dict.fromkeys(list(c["benchmarks"]) + list(only)))
    if limit:
        todo = todo[:limit]
    log.info("prices: %d tickers", len(todo))
    start = f"{c['start_year']}-01-01"
    cols = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume", "is_stale", "is_suspect"]
    for i in range(0, len(todo), 40):
        batch = todo[i:i + 40]
        sym = {yahoo_symbol(t): t for t in batch}
        raw = None
        for attempt in range(3):
            try:
                raw = yf.download(list(sym), start=start, auto_adjust=False, actions=False, group_by="ticker",
                                  progress=False, threads=True)
                break
            except Exception as e:
                log.warning("price batch failed (%s)", e)
                time.sleep(5 * 2**attempt)
        frames, logs = [], []
        for s, t in sym.items():
            now = datetime.now()
            try:
                if raw is None:
                    raise RuntimeError("batch failed")
                sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = tidy_yahoo(sub, t, c["unit_glitch_ratio"], key="ticker")
            except Exception as e:
                logs.append((t, s, now, "error", 0, None, None, str(e)[:300]))
                continue
            if df.empty:
                logs.append((t, s, now, "empty", 0, None, None, "no rows"))
                continue
            frames.append(df[cols])
            logs.append((t, s, now, "ok", len(df), df["date"].min(), df["date"].max(), None))
        with session() as con:
            if frames:
                allp = pd.concat(frames, ignore_index=True)
                con.register("allp", allp)
                con.execute("DELETE FROM us_prices_daily WHERE ticker IN (SELECT DISTINCT ticker FROM allp)")
                con.execute(f"INSERT INTO us_prices_daily SELECT {', '.join(cols)} FROM allp")
            con.executemany("INSERT INTO us_price_download_log VALUES (?,?,?,?,?,?,?,?)", logs)
        if (i // 40) % 10 == 0:
            log.info("prices batch %d/%d", i // 40 + 1, -(-len(todo) // 40))
        time.sleep(2)
