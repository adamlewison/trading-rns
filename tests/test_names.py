from ingest.names import normalise_name, tidm_to_yahoo


def test_tidm_to_yahoo():
    assert tidm_to_yahoo("DSG") == "DSG.L"
    assert tidm_to_yahoo("BT.A") == "BT-A.L"
    assert tidm_to_yahoo("RR.") == "RR.L"
    assert tidm_to_yahoo(" ba. ") == "BA.L"


def test_normalise_name_matches_across_sources():
    a = normalise_name("DILLISTONE GROUP PLC")
    assert a == normalise_name("Dillistone Group") == normalise_name("Dillistone Group Plc (DI)")
    assert normalise_name("Landore Resources Limited NPV") == "LANDORE RESOURCES"
    assert normalise_name("M&C Saatchi plc") == normalise_name("M & C SAATCHI PLC")
    assert normalise_name(None) == "" and normalise_name(float("nan")) == ""
