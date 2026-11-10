"""Filing documents: each 8-K's index page (acceptance time, items) and its press release
(Ex-99.1, else the 8-K itself), as text. Earnings (Item 2.02) and news (1.01/2.01/7.01/8.01)."""
from __future__ import annotations

import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import duckdb

from core.http import Blocked
from ingest.us.common import (
    check_disk,
    log,
    sec_client,
    session,
    us_cfg,
)
from ingest.us.parse import (
    classify_session,
    et_to_utc,
    filing_base_url,
    html_to_text,
    parse_filing_index,
    pick_exhibit,
    primary_doc,
)

# bytes per filing measured on a 400-filing pilot (Nov 2026): gzipped header page + gzipped
# document in the raw cache + extracted text in DuckDB (~1/3 of chars after compression)
EST_TEXT_BYTES = {"earnings": 10_000, "news": 5_500}   # extracted text kept in us.duckdb
FLUSH_MEMORY_LIMIT = "200MB"   # DuckDB cap when saving rows (1GB droplet, shared with the backup)
FLUSH_RETRIES, FLUSH_RETRY_S = 10, 60
BATCH = 100   # filings handed to the workers at a time (a stop takes effect within one batch)
EST_BYTES = {"earnings": 1_800 + 21_500 + 10_000, "news": 1_800 + 8_100 + 5_500}



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
           "earnings_hours": n_e * 2 * c["min_interval_s"] / 3600, "news_hours": n_n * 2 * c["min_interval_s"] / 3600,
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
        """Save the buffered rows. Capped memory, and a short shortage (the backup running on the same
        1GB machine) is waited out instead of killing a multi-day run."""
        if not buf:
            return
        for attempt in range(FLUSH_RETRIES):
            try:
                with session(memory_limit=FLUSH_MEMORY_LIMIT) as con:
                    con.executemany("INSERT OR REPLACE INTO us_filing_docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buf)
                buf.clear()
                return
            except duckdb.OutOfMemoryException as e:
                if attempt == FLUSH_RETRIES - 1:
                    raise
                log.warning("saving %d rows ran out of memory (%s); retry %d in %ds",
                            len(buf), str(e).splitlines()[0], attempt + 1, FLUSH_RETRY_S)
                time.sleep(FLUSH_RETRY_S)

    def fetch(item):
        """Network + parsing for one filing (runs in a worker thread). Returns the row, or the
        Blocked exception so the main thread can count it."""
        acc, cik, form, _pdoc = item
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
        except Blocked as e:
            return e
        except Exception as e:
            log.warning("doc %s failed: %s", acc, e)
        return row

    # Workers share the client, so the per-host rate limit (min_interval_s) holds across all of
    # them: more workers only fill the time spent waiting for SEC's replies, never exceed 8 req/s.
    workers = max(1, int(c.get("workers", 1)))
    log.info("docs[%s]: %d worker(s) sharing min_interval_s=%s", group, workers, c["min_interval_s"])
    i = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(todo), BATCH):
            for res in pool.map(fetch, todo[start:start + BATCH]):   # results in input order
                i += 1
                if isinstance(res, Blocked):
                    consecutive_blocks += 1
                    log.error("blocked (%d in a row): %s", consecutive_blocks, res)
                    if consecutive_blocks >= 3:
                        flush()
                        log.error("STOPPING: SEC appears to be refusing/rate-limiting us")
                        raise SystemExit(3)
                    continue                            # not recorded -> retried next run
                consecutive_blocks = 0
                row = res
                buf.append([row["acc"], row["cik"], row["group"], row["form"], row["items"], row["et"],
                            row["utc"], row["sess"], row["dtype"], row["dname"], row["durl"], row["text"],
                            row["chars"], row["status"], datetime.now()])
                if len(buf) >= 50:
                    flush()
                    if shutil.disk_usage(c["raw_dir"]).free < min_free:
                        log.error("STOPPING: free disk below %.1f GB (rerun resumes; free space first)",
                                  min_free / 1e9)
                        raise SystemExit(4)
                if i % 500 == 0:
                    log.info("docs[%s] %d/%d | net=%d cache=%d", group, i, len(todo),
                             client.n_network, client.n_cache)
    flush()
