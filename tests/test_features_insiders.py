import numpy as np

from features.us_insiders import trail_exit


def test_trailing_stop_sells_next_open_after_close_breach():
    c = np.array([10, 11, 12, 13, 11.4, 11, 11], float)
    o = c - 0.1
    # ATR 0.5 x 3 = 1.5: highest close 13 -> stop 11.5; day 4 closes 11.4 -> sell day 5 open
    assert trail_exit(o, c, c, c, 0, len(c), 0.5, max_d=6) == (5, o[5], "open")


def test_trailing_stop_runs_to_the_time_limit_and_needs_the_full_window():
    c = np.arange(10, 20, dtype=float)
    assert trail_exit(c, c, c, c, 0, len(c), 0.5, max_d=5) == (4, c[4], "close")
    assert trail_exit(c, c, c, c, 6, len(c), 0.5, max_d=5) is None


def test_spread_table_bands():
    from backtest.us_insiders import spread_table
    assert spread_table(np.array([1e9, 2e8, 5e7, 1e7, 3e6])).tolist() == [0.0005, 0.001, 0.002, 0.004, 0.008]
