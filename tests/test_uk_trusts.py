import pandas as pd

from features import uk_trusts as t


def test_actual_close_undoes_later_splits_only():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    # a 5-for-1 split takes effect on day 3: Yahoo shows earlier closes divided by 5
    df = pd.DataFrame({"Close": [20.0, 20.0, 20.0, 21.0, 22.0], "Stock Splits": [0, 0, 0, 5.0, 0]}, index=idx)
    a = t.actual_close(df)
    assert a.tolist() == [100.0, 100.0, 100.0, 21.0, 22.0]
    # a 1-for-10 consolidation on day 2
    df = pd.DataFrame({"Close": [50.0, 50.0, 55.0], "Stock Splits": [0, 0, 0.1]}, index=idx[:3])
    assert [round(x, 6) for x in t.actual_close(df)] == [5.0, 5.0, 55.0]
