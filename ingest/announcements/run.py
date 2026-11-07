"""Announcement ingest CLI.

    # 1. discover announcements (listing pages only; cheap)
    uv run python -m ingest.announcements.run index --mode company [--tickers DSG,ABC] [--limit 20] [--sample 20]
    uv run python -m ingest.announcements.run index --mode daily --start 2015-01-01 --end 2015-01-31
    # 2. fetch full text for non-admin announcements of universe companies
    uv run python -m ingest.announcements.run bodies [--limit 200] [--random]
    # 3. materialise announcements / announcements_admin tables, then report
    uv run python -m ingest.announcements.run build
    uv run python -m ingest.announcements.run report

Everything is resumable: listing progress is in scrape_progress, bodies in ann_bodies, raw HTML in
data/raw/html (never re-downloaded).
"""
from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta

import pandas as pd

from core.config import date_range, load_config
from core.db import connect, session
from core.log import get_logger
from ingest.announcements.base import iter_company, iter_day
from ingest.announcements.investegate import Investegate
from ingest.announcements.store import insert_index

log = get_logger("announcements")

SOURCES = {"investegate": Investegate}


def get_source():
    return SOURCES[load_config()["scrape"]["source"]]()


def _progress(con, key: str, status: str, n: int, source: str) -> None:
    con.execute("INSERT OR REPLACE INTO scrape_progress VALUES (?,?,?,?,?)",
                [source, key, status, n, datetime.now()])


def _done(con, source: str) -> set[str]:
    return set(r[0] for r in con.execute(
        "SELECT key FROM scrape_progress WHERE source=? AND status='done'", [source]).fetchall())


def index_company(tickers: list[str] | None, limit: int | None, sample: int | None, refresh: bool) -> None:
    cfg = load_config()
    start, end = date_range(cfg)
    src = get_source()
    with session() as con:
        if tickers is None:
            tickers = [r[0] for r in con.execute("SELECT tidm FROM universe ORDER BY tidm").fetchall()]
        done = set() if refresh else _done(con, src.name)
    todo = [t for t in tickers if f"company:{t}" not in done]
    n_skipped = len(tickers) - len(todo)
    if sample:
        random.seed(42)
        todo = random.sample(todo, min(sample, len(todo)))
    if limit:
        todo = todo[:limit]
    log.info("company index: %d tickers to scan (%d already done)", len(todo), n_skipped)
    for i, t in enumerate(todo, 1):
        rows = []
        try:
            for lp in iter_company(src, t, since=start):
                rows += [r for r in lp.rows if start <= r.published_uk.date() <= end]
            status = "done"
        except Exception as e:
            log.exception("company %s failed: %s", t, e)
            status = "error"
        n_rows = len(rows)
        with session() as con:
            n_new = insert_index(con, rows)
            _progress(con, f"company:{t}", status, n_rows, src.name)
        log.info("[%d/%d] %s: %d rows (%d new) | net=%d cache=%d", i, len(todo), t, n_rows, n_new,
                 src.client.n_network, src.client.n_cache)


def index_daily(start: date, end: date, refresh: bool) -> None:
    src = get_source()
    with session() as con:
        done = set() if refresh else _done(con, src.name)
    d = start
    while d <= end:
        key = f"day:{d.isoformat()}"
        if key not in done:
            rows = []
            try:
                for lp in iter_day(src, d):
                    rows += lp.rows
                # today's page is still growing -> don't mark done
                status = "done" if d < date.today() else "partial"
            except Exception as e:
                log.exception("day %s failed: %s", d, e)
                status = "error"
            with session() as con:
                n_new = insert_index(con, rows)
                _progress(con, key, status, len(rows), src.name)
            log.info("%s: %d rows (%d new) | net=%d cache=%d", d, len(rows), n_new,
                     src.client.n_network, src.client.n_cache)
        d += timedelta(days=1)


def fetch_bodies(limit: int | None, randomise: bool, tickers: list[str] | None = None) -> None:
    cfg = load_config()["scrape"]
    src = get_source()
    where = ["NOT i.is_admin", "b.ann_id IS NULL"]
    if cfg["bodies_only_for_universe"]:
        where.append("i.tidm IN (SELECT tidm FROM universe)")
    if cfg["bodies_only_pre_open"]:
        where.append("i.pre_open")
    if tickers:
        where.append("i.tidm IN (" + ",".join(f"'{t}'" for t in tickers) + ")")
    order = "random()" if randomise else "i.published_uk DESC"
    q = f"""SELECT i.ann_id, i.url FROM ann_index i LEFT JOIN ann_bodies b USING (ann_id)
            WHERE {' AND '.join(where)} ORDER BY {order}""" + (f" LIMIT {int(limit)}" if limit else "")
    with session() as con:
        if randomise:
            con.execute("SELECT setseed(0.42)")
        todo = con.execute(q).fetchall()
    log.info("bodies: %d to fetch", len(todo))
    buf = []
    for i, (ann_id, url) in enumerate(todo, 1):
        try:
            status, text = src.body(url)
        except Exception as e:
            log.warning("body %s failed: %s", ann_id, e)
            status, text = "error", ""
        buf.append([ann_id, text or None, len(text or ""), status, datetime.now()])
        if len(buf) >= 10 or i == len(todo):
            with session() as con:
                con.executemany("INSERT OR REPLACE INTO ann_bodies VALUES (?,?,?,?,?)", buf)
            buf = []
        if i % 25 == 0 or i == len(todo):
            log.info("bodies %d/%d | net=%d cache=%d", i, len(todo), src.client.n_network, src.client.n_cache)


def build() -> None:
    """Materialise the two output tables. Errors/missing bodies are retried by re-running `bodies`."""
    with session() as con:
        _build(con)


def _build(con) -> None:
    base = """SELECT i.*, b.body_text, b.body_chars, b.status AS body_status, b.fetched_at,
                     u.grp AS universe_grp, u.market AS universe_market
              FROM ann_index i LEFT JOIN ann_bodies b USING (ann_id) LEFT JOIN universe u USING (tidm)
              WHERE i.tidm IN (SELECT tidm FROM universe)"""
    con.execute(f"CREATE OR REPLACE TABLE announcements AS {base} AND NOT i.is_admin")
    con.execute(f"CREATE OR REPLACE TABLE announcements_admin AS {base} AND i.is_admin")
    n1 = con.execute("SELECT count(*) FROM announcements").fetchone()[0]
    n2 = con.execute("SELECT count(*) FROM announcements_admin").fetchone()[0]
    log.info("announcements: %d | announcements_admin: %d", n1, n2)


def report() -> None:
    con = connect(read_only=True)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 70)
    print("\n== discovered (universe tickers) per year ==")
    print(con.execute("""
        SELECT year(published_uk) yr,
               count(*) FILTER (WHERE NOT is_admin) non_admin,
               count(*) FILTER (WHERE is_admin) admin,
               count(*) FILTER (WHERE NOT is_admin AND pre_open) non_admin_pre_open,
               count(*) FILTER (WHERE NOT is_admin AND body_status='ok') with_body
        FROM (SELECT * FROM announcements UNION ALL SELECT * FROM announcements_admin)
        GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
    print("\n== non-admin by headline_category ==")
    print(con.execute("""SELECT headline_category, count(*) n, count(*) FILTER (WHERE pre_open) pre_open,
        round(100.0*count(*)/sum(count(*)) OVER (), 1) pct FROM announcements GROUP BY 1 ORDER BY 2 DESC""").df().to_string(index=False))
    print("\n== admin by rule ==")
    print(con.execute("""SELECT admin_rule, count(*) n FROM announcements_admin GROUP BY 1 ORDER BY 2 DESC""").df().to_string(index=False))
    print("\n== sample of 10 (non-admin, with body) ==")
    s = con.execute("""SELECT tidm, published_uk, pre_open, headline_category, headline, body_chars,
                              left(regexp_replace(body_text, '\\s+', ' ', 'g'), 220) AS body_start
                       FROM announcements WHERE body_status='ok' USING SAMPLE 10 ROWS (reservoir, 7)""").df()
    for _, r in s.iterrows():
        print(f"\n[{r.tidm}] {r.published_uk} pre_open={r.pre_open} cat={r.headline_category} chars={r.body_chars}")
        print(f"  {r.headline}")
        print(f"  {r.body_start}...")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index", "bodies", "build", "report"])
    ap.add_argument("--mode", choices=["company", "daily"], default=None)
    ap.add_argument("--tickers", type=lambda s: s.split(","))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sample", type=int, help="random sample of N universe tickers (company mode)")
    ap.add_argument("--random", action="store_true", help="bodies: random order")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    a = ap.parse_args()
    if a.cmd == "index":
        mode = a.mode or load_config()["scrape"]["index_mode"]
        if mode == "company":
            index_company(a.tickers, a.limit, a.sample, a.refresh)
        else:
            s, e = date_range(load_config())
            index_daily(a.start or s, a.end or e, a.refresh)
    elif a.cmd == "bodies":
        fetch_bodies(a.limit, a.random, a.tickers)
    elif a.cmd == "build":
        build()
    else:
        report()
