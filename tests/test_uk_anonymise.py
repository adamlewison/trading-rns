from types import SimpleNamespace as NS

from classify.uk_news import text_for


def test_former_names_short_names_and_uk_tickers_are_hidden():
    r = NS(company="Star Energy Group Plc", uni_name="STAR ENERGY GROUP PLC", tidm="STAR", headline="Results",
           body_text='IGas Energy plc (AIM: IGAS) results. IGas Energy plc ("IGas" or "the Company"). IGas grew.')
    t, cut = text_for(r)
    assert "IGas" not in t and "IGAS" not in t and "the Company" in t and not cut
