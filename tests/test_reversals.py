import numpy as np
import pandas as pd

from features.us_reversals import thin


def test_thin_keeps_one_event_per_stock_within_5_trading_days():
    cal = pd.bdate_range("2024-01-01", periods=30).to_numpy().astype("datetime64[D]")
    d = pd.DataFrame({"ticker": ["A"] * 4 + ["B"],
                      "date": pd.to_datetime([cal[0], cal[3], cal[6], cal[20], cal[3]])})
    kept = thin(d, cal)
    assert kept[kept.ticker == "A"].date.tolist() == [pd.Timestamp(cal[0]), pd.Timestamp(cal[6]), pd.Timestamp(cal[20])]
    assert len(kept[kept.ticker == "B"]) == 1
