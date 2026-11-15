import numpy as np
import pandas as pd

from backtest import uk_retail_offers as r

DATES = pd.bdate_range("2024-03-04", periods=5).to_numpy().astype("datetime64[D]")   # Mon..Fri


def test_last_known_close_depends_on_the_time_of_day():
    assert r.known_and_next(DATES, pd.Timestamp("2024-03-05 17:00")) == (1, 2)   # after the close: Tuesday known
    assert r.known_and_next(DATES, pd.Timestamp("2024-03-05 07:00")) == (0, 1)   # pre-open: Monday known
    assert r.known_and_next(DATES, pd.Timestamp("2024-03-05 11:00")) == (0, 1)   # intraday: still Monday
    assert r.known_and_next(DATES, pd.Timestamp("2024-03-09 12:00")) == (4, None)  # weekend, no later day


def test_adverse_allocation():
    assert r.adverse_fill(100.0, 100.0, 0.05) == 1.0          # price at the issue price: filled in full
    assert r.adverse_fill(103.0, 100.0, 0.05) == 1.0          # boundary: exactly 3% above still full
    assert r.adverse_fill(103.1, 100.0, 0.05) == 0.25         # popular offer: scaled back
    assert r.adverse_fill(None, 100.0, 0.05) == 0.25 and r.adverse_fill(None, 100.0, -0.05) == 1.0


def test_cluster_t():
    x = pd.Series([0.1, 0.1, -0.3, 0.1])
    assert abs(r.tstat(x, pd.Series(["a", "a", "b", "c"]))) > 0
    assert np.isnan(r.tstat(pd.Series([0.1, 0.1]), pd.Series(["a", "b"])))


def test_account_caps_outstanding_applications():
    o = pd.DataFrame({"launch": pd.to_datetime(["2024-01-01"] * 8), "sale_day": pd.to_datetime(["2024-02-01"] * 8),
                      "net_open": 0.1, "fill_open": 1.0, "bench": 0.0})
    made, idx, n = r.account(o, "open", True)
    assert n == 6 and np.isclose(made, 6 * 500 * 0.1)
