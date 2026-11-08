"""EDGAR filing metadata: quarterly full-index (every 8-K / 8-K/A), CIK <-> ticker <-> exchange,
and the bulk submissions file (items per filing)."""
from __future__ import annotations

import gzip
import json
import zipfile
from datetime import date

import pandas as pd

from ingest.us.common import (
    SEC,
    SUBMISSIONS_ZIP,
    TICKERS_URL,
    bulk_download,
    done_keys,
    log,
    mark_progress,
    sec_client,
    session,
    us_cfg,
)
from ingest.us.parse import (
    parse_form_idx,
    quarters,
)


# ---------------------------------------------------------------- 1. quarterly index
def index() -> None:
    c = us_cfg()
    client = sec_client()
    with session() as con:
        done = done_keys(con)
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
            mark_progress(con, key, "partial" if current else "done", len(rows))
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
        mark_progress(con, "submissions", "done", nf)
    log.info("submissions: %d companies, %d 8-K filings; %d index filings not in submissions", len(comp), nf, missing)
