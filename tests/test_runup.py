import numpy as np
import pandas as pd

from features.us_runup import reaction_index


def test_reaction_day_after_close_is_next_session_pre_market_same_day():
    cal = pd.bdate_range("2024-03-01", periods=6).to_numpy().astype("datetime64[D]")   # Fri..Fri
    when = pd.Series(pd.to_datetime(["2024-03-01 16:05", "2024-03-01 07:00", "2024-03-02 09:00"]))
    ses = pd.Series(["after_close", "pre_market", "non_trading_day"])
    got = reaction_index(cal, when, ses)
    assert [str(cal[k]) for k in got] == ["2024-03-04", "2024-03-01", "2024-03-04"]
