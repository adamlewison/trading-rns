"""US data collection from SEC EDGAR (+ Yahoo prices). DATA ONLY: no LLM, features or backtests.

    uv run python -m ingest.us.run index          # EDGAR quarterly full-index -> every 8-K / 8-K/A
    uv run python -m ingest.us.run tickers        # company_tickers_exchange.json (current only)
    uv run python -m ingest.us.run submissions    # bulk submissions.zip -> items + acceptance times
    uv run python -m ingest.us.run estimate       # counts + disk estimate for the document download
    uv run python -m ingest.us.run docs --group earnings [--limit N] [--yes]   # Item 2.02 -> Ex-99.1
    uv run python -m ingest.us.run docs --group news     [--limit N] [--yes]   # 1.01/2.01/7.01/8.01
    uv run python -m ingest.us.run facts          # bulk companyfacts.zip -> selected XBRL concepts
    uv run python -m ingest.us.run prices         # yfinance daily OHLCV, NYSE/Nasdaq + SPY/IWM
    uv run python -m ingest.us.run report

Everything is resumable (progress table + raw cache in data/raw/us), rate-limited to ~5 req/s
against SEC (limit: 10), and failures are logged, never fatal. Uses data/us.duckdb only.
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import time
import zipfile
from datetime import date, datetime, timedelta

import pandas as pd

from core.http import Blocked
from ingest.us.common import bulk_download, check_disk, log, sec_client, session, us_cfg
from ingest.us.parse import (classify_session, et_to_utc, filing_base_url, html_to_text,
                             parse_filing_index, parse_form_idx, pick_exhibit, primary_doc,
                             quarters, yahoo_symbol)

SEC = "https://www.sec.gov"
SUBMISSIONS_ZIP = f"{SEC}/Archives/edgar/daily-index/bulkdata/submissions.zip"
FACTS_ZIP = f"{SEC}/Archives/edgar/daily-index/xbrl/companyfacts.zip"
TICKERS_URL = f"{SEC}/files/company_tickers_exchange.json"

# bytes per filing measured on a 400-filing pilot (Nov 2026): gzipped header page + gzipped
# document in the raw cache + extracted text in DuckDB (~1/3 of chars after compression)
EST_TEXT_BYTES = {"earnings": 10_000, "news": 5_500}   # extracted text kept in us.duckdb
EST_BYTES = {"earnings": 1_800 + 21_500 + 10_000, "news": 1_800 + 8_100 + 5_500}


def _progress(con, key: str, status: str, n: int) -> None:
    con.execute("INSERT OR REPLACE INTO us_progress VALUES (?,?,?,?)", [key, status, n, datetime.now()])


def _done(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT key FROM us_progress WHERE status='done'").fetchall()}


# ---------------------------------------------------------------- 1. quarterly index
def index() -> None:
    c = us_cfg()
    client = sec_client()
    with session() as con:
        done = _done(con)
    today = date.today()
    for y, q in quarters(c["start_year"], today):
        key = f"index:{y}Q{q}"
        current = (y, q) == quarters(c["start_year"], today)[-1]
        if key in done and not current:
            continue
        url = f"{SEC}/Archives/edgar/full-index/{y}/QTR{q}/form.gz"
        try:
            status, body = client.get(url, use_cache=not current)   # past quarters are immutable
        except Exception as e:
            log.error("index %s failed: %s", key, e)
            continue
        if status != 200:
            log.error("index %s: HTTP %s", key, status)
            continue
        text = gzip.decompress(body).decode("latin-1")
        rows = pd.DataFrame(parse_form_idx(text, set(c["forms"])))
        rows["year"], rows["qtr"] = y, q
        with session() as con:
            con.register("r", rows)
            con.execute("""INSERT OR REPLACE INTO sec_form_index
                SELECT accession, cik, company, form, date_filed, filename, year, qtr FROM r""")
            _progress(con, key, "partial" if current else "done", len(rows))
        log.info("index %s: %d 8-K/8-K/A filings", key, len(rows))


# ---------------------------------------------------------------- 2. tickers
def tickers() -> None:
    client = sec_client()
    status, body = client.get(TICKERS_URL, use_cache=False)
    d = json.loads(body)
    df = pd.DataFrame(d["data"], columns=d["fields"])
    df["snapshot_date"] = date.today()
    with session() as con:
        con.execute("DELETE FROM sec_ticker_map WHERE snapshot_date = ?", [date.today()])
        con.register("t", df)
        con.execute("INSERT INTO sec_ticker_map SELECT snapshot_date, cik, name, ticker, exchange FROM t")
    log.info("ticker map: %d rows; by exchange %s", len(df), df["exchange"].value_counts(dropna=False).to_dict())


# ---------------------------------------------------------------- 3. submissions (items, acceptance)
def submissions(refresh: bool = False) -> None:
    c = us_cfg()
    forms = set(c["forms"])
    start = f"{c['start_year']}-01-01"
    zpath = bulk_download(SUBMISSIONS_ZIP, c["raw_dir"] / "bulk" / "submissions.zip", refresh)
    with session() as con:
        ciks = {r[0] for r in con.execute("SELECT DISTINCT cik FROM sec_form_index").fetchall()}
    log.info("parsing submissions for %d CIKs with 8-K filings", len(ciks))
    comp_rows, fil_frames, n = [], [], 0

    def take(cols: dict, cik: int):
        df = pd.DataFrame({k: cols.get(k, []) for k in (
            "accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime", "items",
            "primaryDocument", "isXBRL")})
        if df.empty:
            return
        df = df[df["form"].isin(forms) & (df["filingDate"] >= start)]
        if not df.empty:
            df.insert(1, "cik", cik)
            fil_frames.append(df)

    def flush():
        if not fil_frames:
            return
        f = pd.concat(fil_frames, ignore_index=True)
        f["acceptanceDateTime"] = pd.to_datetime(f["acceptanceDateTime"], utc=True, errors="coerce").dt.tz_localize(None)
        f["reportDate"] = pd.to_datetime(f["reportDate"], errors="coerce").dt.date
        f["filingDate"] = pd.to_datetime(f["filingDate"], errors="coerce").dt.date
        f["isXBRL"] = f["isXBRL"].astype(bool)
        with session() as con:
            con.register("f", f)
            con.execute("""INSERT OR REPLACE INTO sec_filings SELECT accessionNumber, cik, form,
                filingDate, reportDate, acceptanceDateTime, items, primaryDocument, isXBRL FROM f""")
        fil_frames.clear()

    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        overflow: dict[int, list[str]] = {}
        for nm in names:
            if "-submissions-" in nm:
                overflow.setdefault(int(nm[3:13]), []).append(nm)
        for nm in names:
            if "-submissions-" in nm or not nm.startswith("CIK"):
                continue
            cik = int(nm[3:13])
            if cik not in ciks:
                continue
            d = json.loads(z.read(nm))
            comp_rows.append({
                "cik": cik, "name": d.get("name"), "tickers": ",".join(d.get("tickers") or []),
                "exchanges": ",".join(e or "" for e in (d.get("exchanges") or [])), "sic": d.get("sic"),
                "sic_description": d.get("sicDescription"), "state_of_inc": d.get("stateOfIncorporation"),
                "category": d.get("category"), "entity_type": d.get("entityType"),
                "fiscal_year_end": d.get("fiscalYearEnd"),
                "former_names": json.dumps(d.get("formerNames") or [])})
            take(d.get("filings", {}).get("recent", {}), cik)
            for ov in overflow.get(cik, []):
                take(json.loads(z.read(ov)), cik)
            n += 1
            if n % 2000 == 0:
                flush()
                log.info("  submissions: %d CIKs", n)
    flush()
    comp = pd.DataFrame(comp_rows)
    with session() as con:
        con.register("comp", comp)
        con.execute("INSERT OR REPLACE INTO sec_companies SELECT * FROM comp")
        nf = con.execute("SELECT count(*) FROM sec_filings").fetchone()[0]
        missing = con.execute("""SELECT count(*) FROM sec_form_index i
            WHERE NOT EXISTS (SELECT 1 FROM sec_filings f WHERE f.accession = i.accession)""").fetchone()[0]
        _progress(con, "submissions", "done", nf)
    log.info("submissions: %d companies, %d 8-K filings; %d index filings not in submissions", len(comp), nf, missing)


# ---------------------------------------------------------------- 4. documents
def _target_sql(group: str) -> str:
    c = us_cfg()
    items = c["earnings_items"] if group == "earnings" else c["news_items"]
    cond = " OR ".join(f"list_contains(string_split(f.items, ','), '{i}')" for i in items)
    extra = ""
    if group == "news":   # earnings filings are handled by the earnings group even if they also carry 8.01
        extra = " AND NOT " + "(" + " OR ".join(
            f"list_contains(string_split(f.items, ','), '{i}')" for i in c["earnings_items"]) + ")"
    return f"""SELECT f.accession, f.cik, f.form, f.primary_doc FROM sec_filings f
               LEFT JOIN us_filing_docs d USING (accession)
               WHERE d.accession IS NULL AND ({cond}){extra}"""


def estimate() -> dict:
    c = us_cfg()
    with session(read_only=True) as con:
        n_e = con.execute(f"SELECT count(*) FROM ({_target_sql('earnings')})").fetchone()[0]
        n_n = con.execute(f"SELECT count(*) FROM ({_target_sql('news')})").fetchone()[0]
        n_ciks = con.execute("SELECT count(DISTINCT cik) FROM sec_filings").fetchone()[0]
    est = {"earnings_filings": n_e, "news_filings": n_n, "ciks": n_ciks,
           "earnings_gb": n_e * EST_BYTES["earnings"] / 1e9, "news_gb": n_n * EST_BYTES["news"] / 1e9,
           "earnings_hours_at_5rps": n_e * 2 / 5 / 3600, "news_hours_at_5rps": n_n * 2 / 5 / 3600,
           "bulk_zips_gb": 3.0, "facts_db_gb": 0.6}
    est["total_gb"] = est["earnings_gb"] + est["news_gb"] + est["bulk_zips_gb"] + est["facts_db_gb"]
    est["free_gb"] = shutil.disk_usage(c["raw_dir"]).free / 1e9
    for k, v in est.items():
        print(f"{k:24} {v:,.2f}" if isinstance(v, float) else f"{k:24} {v:,}")
    return est


def docs(group: str, limit: int | None, assume_yes: bool, year: int | None = None, random: bool = False) -> None:
    c = us_cfg()
    client = sec_client()
    with session(read_only=True) as con:
        sql = _target_sql(group) + (f" AND year(f.filing_date) = {int(year)}" if year else "")
        sql += " ORDER BY " + ("hash(f.accession)" if random else "f.filing_date DESC")
        todo = con.execute(sql + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    # with prune_raw the cached pages move to the B2 backup every 30 min, so only the text stays local
    est = len(todo) * (EST_TEXT_BYTES[group] if c.get("prune_raw") else EST_BYTES[group])
    check_disk(est, f"{group} documents", interactive=True, assume_yes=assume_yes)
    min_free = float(c.get("min_free_gb", 3)) * 1e9
    log.info("docs[%s]: %d filings to fetch", group, len(todo))
    buf, consecutive_blocks = [], 0

    def flush():
        if buf:
            with session() as con:
                con.executemany("INSERT OR REPLACE INTO us_filing_docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buf)
            buf.clear()

    for i, (acc, cik, form, pdoc) in enumerate(todo, 1):
        base = filing_base_url(cik, acc)
        row = dict(acc=acc, cik=cik, group=group, form=form, items=None, et=None, utc=None, sess=None,
                   dtype=None, dname=None, durl=None, text=None, chars=0, status="error")
        try:
            st, body = client.get(f"{base}/{acc}-index.htm")
            if st == 404:
                row["status"] = "not_found"
            else:
                h = parse_filing_index(body.decode("utf-8", errors="replace"))
                row["items"] = ",".join(h["items"])
                if h["acceptance_et"]:
                    row["et"] = h["acceptance_et"]
                    row["utc"] = et_to_utc(h["acceptance_et"])
                    row["sess"] = classify_session(h["acceptance_et"])
                d = pick_exhibit(h["documents"]) or primary_doc(h["documents"], h["form"] or form)
                if d is None:
                    row["status"] = "no_document"
                else:
                    row["dtype"], row["dname"] = d["type"], d["filename"]
                    row["durl"] = f"{base}/{d['filename']}"
                    st2, raw = client.get(row["durl"])
                    if st2 == 404:
                        row["status"] = "not_found"
                    else:
                        row["text"] = html_to_text(raw.decode("utf-8", errors="replace"))
                        row["chars"] = len(row["text"])
                        row["status"] = "ok" if row["chars"] else "empty"
            consecutive_blocks = 0
        except Blocked as e:
            consecutive_blocks += 1
            log.error("blocked (%d in a row): %s", consecutive_blocks, e)
            if consecutive_blocks >= 3:
                flush()
                log.error("STOPPING: SEC appears to be refusing/rate-limiting us")
                raise SystemExit(3)
            continue                                    # not recorded -> retried next run
        except Exception as e:
            log.warning("doc %s failed: %s", acc, e)
        buf.append([row["acc"], row["cik"], row["group"], row["form"], row["items"], row["et"], row["utc"],
                    row["sess"], row["dtype"], row["dname"], row["durl"], row["text"], row["chars"],
                    row["status"], datetime.now()])
        if len(buf) >= 50 or i == len(todo):
            flush()
            if shutil.disk_usage(c["raw_dir"]).free < min_free:
                log.error("STOPPING: free disk below %.1f GB (rerun resumes; free space first)", min_free / 1e9)
                raise SystemExit(4)
        if i % 500 == 0:
            log.info("docs[%s] %d/%d | net=%d cache=%d", group, i, len(todo), client.n_network, client.n_cache)
    flush()


# ---------------------------------------------------------------- 5. company facts
def facts(refresh: bool = False) -> None:
    c = us_cfg()
    want = {tax: set(cs) for tax, cs in c["fact_concepts"].items()}
    zpath = bulk_download(FACTS_ZIP, c["raw_dir"] / "bulk" / "companyfacts.zip", refresh)
    with session() as con:
        ciks = {r[0] for r in con.execute("SELECT DISTINCT cik FROM sec_filings").fetchall()}
        con.execute("DELETE FROM us_facts")
    rows, n, n_rows = [], 0, 0

    def flush():
        nonlocal n_rows
        if rows:
            df = pd.DataFrame(rows, columns=["cik", "taxonomy", "concept", "unit", "val", "start_date",
                                             "end_date", "fy", "fp", "form", "filed", "accn", "frame"])
            for col in ("start_date", "end_date", "filed"):
                df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
            with session() as con:
                con.register("df", df)
                con.execute("INSERT INTO us_facts SELECT * FROM df")
            n_rows += len(rows)
            rows.clear()

    with zipfile.ZipFile(zpath) as z:
        for nm in z.namelist():
            try:
                cik = int(nm[3:13])
            except ValueError:
                continue
            if cik not in ciks:
                continue
            d = json.loads(z.read(nm))
            for tax, concepts in want.items():
                for concept in concepts:
                    node = d.get("facts", {}).get(tax, {}).get(concept)
                    if not node:
                        continue
                    for unit, vals in node.get("units", {}).items():
                        for v in vals:
                            rows.append((cik, tax, concept, unit, v.get("val"), v.get("start"), v.get("end"),
                                         v.get("fy"), v.get("fp"), v.get("form"), v.get("filed"),
                                         v.get("accn"), v.get("frame")))
            n += 1
            if len(rows) > 300_000:
                flush()
                log.info("  facts: %d CIKs, %d rows", n, n_rows)
    flush()
    with session() as con:
        _progress(con, "facts", "done", n_rows)
    log.info("facts: %d CIKs, %d rows", n, n_rows)


# ---------------------------------------------------------------- 6. prices
def prices(limit: int | None = None, refresh: bool = False) -> None:
    import yfinance as yf

    from ingest.prices import _tidy
    c = us_cfg()
    with session(read_only=True) as con:
        tk = con.execute(f"""SELECT DISTINCT ticker FROM sec_ticker_map
            WHERE snapshot_date = (SELECT max(snapshot_date) FROM sec_ticker_map)
              AND exchange IN ({','.join(repr(e) for e in c['price_exchanges'])})""").df()["ticker"].tolist()
        recent = set() if refresh else {r[0] for r in con.execute(
            "SELECT DISTINCT ticker FROM us_price_download_log WHERE attempted_at > ?",
            [datetime.now() - timedelta(days=3)]).fetchall()}
    todo = [t for t in list(c["benchmarks"]) + sorted(tk) if t not in recent]
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
                df = _tidy(sub, t, 30.0).rename(columns={"tidm": "ticker"})
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


# ---------------------------------------------------------------- report
def report() -> None:
    c = us_cfg()
    pd.set_option("display.width", 200)
    with session(read_only=True) as con:
        q = lambda s: con.execute(s).df()
        print("== 8-K / 8-K/A in EDGAR full-index, per year ==")
        print(q("SELECT year, count(*) filter (where form='8-K') n_8k, count(*) filter (where form='8-K/A') n_8ka FROM sec_form_index GROUP BY 1 ORDER BY 1").to_string(index=False))
        print("\n== filings per year by item (from submissions) ==")
        items = c["earnings_items"] + c["news_items"]
        cols = ", ".join(f"count(*) filter (where list_contains(string_split(items, ','), '{i}')) AS \"{i}\"" for i in items)
        print(q(f"SELECT year(filing_date) yr, count(*) n, {cols} FROM sec_filings GROUP BY 1 ORDER BY 1").to_string(index=False))
        print("\n== fetched documents ==")
        print(q("""SELECT item_group, status, count(*) n, round(avg(chars)) avg_chars FROM us_filing_docs GROUP BY ALL ORDER BY ALL""").to_string(index=False))
        print(q("""SELECT item_group, year(acceptance_et) yr, count(*) n,
                     round(100*avg((session='pre_market')::int),1) pre_pct, round(100*avg((session='intraday')::int),1) intra_pct,
                     round(100*avg((session='after_close')::int),1) after_pct
                   FROM us_filing_docs WHERE status='ok' GROUP BY ALL ORDER BY ALL""").to_string(index=False))
        print("\n== acceptance time: header (ET->UTC) vs submissions JSON ==")
        print(q("""SELECT count(*) n, count(*) filter (where abs(epoch(d.acceptance_utc) - epoch(f.acceptance_utc_sub)) <= 1) same,
                          count(*) filter (where abs(epoch(d.acceptance_utc) - epoch(f.acceptance_utc_sub)) between 3500 and 18100) off_by_hours
                   FROM us_filing_docs d JOIN sec_filings f USING (accession) WHERE d.acceptance_utc IS NOT NULL""").to_string(index=False))
        print("\n== companies / tickers / facts / prices ==")
        print(q("""SELECT (SELECT count(*) FROM sec_companies) companies,
                          (SELECT count(DISTINCT cik) FROM sec_ticker_map) ciks_with_ticker,
                          (SELECT count(*) FROM us_facts) fact_rows,
                          (SELECT count(DISTINCT ticker) FROM us_prices_daily) priced_tickers,
                          (SELECT count(*) FROM us_prices_daily) price_rows""").to_string(index=False))
        print(q("""WITH l AS (SELECT *, row_number() OVER (PARTITION BY ticker ORDER BY attempted_at DESC) rn FROM us_price_download_log)
                   SELECT status, count(*) FROM l WHERE rn=1 GROUP BY 1""").to_string(index=False))
        print("\n== 10 sample earnings releases ==")
        s = q("""SELECT d.cik, c.name, t.ticker, d.acceptance_et, d.acceptance_utc, d.session, d.items_header,
                        d.doc_type, d.chars, left(regexp_replace(d.text, '\\s+', ' ', 'g'), 200) AS start
                 FROM us_filing_docs d LEFT JOIN sec_companies c USING (cik)
                 LEFT JOIN (SELECT cik, any_value(ticker) ticker FROM sec_ticker_map GROUP BY 1) t USING (cik)
                 WHERE d.item_group='earnings' AND d.status='ok' ORDER BY hash(d.accession) LIMIT 10""")
        for _, r in s.iterrows():
            print(f"\n[{r.ticker or '-'} | CIK {r.cik}] {r['name']}")
            print(f"  accepted {r.acceptance_et} ET = {r.acceptance_utc} UTC  session={r.session}  items={r.items_header}  {r.doc_type} {r.chars} chars")
            print(f"  {r.start}...")
    import subprocess
    print("\n== storage ==")
    print(subprocess.run(["du", "-sh", str(c["db"]), str(c["raw_dir"] / "sec"), str(c["raw_dir"] / "bulk")],
                         capture_output=True, text=True).stdout)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index", "tickers", "submissions", "estimate", "docs", "facts", "prices", "report"])
    ap.add_argument("--group", choices=["earnings", "news"], default="earnings")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--yes", action="store_true", help="approve a download estimated above the disk warning threshold")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--year", type=int, help="docs: only filings from this year (pilots)")
    ap.add_argument("--random", action="store_true", help="docs: random order instead of newest first")
    a = ap.parse_args()
    if a.cmd == "docs":
        docs(a.group, a.limit, a.yes, a.year, a.random)
    elif a.cmd in ("submissions", "facts"):
        globals()[a.cmd](a.refresh)
    elif a.cmd == "prices":
        prices(a.limit, a.refresh)
    else:
        globals()[a.cmd]()
