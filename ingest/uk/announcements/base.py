"""Pluggable announcement-source interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterator, Protocol


@dataclass
class IndexRow:
    source: str
    source_id: str
    url: str
    headline: str
    published_uk: datetime          # Europe/London wall-clock, naive
    tidm: str | None = None
    company: str | None = None
    supplier: str | None = None     # RNS, PRN, GNW, ...
    is_regulatory: bool | None = None

    @property
    def ann_id(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass
class ListingPage:
    rows: list[IndexRow] = field(default_factory=list)
    has_next: bool = False


class AnnouncementSource(Protocol):
    name: str

    def company_page(self, tidm: str, page: int) -> ListingPage: ...
    def day_page(self, day: date, page: int) -> ListingPage: ...
    def body(self, url: str) -> tuple[str, str]:
        """Return (status, text). status: ok | empty | not_found."""
        ...


def iter_company(src: AnnouncementSource, tidm: str, since: date, max_pages: int = 500) -> Iterator[ListingPage]:
    """Company listings are newest-first: stop once a page reaches back past `since`."""
    for p in range(1, max_pages + 1):
        lp = src.company_page(tidm, p)
        yield lp
        if not lp.has_next or not lp.rows or min(r.published_uk.date() for r in lp.rows) < since:
            return


def iter_day(src: AnnouncementSource, day: date, max_pages: int = 200) -> Iterator[ListingPage]:
    for p in range(1, max_pages + 1):
        lp = src.day_page(day, p)
        yield lp
        if not lp.has_next or not lp.rows:
            return
