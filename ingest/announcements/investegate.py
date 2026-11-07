"""Investegate (www.investegate.co.uk) source.

robots.txt (checked 2026-11-07) allows /company/, /today-announcements/ and /announcement/.
Terms of use: personal reference by private investors only; no redistribution or commercial use.
=> Use for personal research only, never republish the text, rate-limit politely (see README).

Timestamps on listing pages are UK wall-clock time ("01 Oct 2026" + "07:00 AM").
The announcement page itself carries no machine-readable timestamp, so the listing time is
authoritative. The page also shows an "AI summary" block, which we deliberately ignore.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from core.http import PoliteClient
from ingest.announcements.base import IndexRow, ListingPage

BASE = "https://www.investegate.co.uk"
_ID_RE = re.compile(r"/announcement/([a-z0-9]+)/[^/]+/[^/]+/(\d+)")
_TIDM_IN_NAME = re.compile(r"\(([A-Z0-9.]{1,6})\)\s*$")
_TRAILER = re.compile(
    r"\n\s*(This information is provided by RNS|This information is provided by Reach|"
    r"Terms and conditions relating to the use and distribution of this information)", re.I)


def _parse_dt(d: str, t: str | None = None) -> datetime:
    s = f"{d} {t}" if t else d
    s = re.sub(r"\s+", " ", s.strip())
    for fmt in ("%d %b %Y %I:%M %p", "%d %b %Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"unparseable datetime {s!r}")


def _supplier(td) -> tuple[str | None, bool | None]:
    a = td.css_first("a")
    if a is None:
        return None, None
    cls = a.attributes.get("class") or ""
    m = re.search(r"source-([A-Za-z]*)", cls)
    reg = None
    if "non-regulatory" in cls:
        reg = False
    elif "regulatory" in cls:
        reg = True
    return ((m.group(1) or None) if m else None), reg


def parse_listing(html: str, tidm: str | None = None, company: str | None = None,
                  page: int = 1) -> ListingPage:
    """Parse a company page (Date|Time|Source|Announcement) or a day page
    (Time|Source|Company|Announcement)."""
    tree = HTMLParser(html)
    rows: list[IndexRow] = []
    for tr in tree.css("table.table-investegate tbody tr"):
        tds = tr.css("td")
        link = tr.css_first("a.announcement-link")
        if link is None or len(tds) < 4:
            continue
        href = link.attributes.get("href") or ""
        m = _ID_RE.search(href)
        if not m:
            continue
        comp_link = tds[2].css_first('a[href*="/company/"]')
        if comp_link is not None:            # day page
            published = _parse_dt(tds[0].text(strip=True))
            supplier, reg = _supplier(tds[1])
            name_txt = tds[2].text(separator=" ", strip=True)
            row_tidm = comp_link.attributes.get("href", "").rstrip("/").split("/")[-1].upper() or None
            row_company = _TIDM_IN_NAME.sub("", name_txt).strip() or None
        else:                                # company page
            published = _parse_dt(tds[0].text(strip=True), tds[1].text(strip=True))
            supplier, reg = _supplier(tds[2])
            row_tidm, row_company = tidm, company
        rows.append(IndexRow(
            source="investegate", source_id=m.group(2), url=href if href.startswith("http") else BASE + href,
            headline=link.text(strip=True), published_uk=published, tidm=row_tidm, company=row_company,
            supplier=supplier or m.group(1).upper(), is_regulatory=reg,
        ))
    has_next = bool(tree.css_first(f'a[href*="page={page + 1}"]'))
    return ListingPage(rows=rows, has_next=has_next)


def parse_company_name(html: str) -> str | None:
    h1 = HTMLParser(html).css_first("h1#main-title")
    if h1 is None:
        return None
    t = re.sub(r"\s*RNS Announcements\s*$", "", h1.text(strip=True))
    return _TIDM_IN_NAME.sub("", t).strip() or None


def extract_body(html: str) -> str:
    """Announcement text lives in a declarative shadow DOM <template> inside
    .announcement-shadow-wrapper; fall back to .news-window for older layouts."""
    m = re.search(r'announcement-shadow-wrapper[^>]*>\s*<template[^>]*>(.*?)</template>', html, re.S)
    if m:
        frag = HTMLParser(m.group(1))
    else:
        tree = HTMLParser(html)
        node = tree.css_first("div.news-window")
        if node is None:
            return ""
        frag = HTMLParser(node.html)
    for n in frag.css("style, script, noscript"):
        n.decompose()
    for br in frag.css("br"):
        br.replace_with("\n")
    # block elements end a line; table cells are tab-separated; inline elements (a, span, b)
    # join without breaks so sentences are not chopped up
    for n in frag.css("p, div, tr, li, h1, h2, h3, h4, h5, h6, table, pre, blockquote"):
        n.insert_after("\n")
    for n in frag.css("td, th"):
        n.insert_after("\t")
    body = frag.body or frag.root
    if body is None:
        return ""
    text = body.text(separator="", strip=False)
    text = text.replace("\xa0", " ").replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r" *\t[ \t]*", "\t", text)
    text = re.sub(r" +", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    cut = _TRAILER.search(text)
    if cut:
        text = text[: cut.start()].rstrip()
    return text


class Investegate:
    name = "investegate"

    def __init__(self, client: PoliteClient | None = None):
        self.client = client or PoliteClient(cache_subdir="html")
        self._company_names: dict[str, str | None] = {}

    def _get(self, url: str, use_cache: bool = True) -> tuple[int, str]:
        status, body = self.client.get(url, use_cache=use_cache)
        return status, body.decode("utf-8", errors="replace")

    def company_page(self, tidm: str, page: int, use_cache: bool = True) -> ListingPage:
        url = f"{BASE}/company/{tidm}" + (f"?page={page}" if page > 1 else "")
        status, html = self._get(url, use_cache)
        if status != 200:
            return ListingPage()
        if tidm not in self._company_names:
            self._company_names[tidm] = parse_company_name(html)
        return parse_listing(html, tidm=tidm, company=self._company_names[tidm], page=page)

    def day_page(self, day: date, page: int, use_cache: bool = True) -> ListingPage:
        url = f"{BASE}/today-announcements/{day.isoformat()}" + (f"?page={page}" if page > 1 else "")
        status, html = self._get(url, use_cache)
        if status != 200:
            return ListingPage()
        return parse_listing(html, page=page)

    def body(self, url: str) -> tuple[str, str]:
        status, html = self._get(url)
        if status in (404, 410):
            return "not_found", ""
        text = extract_body(html)
        return ("ok" if text else "empty"), text
