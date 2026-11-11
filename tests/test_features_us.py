"""Phase 3 building blocks: trade timing and the pre-registered exit rules."""
import numpy as np
import pandas as pd

from features.us_earnings import bracket_exit, stop_exit, trade_dates

CAL = np.array(["2024-03-01", "2024-03-04", "2024-03-05"], dtype="datetime64[D]")   # Fri, Mon, Tue


def test_trade_date_pre_market_same_day_after_close_next_day_weekend_skipped():
    acc = pd.Series(pd.to_datetime(["2024-03-01 07:00", "2024-03-01 16:30", "2024-03-02 08:00", "2024-03-04 09:45"]))
    ses = pd.Series(["pre_market", "after_close", "pre_market", "pre_market"])
    got = trade_dates(acc, ses, CAL).dt.strftime("%m-%d").tolist()
    # Fri 07:00 -> Fri open; Fri 16:30 -> Mon; Sat 08:00 -> Mon; a "pre-market" stamp after 09:30 -> next day
    assert got == ["03-01", "03-04", "03-04", "03-05"]


def test_bracket_target_stop_gap_and_timeout():
    e = 100.0
    # day 1 touches +5% -> +5%
    assert bracket_exit(e, [100], [106], [99], [104], 0.05, -0.03, 3) == (0.05, 1)
    # day 1 range touches both -> stop assumed first
    assert bracket_exit(e, [100], [106], [96], [104], 0.05, -0.03, 3) == (-0.03, 1)
    # day 2 gaps down through the stop -> filled at the open (worse than -3%)
    r, d = bracket_exit(e, [100, 90], [101, 91], [99, 89], [100, 90], 0.05, -0.03, 3)
    assert d == 2 and abs(r - (-0.10)) < 1e-12
    # nothing hit in 3 days -> close of day 3
    r, d = bracket_exit(e, [100, 101, 102], [102, 103, 104], [99, 99, 99], [101, 102, 103], 0.05, -0.03, 3)
    assert d == 3 and abs(r - 0.03) < 1e-12


def test_stop_exit_holds_until_stop_or_end():
    o, l, c = [100] * 20, [95] * 20, [101] * 20
    assert stop_exit(100, o, l, c, -0.08, 20) == (c[-1] / 100 - 1, 20)
    l2 = [95] * 5 + [91] + [95] * 14
    assert stop_exit(100, o, l2, c, -0.08, 20) == (-0.08, 6)
