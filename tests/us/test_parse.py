from datetime import date, datetime

from ingest.us.parse import (
    accession_from_path,
    classify_session,
    et_to_utc,
    html_to_text,
    parse_form_idx,
    parse_index_headers,
    pick_exhibit,
    quarters,
    yahoo_symbol,
)

FORM_IDX = """Description:           Master Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    March 31, 2012

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
10-K        ACME CORP                                                     1234567     2012-02-01  edgar/data/1234567/0001234567-12-000001.txt
8-K         APPLE INC                                                     320193      2012-01-24  edgar/data/320193/0001193125-12-023398.txt
8-K/A       SOME VERY LONG COMPANY NAME THAT GOES ON AND ON HOLDINGS INC  99         2012-03-30  edgar/data/99/0000000099-12-000007.txt
"""

HEADERS = """<HTML><HEAD><!--
<SEC-HEADER>0000320193-26-000024.hdr.sgml : 20261102
<ACCEPTANCE-DATETIME>20261102163023
<TYPE>8-K
<ITEMS>2.02
<ITEMS>9.01
</SEC-HEADER>
--></HEAD><BODY><PRE>&lt;ACCEPTANCE-DATETIME&gt;20261102163023
&lt;DOCUMENT&gt;
&lt;TYPE&gt;8-K
&lt;FILENAME&gt;aapl-20261102.htm
&lt;DESCRIPTION&gt;8-K
&lt;/DOCUMENT&gt;
&lt;DOCUMENT&gt;
&lt;TYPE&gt;EX-99.1
&lt;FILENAME&gt;a8-kex991.htm
&lt;DESCRIPTION&gt;EX-99.1
&lt;/DOCUMENT&gt;
</PRE></BODY></HTML>"""


def test_parse_form_idx():
    rows = parse_form_idx(FORM_IDX, {"8-K", "8-K/A"})
    assert [r["form"] for r in rows] == ["8-K", "8-K/A"]
    assert rows[0]["cik"] == 320193 and rows[0]["accession"] == "0001193125-12-023398"
    assert rows[0]["date_filed"] == date(2012, 1, 24) and rows[0]["company"] == "APPLE INC"
    assert rows[1]["company"].startswith("SOME VERY LONG") and rows[1]["cik"] == 99


def test_parse_index_headers_and_exhibit():
    h = parse_index_headers(HEADERS)
    assert h["acceptance_et"] == datetime(2026, 11, 2, 16, 30, 23)
    assert h["items"] == ["2.02", "9.01"] and h["form"] == "8-K"
    assert [d["type"] for d in h["documents"]] == ["8-K", "EX-99.1"]
    assert pick_exhibit(h["documents"])["filename"] == "a8-kex991.htm"


def test_pick_exhibit_variants():
    docs = [{"type": "8-K", "filename": "a"}, {"type": "EX-99.2", "filename": "b"}, {"type": "EX-99", "filename": "c"}]
    assert pick_exhibit(docs)["filename"] == "c"           # EX-99 preferred over EX-99.2
    assert pick_exhibit([{"type": "EX-99.2", "filename": "b"}])["filename"] == "b"
    assert pick_exhibit([{"type": "EX-10.1", "filename": "x"}]) is None


def test_et_to_utc_dst():
    assert et_to_utc(datetime(2026, 11, 2, 16, 30, 23)) == datetime(2026, 11, 2, 21, 30, 23)  # EST
    assert et_to_utc(datetime(2026, 7, 15, 16, 5)) == datetime(2026, 7, 15, 20, 5)            # EDT


def test_classify_session():
    assert classify_session(datetime(2026, 11, 2, 7, 0)) == "pre_market"
    assert classify_session(datetime(2026, 11, 2, 9, 29, 59)) == "pre_market"
    assert classify_session(datetime(2026, 11, 2, 9, 30)) == "intraday"
    assert classify_session(datetime(2026, 11, 2, 15, 59, 59)) == "intraday"
    assert classify_session(datetime(2026, 11, 2, 16, 0)) == "after_close"
    assert classify_session(datetime(2026, 11, 7, 10, 0)) == "non_trading_day"   # Saturday


def test_html_to_text():
    t = html_to_text("<html><body><p>Revenue was <b>$12.3</b> billion, up 8%.</p>"
                     "<table><tr><td>EPS</td><td>$1.20</td></tr></table><script>x</script></body></html>")
    assert "Revenue was $12.3 billion, up 8%." in t and "EPS\t$1.20" in t and "x" not in t.split("\n")


def test_misc():
    assert accession_from_path("edgar/data/1/0000000001-12-000001.txt") == "0000000001-12-000001"
    assert yahoo_symbol("BRK.B") == "BRK-B" and yahoo_symbol("brk-b") == "BRK-B"
    assert quarters(2025, date(2026, 5, 1))[-1] == (2026, 2) and len(quarters(2025, date(2026, 5, 1))) == 6


INDEX_HTM = """<html><body><div id="formDiv"><div id="formHeader"><div id="formName"><strong>Form 8-K</strong> - Current report:</div></div>
<div class="formContent">
<div class="formGrouping"><div class="infoHead">Filing Date</div><div class="info">2012-10-29</div>
<div class="infoHead">Accepted</div><div class="info">2012-10-29 06:06:49</div></div>
<div class="formGrouping"><div class="infoHead">Items</div><div class="info">Item 2.02: Results of Operations and Financial Condition<br>Item 7.01: Regulation FD Disclosure<br>Item 9.01: Financial Statements and Exhibits</div></div>
</div>
<table class="tableFile" summary="Document Format Files"><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>FORM 8-K</td><td><a href="/x/v326782_8k.htm">v326782_8k.htm</a></td><td>8-K</td><td>19088</td></tr>
<tr><td>2</td><td>EXHIBIT 99.1</td><td><a href="/x/v326782_ex99-1.htm">v326782_ex99-1.htm</a></td><td>EX-99.1</td><td>36606</td></tr>
</table></div></body></html>"""


def test_parse_filing_index_htm():
    from ingest.us.parse import parse_filing_index
    h = parse_filing_index(INDEX_HTM)
    assert h["acceptance_et"] == datetime(2012, 10, 29, 6, 6, 49)
    assert h["items"] == ["2.02", "7.01", "9.01"] and h["form"] == "8-K"
    assert pick_exhibit(h["documents"])["filename"] == "v326782_ex99-1.htm"
    assert classify_session(h["acceptance_et"]) == "pre_market"
