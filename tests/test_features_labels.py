import pandas as pd

from features.us_earnings_labels import guidance_changes, period_key, split_factor


def test_period_key():
    assert period_key("FY2021") == "FY2021" == period_key("fiscal year 2021") == period_key("Full year 2021")
    assert period_key("FY'21") == "FY2021" and period_key("FY21") == "FY2021"
    assert period_key("Q3 2018") == "Q3-2018" == period_key("third quarter of fiscal 2018") == period_key("3Q 2018")
    assert period_key("second half of 2020") is None and period_key("next year") is None and period_key(None) is None


def test_guidance_change_vs_previous_same_period_and_basis():
    t = pd.to_datetime(["2021-02-01", "2021-05-01", "2021-08-01", "2021-11-01"])
    df = pd.DataFrame({"accession": list("abcd"), "cik": 1, "acceptance_et": t,
                       "guidance_period": ["FY2021", "fiscal 2021", "FY 2021", "FY2022"],
                       "guidance_eps_basis": ["gaap", "gaap", "adjusted", "gaap"],
                       "guidance_eps_low": [2.0, 2.2, 2.4, 3.0], "guidance_eps_high": [2.2, 2.4, 2.6, 3.0],
                       "guidance_revenue_low_m": [100, 110, None, None], "guidance_revenue_high_m": [100, 110, None, None]})
    g = guidance_changes(df).set_index("accession")
    assert g.loc["a", "guid_is_first"] and pd.isna(g.loc["a", "guid_eps_chg"])
    assert abs(g.loc["b", "guid_eps_chg"] - (2.3 - 2.1) / 2.1) < 1e-9 and abs(g.loc["b", "guid_rev_chg"] - 0.1) < 1e-9
    assert pd.isna(g.loc["c", "guid_eps_chg"])            # basis changed: not comparable
    assert g.loc["d", "guid_is_first"]                     # a new fiscal year starts fresh


def test_split_factor_undoes_later_splits_only():
    splits = pd.DataFrame({"ticker": ["X", "X"], "date": pd.to_datetime(["2020-08-31", "2014-06-09"]), "ratio": [4.0, 7.0]})
    ev = pd.DataFrame({"ticker": ["X", "X", "Y"], "d": pd.to_datetime(["2012-01-03", "2015-01-02", "2015-01-02"])})
    assert split_factor(splits, ev, "d").tolist() == [28.0, 4.0, 1.0]
    ev["d2"] = pd.to_datetime(["2016-01-01", "2021-01-01", "2021-01-01"])
    assert split_factor(splits, ev, "d", "d2").tolist() == [7.0, 4.0, 1.0]
