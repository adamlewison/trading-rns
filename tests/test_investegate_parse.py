"""Parsing tests on small synthetic HTML that mimics Investegate's structure (no real content)."""
from datetime import date, datetime

from ingest.announcements.base import IndexRow, ListingPage, iter_company
from ingest.announcements.investegate import extract_body, parse_company_name, parse_listing

COMPANY_PAGE = """
<h1 id="main-title">Acme Widgets (ACME) RNS Announcements</h1>
<table class="table-investegate"><thead><tr><th>Date</th><th>Time</th><th>Source</th><th>Announcement</th></tr></thead>
<tbody>
<tr><td>01 Oct 2026</td><td>07:00 AM</td>
<td><div><a class="regulatory source-RNS" href="/source/RNS">RNS</a></div></td>
<td><a class="announcement-link" href="https://www.investegate.co.uk/announcement/rns/acme-widgets--acme/trading-update/9800214">Trading Update</a></td></tr>
<tr><td>30 Sep 2026</td><td>02:30 PM</td>
<td><div><a class="non-regulatory source-RNS" href="/source/RNS">RNS</a></div></td>
<td><a class="announcement-link" href="https://www.investegate.co.uk/announcement/rns/acme-widgets--acme/investor-webinar/9800001">Investor Webinar</a></td></tr>
</tbody></table>
<a href="https://www.investegate.co.uk/company/ACME?page=2">2</a>
"""

DAY_PAGE = """
<table class="table-investegate"><thead><tr><th>Time</th><th>Source</th><th>Company</th><th>Announcement</th></tr></thead>
<tbody>
<tr><td>10 Mar 2015 06:30 PM</td>
<td><div class="text-center"><a class="regulatory source-PRN" href="/source/PRN">PRN</a></div></td>
<td><div><a href="https://www.investegate.co.uk/company/BT.A">BT Group (BT.A)</a></div></td>
<td><a class="announcement-link" href="https://www.investegate.co.uk/announcement/prn/bt-group--bt-a/holding-s-in-company/3911788">Holding(s) in Company</a></td></tr>
</tbody></table>
"""

BODY_PAGE = """
<div id="ai-summary">AI SUMMARY SHOULD NOT APPEAR</div>
<div class="announcement-shadow-wrapper"><template shadowrootmode="open"><style>:host{display:block}</style>
<p>Acme Widgets PLC</p><p>Trading Update</p>
<p>Revenue is <b>ahead</b> of <a href="#">market expectations</a>.</p>
<table><tr><td>Revenue</td><td>£10.0m</td></tr></table>
<p>This information is provided by RNS, the news service of the London Stock Exchange.</p>
</template></div>
"""


def test_parse_company_page():
    lp = parse_listing(COMPANY_PAGE, tidm="ACME", company="Acme Widgets", page=1)
    assert lp.has_next
    assert len(lp.rows) == 2
    r = lp.rows[0]
    assert r.source_id == "9800214" and r.tidm == "ACME" and r.headline == "Trading Update"
    assert r.published_uk == datetime(2026, 10, 1, 7, 0)
    assert r.supplier == "RNS" and r.is_regulatory is True
    assert lp.rows[1].published_uk == datetime(2026, 9, 30, 14, 30)
    assert lp.rows[1].is_regulatory is False
    assert parse_company_name(COMPANY_PAGE) == "Acme Widgets"


def test_parse_day_page():
    lp = parse_listing(DAY_PAGE, page=1)
    assert not lp.has_next
    r = lp.rows[0]
    assert r.tidm == "BT.A" and r.company == "BT Group" and r.supplier == "PRN"
    assert r.published_uk == datetime(2015, 3, 10, 18, 30)


def test_extract_body():
    t = extract_body(BODY_PAGE)
    assert "AI SUMMARY" not in t
    assert ":host" not in t
    assert "Revenue is ahead of market expectations." in t   # inline tags don't break lines
    assert "Revenue\t£10.0m" in t
    assert "provided by RNS" not in t                        # boilerplate trailer cut


class FakeSource:
    name = "fake"

    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def company_page(self, tidm, page):
        self.calls += 1
        return self.pages[page - 1]


def _row(d):
    return IndexRow("fake", d.isoformat(), "u", "h", datetime(d.year, d.month, d.day, 7))


def test_iter_company_stops_once_past_start_date():
    pages = [ListingPage([_row(date(2024, 1, 1))], True),
             ListingPage([_row(date(2011, 6, 1))], True),     # reaches before `since`
             ListingPage([_row(date(2010, 1, 1))], False)]
    src = FakeSource(pages)
    got = list(iter_company(src, "X", since=date(2012, 1, 1)))
    assert len(got) == 2 and src.calls == 2
