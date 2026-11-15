import numpy as np
import pandas as pd

from backtest import us_tax_loss as t


def test_pick_worst_and_x80_drops_deep_falls():
    u = pd.DataFrame({"ticker": [f"T{i:02d}" for i in range(40)], "ytd": np.linspace(-0.95, 0.5, 40)})
    w = t.pick(u, "W30")
    assert len(w) == 30 and w.ytd.iloc[0] == -0.95
    x = t.pick(u, "X80")
    assert x.ytd.min() >= -0.80 and len(x) == 30
    assert t.pick(pd.DataFrame({"ticker": ["A"], "ytd": [-0.80]}), "X80").ticker.tolist() == ["A"]   # boundary kept


def test_costs():
    c = t.round_trip(np.array([3e6, 5e7, 2e8, 1e9]))
    assert np.allclose(c, [0.006 + 0.006, 0.006 + 0.002, 0.006 + 0.001, 0.006 + 0.0005])


def test_dates_and_period_split():
    cal = pd.bdate_range("2019-01-01", "2020-02-28")
    d = t.year_dates(cal, 2019)
    dec = cal[(cal.year == 2019) & (cal.month == 12)]
    assert d["S"] == dec[-6] and d["entry"] == dec[-5] and d["S"] < d["entry"]
    assert d["exit10"] == cal[(cal.year == 2020) & (cal.month == 1)][9]
    assert not set(t.CHOOSE_YEARS) & set(t.FINAL_YEARS) and max(t.CHOOSE_YEARS) == 2020
