import numpy as np
import pandas as pd

from backtest.uk_news import round_trip, spread_uk


def test_uk_costs_spread_bands_and_stamp_duty_on_main_market_only():
    assert spread_uk(np.array([6e6, 2e6, 3e5, 6e4])).tolist() == [0.004, 0.008, 0.015, 0.03]
    d = pd.DataFrame({"turnover_20d": [2e6, 2e6], "is_aim": [1, 0]})
    rt = round_trip(d)
    assert abs(rt[0] - (0.008 + 0.005)) < 1e-12 and abs(rt[1] - (0.008 + 0.005 + 0.005)) < 1e-12
