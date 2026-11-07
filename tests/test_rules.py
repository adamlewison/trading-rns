import pytest

from ingest.announcements.rules import admin_rule, clean_headline, headline_category, is_replacement

ADMIN = [
    "Total Voting Rights", "TVR", "Replacement - Total Voting Rights", "Holding(s) in Company",
    "Form 8.3 - Dialight plc", "Form 8 (OPD) - Target plc", "Transaction in Own Shares",
    "Block listing Interim Review", "Result of AGM", "Result of AGM & GM", "Notice of AGM",
    "Results of Scheme Court Meeting & General Meeting", "Notice of Results",
    "Notification of Full Year Results", "Change of Adviser", "Change of Registered Office",
    "Rule 2.9 Announcement", "Posting of Annual Report and Notice of AGM", "Director Declaration",
    "Price Monitoring Extension", "Notification of Major Holdings",
]
NOT_ADMIN = [
    "Trading Update", "Final Results", "AGM Statement", "Director/PDMR Shareholding",
    "Director Subscription and Total Voting Rights", "Result of AGM and Trading Update",
    "Placing to raise £5m", "Contract Win", "Issue of Equity", "Half-year Report",
    "Possible Offer for the Company", "Update on Change of Strategy & Notice of GM",
]


@pytest.mark.parametrize("h", ADMIN)
def test_admin(h):
    assert admin_rule(h) is not None, h
    assert headline_category(h) == "admin"


@pytest.mark.parametrize("h", NOT_ADMIN)
def test_not_admin(h):
    """Admin filter must be narrow: anything that might carry news stays in the main table."""
    assert admin_rule(h) is None, h


@pytest.mark.parametrize("h,cat", [
    ("Trading Update", "trading_update"), ("Q3 Trading Update", "trading_update"),
    ("Final Results", "results"), ("Interim Results & Investor Presentation", "results"),
    ("Placing and Subscription", "fundraise"), ("Director/PDMR Shareholding", "director_dealing"),
    ("Contract Win", "contract_win"), ("Possible Offer for XYZ plc", "takeover"),
    ("Rule 2.7 Announcement", "takeover"), ("Profit Warning", "profit_warning"),
])
def test_category(h, cat):
    assert headline_category(h) == cat


def test_clean_headline_keeps_meaningful_update():
    assert clean_headline("Trading Update") == "Trading Update"
    assert clean_headline("Final Results - Replacement") == "Final Results"
    assert is_replacement("Replacement: Final Results")
