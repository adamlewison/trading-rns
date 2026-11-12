import pandas as pd

from ingest.us.splits import split_rows


def test_split_rows_keeps_real_splits_and_reverse_splits():
    s = pd.Series([4.0, 0.0, 1.0, 1 / 3], index=pd.to_datetime(
        ["2020-08-31", "2021-01-01", "2022-01-01", "2016-10-06"]))
    rows = split_rows("X", s)
    assert [(r[1].isoformat(), round(r[2], 4)) for r in rows] == [("2020-08-31", 4.0), ("2016-10-06", 0.3333)]
