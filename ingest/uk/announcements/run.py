"""Announcement ingest CLI.

    # 1. discover announcements (listing pages only; cheap)
    uv run python -m ingest.uk.announcements.run index --mode company [--tickers DSG,ABC] [--limit 20] [--sample 20]
    uv run python -m ingest.uk.announcements.run index --mode daily --start 2015-01-01 --end 2015-01-31
    # 2. fetch full text for non-admin announcements of universe companies
    uv run python -m ingest.uk.announcements.run bodies [--limit 200] [--random]
    # 3. materialise announcements / announcements_admin tables, then report
    uv run python -m ingest.uk.announcements.run build
    uv run python -m ingest.uk.announcements.run report

Everything is resumable: listing progress is in scrape_progress, bodies in ann_bodies, raw HTML in
data/raw/uk/html (never re-downloaded).
"""
from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta

import pandas as pd

from core.config import date_range, load_config
from core.http import Blocked
from core.log import get_logger
from ingest.uk.announcements.base import iter_company, iter_day
from ingest.uk.announcements.investegate import Investegate
from ingest.uk.announcements.store import insert_index
from ingest.uk.common import connect, session, uk_cfg

log = get_logger("announcements")

MAX_CONSECUTIVE_BLOCKS = 3


class Breaker:
    """Stop the whole job after N consecutive blocked units instead of hammering a site
    that is refusing us. Exit code 3 = blocked (ingest/uk/run_all.sh stops; the check-in reports it)."""

    def __init__(self, n: int = MAX_CONSECUTIVE_BLOCKS):
        self.n, self.count = n, 0

    def ok(self) -> None:
        self.count = 0

    def blocked(self, e: Exception) -> None:
        self.count += 1
        log.error("blocked (%d/%d in a row): %s", self.count, self.n, e)
        if self.count >= self.n:
            log.error("STOPPING: source appears to be blocking/rate-limiting us")
            raise SystemExit(3)

SOURCES = {"investegate": Investegate}


def get_source():
    return SOURCES[uk_cfg()["announcements"]["source"]]()


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
    breaker = Breaker()
    for i, t in enumerate(todo, 1):
        rows = []
        try:
            for lp in iter_company(src, t, since=start):
                rows += [r for r in lp.rows if start <= r.published_uk.date() <= end]
            status = "done"
            breaker.ok()
        except Blocked as e:
            breaker.blocked(e)       # nothing recorded -> retried on the next run
            continue
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
    breaker = Breaker()
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
                breaker.ok()
            except Blocked as e:
                breaker.blocked(e)
                d += timedelta(days=1)
                continue
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
    cfg = uk_cfg()["announcements"]
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
    breaker = Breaker()

    def flush():
        if buf:
            with session() as con:
                con.executemany("INSERT OR REPLACE INTO ann_bodies VALUES (?,?,?,?,?)", buf)
            buf.clear()

    for i, (ann_id, url) in enumerate(todo, 1):
        try:
            status, text = src.body(url)
            breaker.ok()
        except Blocked as e:
            flush()
            breaker.blocked(e)       # not recorded -> retried on the next run
            continue
        except Exception as e:
            log.warning("body %s failed: %s", ann_id, e)
            status, text = "error", ""
        buf.append([ann_id, text or None, len(text or ""), status, datetime.now()])
        if len(buf) >= 10 or i == len(todo):
            flush()
        if i % 25 == 0 or i == len(todo):
            log.info("bodies %d/%d | net=%d cache=%d", i, len(todo), src.client.n_network, src.client.n_cache)


def reclassify() -> None:
    """Re-apply headline rules (admin filter / category) to everything already indexed."""
    from ingest.uk.announcements.rules import (
        admin_rule,
        headline_category,
        is_replacement,
    )
    with session() as con:
        df = con.execute("SELECT ann_id, headline FROM ann_index").df()
        df["admin_rule"] = df["headline"].map(admin_rule)
        df["is_admin"] = df["admin_rule"].notna()
        df["headline_category"] = df["headline"].map(headline_category)
        df["is_replacement"] = df["headline"].map(is_replacement)
        con.register("rc", df)
        con.execute("""UPDATE ann_index i SET admin_rule = rc.admin_rule, is_admin = rc.is_admin,
                       headline_category = rc.headline_category, is_replacement = rc.is_replacement
                       FROM rc WHERE i.ann_id = rc.ann_id""")
    log.info("reclassified %d rows: %d admin", len(df), int(df["is_admin"].sum()))


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
                       FROM announcements WHERE body_status='ok' ORDER BY hash(ann_id) LIMIT 10""").df()
    for _, r in s.iterrows():
        print(f"\n[{r.tidm}] {r.published_uk} pre_open={r.pre_open} cat={r.headline_category} chars={r.body_chars}")
        print(f"  {r.headline}")
        print(f"  {r.body_start}...")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index", "bodies", "reclassify", "build", "report"])
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
        mode = a.mode or uk_cfg()["announcements"]["index_mode"]
        if mode == "company":
            index_company(a.tickers, a.limit, a.sample, a.refresh)
        else:
            s, e = date_range(load_config())
            index_daily(a.start or s, a.end or e, a.refresh)
    elif a.cmd == "bodies":
        fetch_bodies(a.limit, a.random, a.tickers)
    elif a.cmd == "reclassify":
        reclassify()
    elif a.cmd == "build":
        build()
    else:
        report()
