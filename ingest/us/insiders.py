"""Insider transactions (Form 4) from SEC's quarterly Insider Transactions Data Sets.

    uv run python -W ignore -m ingest.us.run insiders [--refresh]

One zip per quarter (2006 Q1 on) from
https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/YYYYqN_form345.zip,
kept in data/raw/us/bulk/ (backed up by `core.backup push-bulk`). Every non-derivative transaction
on Form 4 / 4/A goes into `us_insider_tx`, with the reporting owners' roles. Resumable: a quarter is
loaded once (us_progress key `insiders:YYYYqN`); `--refresh` re-downloads the latest two quarters,
which SEC may still be completing.

Timing: the data sets give the filing DATE only, not the time. Backtests must assume the filing was
seen after that day's close (trade the next session at the earliest)."""
from __future__ import annotations

import io
import time
import zipfile
from datetime import date

import httpx
import pandas as pd

from ingest.us.common import bulk_download, done_keys, log, mark_progress, session, us_cfg, user_agent

URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
FIRST = (2006, 1)
COLS = ["accession", "quarter", "filing_date", "doc_type", "issuer_cik", "issuer_name", "issuer_ticker",
        "owner_ciks", "owner_names", "relationship", "titles", "is_director", "is_officer", "is_ten_pct",
        "n_owners", "aff10b5one", "trans_sk", "security_title", "trans_date", "trans_code", "acq_disp",
        "shares", "price", "shares_after", "direct_indirect"]


def quarters(today: date | None = None) -> list[str]:
    today = today or date.today()
    out, (y, q) = [], FIRST
    while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
        out.append(f"{y}q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def _read(z: zipfile.ZipFile, name: str, cols: list[str]) -> pd.DataFrame:
    """Columns missing from older quarters (e.g. AFF10B5ONE, added 2023) come back empty."""
    with z.open(name) as f:
        have = f.readline().decode("utf-8", "replace").rstrip("\r\n").split("\t")
    with z.open(name) as f:
        df = pd.read_csv(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), sep="\t", dtype=str,
                         quoting=3, usecols=[c for c in cols if c in have], keep_default_na=False)
    for c in cols:
        if c not in df:
            df[c] = ""
    return df[cols]


def parse_quarter(zpath, quarter: str) -> pd.DataFrame:
    """One quarter's zip -> one row per non-derivative Form 4 transaction, owners aggregated."""
    with zipfile.ZipFile(zpath) as z:
        sub = _read(z, "SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK",
                                          "ISSUERNAME", "ISSUERTRADINGSYMBOL", "AFF10B5ONE"])
        own = _read(z, "REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                                              "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"])
        tx = _read(z, "NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE",
                                             "TRANS_DATE", "TRANS_CODE", "TRANS_ACQUIRED_DISP_CD",
                                             "TRANS_SHARES", "TRANS_PRICEPERSHARE", "SHRS_OWND_FOLWNG_TRANS",
                                             "DIRECT_INDIRECT_OWNERSHIP"])
    sub = sub[sub.DOCUMENT_TYPE.isin(["4", "4/A"])]
    rel = own.RPTOWNER_RELATIONSHIP.fillna("")
    own = own.assign(d=rel.str.contains("Director").astype(int), o=rel.str.contains("Officer").astype(int),
                     t=rel.str.contains("TenPercent").astype(int))
    g = own.groupby("ACCESSION_NUMBER").agg(
        owner_ciks=("RPTOWNERCIK", lambda s: ";".join(s)), owner_names=("RPTOWNERNAME", lambda s: ";".join(s)),
        relationship=("RPTOWNER_RELATIONSHIP", lambda s: ";".join(s)),
        titles=("RPTOWNER_TITLE", lambda s: ";".join(x for x in s if x)),
        is_director=("d", "max"), is_officer=("o", "max"), is_ten_pct=("t", "max"),
        n_owners=("RPTOWNERCIK", "size")).reset_index()
    df = tx.merge(sub, on="ACCESSION_NUMBER").merge(g, on="ACCESSION_NUMBER", how="left")
    num = lambda s: pd.to_numeric(s, errors="coerce")
    dt = lambda s: pd.to_datetime(s, format="%d-%b-%Y", errors="coerce").dt.date
    out = pd.DataFrame({
        "accession": df.ACCESSION_NUMBER, "quarter": quarter, "filing_date": dt(df.FILING_DATE),
        "doc_type": df.DOCUMENT_TYPE, "issuer_cik": num(df.ISSUERCIK).astype("Int64"),
        "issuer_name": df.ISSUERNAME, "issuer_ticker": df.ISSUERTRADINGSYMBOL.str.upper().str.strip(),
        "owner_ciks": df.owner_ciks, "owner_names": df.owner_names, "relationship": df.relationship,
        "titles": df.titles, "is_director": df.is_director, "is_officer": df.is_officer,
        "is_ten_pct": df.is_ten_pct, "n_owners": df.n_owners,
        "aff10b5one": df.AFF10B5ONE.str.lower().map({"1": True, "true": True, "0": False, "false": False}),
        "trans_sk": num(df.NONDERIV_TRANS_SK).astype("Int64"), "security_title": df.SECURITY_TITLE,
        "trans_date": dt(df.TRANS_DATE), "trans_code": df.TRANS_CODE, "acq_disp": df.TRANS_ACQUIRED_DISP_CD,
        "shares": num(df.TRANS_SHARES), "price": num(df.TRANS_PRICEPERSHARE),
        "shares_after": num(df.SHRS_OWND_FOLWNG_TRANS), "direct_indirect": df.DIRECT_INDIRECT_OWNERSHIP})
    return out[COLS]


def insiders(refresh: bool = False) -> None:
    c = us_cfg()
    qs = quarters()
    with session(memory_limit="300MB") as con:
        done = {k.split(":", 1)[1] for k in done_keys(con) if k.startswith("insiders:")}
    redo = set(qs[-2:]) if refresh else set()
    for q in qs:
        if q in done and q not in redo:
            continue
        dest = c["raw_dir"] / "bulk" / f"insider_{q}_form345.zip"
        if not dest.exists() or q in redo:                 # a quarter SEC hasn't published yet: 404
            code = httpx.head(URL.format(q=q), headers={"User-Agent": user_agent()}, timeout=60,
                              follow_redirects=True).status_code
            if code != 200:
                log.info("insiders %s: not published yet (HTTP %d)", q, code)
                continue
        try:
            bulk_download(URL.format(q=q), dest, refresh=q in redo)
        except (RuntimeError, httpx.HTTPError) as e:
            log.info("insiders %s: not available yet (%s)", q, str(e)[:80])
            continue
        df = parse_quarter(dest, q)
        with session(memory_limit="300MB") as con:
            con.execute("DELETE FROM us_insider_tx WHERE quarter = ?", [q])
            con.register("df", df)
            con.execute("INSERT INTO us_insider_tx SELECT * FROM df")
            mark_progress(con, f"insiders:{q}", "done", len(df))
        n_buy = int((df.trans_code == "P").sum())
        log.info("insiders %s: %d transactions (%d open-market purchases)", q, len(df), n_buy)
        time.sleep(1.0)                    # one file a second: far below SEC's limit
    with session(read_only=True) as con:
        n, nb, q0, q1 = con.execute("""SELECT count(*), count(*) FILTER (WHERE trans_code = 'P'),
                                       min(quarter), max(quarter) FROM us_insider_tx""").fetchone()
    log.info("INSIDERS COMPLETE: %d transactions, %d purchases, %s to %s", n, nb, q0, q1)
