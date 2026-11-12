import numpy as np
import pandas as pd

from backtest import leveraged_trend as lt


def test_two_x_fund_doubles_daily_moves_less_financing_and_fee():
    idx = pd.bdate_range("2020-01-01", periods=3)
    spy = pd.Series([100.0, 101.0, 99.99], index=idx)
    rf = pd.Series(0.02, index=idx)
    r = lt.fund_returns(spy, rf, 2.0)
    assert abs(r.iloc[1] - (0.02 - (0.02 + lt.SWAP_SPREAD) / 252 - lt.FUND_FEE / 252)) < 1e-12


def test_signal_is_used_two_closes_later():
    idx = pd.bdate_range("2020-01-01", periods=300)
    spy = pd.Series(np.r_[np.linspace(100, 200, 250), np.linspace(200, 100, 50)], index=idx)
    s = lt.signal(spy, "daily")
    raw = (spy > spy.rolling(200).mean()).astype(float)
    assert (s.iloc[2:].values == raw.iloc[:-2].values).all()
