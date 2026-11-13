import pandas as pd

from paper.factors import is_first_trading_day_of_month, status_text


def test_first_trading_day_of_month():
    cal = pd.DatetimeIndex(pd.to_datetime(["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03"]))
    assert is_first_trading_day_of_month(cal, pd.Timestamp("2026-11-02"))
    assert not is_first_trading_day_of_month(cal, pd.Timestamp("2026-11-03"))
    assert not is_first_trading_day_of_month(cal, pd.Timestamp("2026-11-01"))     # weekend


def test_status_compounds_months_per_portfolio():
    m = pd.DataFrame({"variant": ["Q", "Q"], "entry": ["2026-12-01", "2027-01-04"], "until": ["2027-01-04", "2027-02-01"],
                      "ret": [0.10, -0.05], "spy": [0.02, 0.01], "open": [False, True]})
    t = status_text(m)
    assert "| Q | +4.5% | +3.0% | +1.5% | 2 |" in t
