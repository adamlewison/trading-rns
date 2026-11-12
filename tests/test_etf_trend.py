import numpy as np
import pandas as pd

from backtest import etf_trend as et


def _prices(n=600):
    idx = pd.bdate_range("2010-01-01", periods=n)
    cols = ["SPY", "EFA", "IEF", "VNQ", "DBC", "AGG", "SHY", "QQQ", "IWM", "EEM", "GLD", "TLT"]
    p = pd.DataFrame(100.0, index=idx, columns=cols)
    p["SPY"] = 100 * 1.001 ** np.arange(n)              # steady uptrend
    return p


def test_buy_and_hold_matches_the_asset_less_one_entry_cost():
    p = _prices()
    v = et.simulate(p, "SPY", p.index[0], p.index[-1])
    expected = (p.SPY.iloc[-1] / p.SPY.iloc[0]) * (1 - et.COST_SIDE)
    assert abs(v.iloc[-1] - expected) < 1e-9


def test_timing_holds_spy_in_an_uptrend_and_signals_use_month_ends_only():
    p = _prices()
    m = p.loc[et.month_ends(p)]
    w = et.weights(m, "V4")
    assert (w.SPY.iloc[10:] == 1).all() and (w.SHY.iloc[10:] == 0).all()
