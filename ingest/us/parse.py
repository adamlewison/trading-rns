"""Pure parsing helpers for SEC EDGAR data (tested in tests/test_us_parse.py)."""
from __future__ import annotations

import html as htmlmod
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
OPEN, CLOSE = time(9, 30), time(16, 0)

_ACC_RE = re.compile(r"(\d{10}-\d{2}-\d{6})")


def accession_from_path(path: str) -> str | None:
    """'edgar/data/320193/0000320193-26-000024.txt' -> '0000320193-26-000024'."""
    m = _ACC_RE.search(path or "")
    return m.group(1) if m else None


def filing_base_url(cik: int | str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"


def parse_form_idx(text: str, forms: set[str]) -> list[dict]:
    """Parse an EDGAR full-index form.idx (fixed-width). Columns are located from the header
    line, so we don't depend on hard-coded offsets."""
    lines = text.splitlines()
    hdr_i = next(i for i, l in enumerate(lines) if l.startswith("Form Type") and "File Name" in l)
    h = lines[hdr_i]
    cols = {k: h.index(k) for k in ("Form Type", "Company Name", "CIK", "Date Filed", "File Name")}
    out = []
    for l in lines[hdr_i + 2:]:
        if not l.strip():
            continue
        form = l[: cols["Company Name"]].strip()
        if form not in forms:
            continue
        # CIK/date/filename are right-hand whitespace-separated fields; company names can be long
        parts = l[cols["Company Name"]:].rstrip().rsplit(None, 3)
        if len(parts) != 4:
            continue
        company, cik, filed, fname = parts
        out.append({"form": form, "company": company.strip(), "cik": int(cik),
                    "date_filed": date.fromisoformat(filed), "filename": fname,
                    "accession": accession_from_path(fname)})
    return out


def parse_index_headers(page: str) -> dict:
    """Parse '<acc>-index-headers.html': acceptance datetime (ET), items, document list."""
    t = htmlmod.unescape(page)
    m = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", t)
    acc_et = datetime.strptime(m.group(1), "%Y%m%d%H%M%S") if m else None
    head = t.split("</SEC-HEADER>")[0]
    items = list(dict.fromkeys(re.findall(r"<ITEMS>\s*([0-9.]+)", head)))
    form = re.search(r"<TYPE>([^\n<]+)", head)
    docs = []
    for blk in re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", t, re.S):
        ty = re.search(r"<TYPE>([^\n<]+)", blk)
        fn = re.search(r"<FILENAME>([^\n<]+)", blk)
        de = re.search(r"<DESCRIPTION>([^\n<]*)", blk)
        if ty and fn:
            docs.append({"type": ty.group(1).strip().upper(), "filename": fn.group(1).strip(),
                         "description": (de.group(1).strip() if de else None)})
    return {"acceptance_et": acc_et, "items": items, "form": form.group(1).strip() if form else None,
            "documents": docs}


def parse_filing_index(page: str) -> dict:
    """Parse the '<acc>-index.htm' filing index page, which exists for every EDGAR filing (the
    '-index-headers.html' variant is missing for older ones). 'Accepted' is US Eastern wall time."""
    from selectolax.lexbor import LexborHTMLParser

    tree = LexborHTMLParser(page)
    acc_et, items, form = None, [], None
    for grp in tree.css("div.formGrouping"):
        heads = [h.text(strip=True) for h in grp.css("div.infoHead")]
        vals = [v.text(strip=True) for v in grp.css("div.info")]
        for h, v in zip(heads, vals):
            if h == "Accepted":
                acc_et = datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
        if heads and heads[0].startswith("Items"):
            items = re.findall(r"Item\s+(\d+\.\d+)", grp.text(separator=" "))
    fid = tree.css_first("#formName")
    if fid:
        m = re.search(r"Form\s+([\w\-/]+)", fid.text())
        form = m.group(1) if m else None
    docs = []
    for tbl in tree.css("table.tableFile"):
        if "Document Format" not in (tbl.attributes.get("summary") or ""):
            continue
        for tr in tbl.css("tr")[1:]:
            tds = tr.css("td")
            if len(tds) < 4:
                continue
            a = tds[2].css_first("a")
            fname = a.text(strip=True) if a else tds[2].text(strip=True)
            ty = tds[3].text(strip=True).upper()
            if fname:
                docs.append({"type": ty, "filename": fname.split(" ")[0], "description": tds[1].text(strip=True)})
    return {"acceptance_et": acc_et, "items": list(dict.fromkeys(items)), "form": form, "documents": docs}


def et_to_utc(dt_et: datetime) -> datetime:
    """Naive US/Eastern wall clock -> naive UTC (DST-aware)."""
    return dt_et.replace(tzinfo=ET).astimezone(UTC).replace(tzinfo=None)


def utc_to_et(dt_utc: datetime) -> datetime:
    return dt_utc.replace(tzinfo=UTC).astimezone(ET).replace(tzinfo=None)


def classify_session(dt_et: datetime) -> str:
    """pre_market (< 09:30 ET) / intraday (09:30–16:00) / after_close (>= 16:00), by wall-clock
    time on weekdays; weekends -> non_trading_day. Exchange holidays are flagged later from the
    benchmark price calendar (a holiday weekday looks like a normal day here)."""
    if dt_et.weekday() >= 5:
        return "non_trading_day"
    t = dt_et.time()
    if t < OPEN:
        return "pre_market"
    if t < CLOSE:
        return "intraday"
    return "after_close"


_EX_RE = re.compile(r"^EX-99(\.0?1)?$")


def pick_exhibit(docs: list[dict]) -> dict | None:
    """The press release is normally EX-99.1 (sometimes EX-99 or EX-99.01); take the first match,
    else the first EX-99.x."""
    for d in docs:
        if _EX_RE.match(d["type"]):
            return d
    for d in docs:
        if d["type"].startswith("EX-99"):
            return d
    return None


def primary_doc(docs: list[dict], form: str | None) -> dict | None:
    for d in docs:
        if form and d["type"] == form.upper():
            return d
    return docs[0] if docs else None


def html_to_text(raw: str) -> str:
    """Readable text from an EDGAR HTML/TXT document: block elements -> newlines, table cells ->
    tabs, inline tags joined (so '<b>12</b>%' stays '12%')."""
    from selectolax.lexbor import LexborHTMLParser

    if "<" not in raw[:2000] and "<html" not in raw.lower()[:5000]:
        text = raw                                         # plain-text exhibit (older filings)
    else:
        tree = LexborHTMLParser(raw)
        for n in tree.css("script, style, noscript, head, ix\\:header"):
            n.decompose()
        for br in tree.css("br"):
            br.replace_with("\n")
        for n in tree.css("p, div, tr, li, h1, h2, h3, h4, h5, h6, table, pre"):
            n.insert_after("\n")
        for n in tree.css("td, th"):
            n.insert_after("\t")
        body = tree.body or tree.root
        text = body.text(separator="", strip=False) if body else ""
    text = text.replace("\xa0", " ").replace("​", "").replace("﻿", "")
    text = re.sub(r" *\t[ \t]*", "\t", text)
    text = re.sub(r" +", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def yahoo_symbol(ticker: str) -> str:
    """SEC 'BRK-B' / 'BRK.B' -> Yahoo 'BRK-B'."""
    return ticker.strip().upper().replace(".", "-").replace("/", "-")


def quarters(start_year: int, today: date) -> list[tuple[int, int]]:
    last_q = (today.month - 1) // 3 + 1
    return [(y, q) for y in range(start_year, today.year + 1) for q in range(1, 5)
            if (y, q) <= (today.year, last_q)]
