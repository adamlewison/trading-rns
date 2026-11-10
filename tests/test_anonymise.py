"""Anonymiser: company names, tickers and web addresses go; ordinary words stay."""
from classify.anonymise import anonymise, name_variants

RELEASE = """APPLE INC. REPORTS FIRST QUARTER RESULTS
CUPERTINO, California -- Apple Inc. (NASDAQ: AAPL) today announced results. Apple's revenue rose.
Applebee's is unaffected. Visit www.apple.com/investor or email ir@apple.com. Tim Cook said AAPL holders..."""


def test_names_tickers_urls_removed():
    out = anonymise(RELEASE, ["Apple Inc.", "APPLE COMPUTER INC /CA/"], ["AAPL"])
    assert "Apple" not in out.replace("Applebee", "")
    assert "APPLE" not in out and "AAPL" not in out and "apple.com" not in out
    assert "(NASDAQ: [TICKER])" in out and "[COMPANY] REPORTS" in out
    assert "Applebee's" in out and "Tim Cook" in out           # neighbours and people stay


def test_common_first_words_and_lowercase_single_words_survive():
    v = name_variants(["First Solar, Inc.", "Target Corp"])
    assert "First" not in v and "First Solar" in v and "Target" in v
    out = anonymise("Target Corporation beat its sales target. First quarter at First Solar.",
                    ["Target Corp", "First Solar, Inc."], ["TGT", "FSLR"])
    assert out == "[COMPANY] beat its sales target. First quarter at [COMPANY]."


def test_short_tickers_only_in_ticker_context():
    out = anonymise("Agilent (NYSE: A) said A strong year. (A) closes.", ["Agilent Technologies Inc"], ["A"])
    assert out == "[COMPANY] (NYSE: [TICKER]) said A strong year. ([TICKER]) closes."


def test_capitalised_forms_of_upper_case_names():
    out = anonymise("Medallion Financial and CoStar reported; MEDALLION rose.",
                    ["MEDALLION FINANCIAL CORP", "COSTAR GROUP INC"], ["MFIN", "CSGP"])
    assert out == "[COMPANY] and [COMPANY] reported; [COMPANY] rose."


def test_8k_cover_page_phone_and_ids_removed():
    doc = ("FORM 8-K\n(Exact name of registrant as specified in its charter)\nDelaware 1-6903 75-0225040\n"
           "2525 N. Stemmons Freeway, Dallas, Texas\nRegistrant's telephone number: 214-631-4420\n"
           "Item 2.02 Results of Operations. Revenue rose. Call (214) 631-4420.")
    out = anonymise(doc, ["Trinity Industries Inc"], ["TRN"])
    assert out.startswith("Item 2.02") and "Stemmons" not in out and "631-4420" not in out


def test_wire_service_ticker_tags():
    out = anonymise("2024--FICO (NYSE:FICO) results (FICO-F) FICOF", ["Fair Isaac Corp"], ["FICO"])
    assert "FICO" not in out


def test_acronyms_and_filenames():
    out = anonymise("uei-20190502ex991.htm\nUniversal Electronics Inc. (UEI) said UEI's sales rose.",
                    ["UNIVERSAL ELECTRONICS INC"], ["UEIC"])
    assert out.replace("[COMPANY].", "[COMPANY]") == "[FILE]\n[COMPANY] ([COMPANY]) said [COMPANY] sales rose."


def test_unknown_old_ticker_with_exchange():
    assert anonymise("Vical (Nasdaq:VICL) and (NYSE: BRK.B)", ["Brickell Biotech"], ["BBI"]) == \
        "Vical (Nasdaq:[TICKER]) and (NYSE: [TICKER])"


def test_hyphenated_form_of_a_spaced_name():
    """2026-11-10: EDGAR says "SHERWIN WILLIAMS CO", the release says "The Sherwin-Williams Company"."""
    out = anonymise("The Sherwin-Williams Company reports; Sherwin Williams rose.", ["SHERWIN WILLIAMS CO"], ["SHW"])
    assert "Sherwin" not in out and "Williams" not in out
