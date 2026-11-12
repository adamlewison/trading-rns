import numpy as np
import pytest

from backtest import us_earnings as bt


def test_corwin_schultz_zero_for_flat_and_positive_for_bouncing_prices():
    assert bt.corwin_schultz([10] * 10, [10] * 10) == 0
    rng = np.random.default_rng(1)
    mid = 100 * np.exp(np.cumsum(rng.normal(0, 0.005, 20)))
    s = bt.corwin_schultz(mid * 1.01, mid * 0.99)     # a 2% high-low band around a slow walk
    assert 0.005 < s < 0.04


def test_round_trip_cost_commission_capped_at_1pct_and_min_1usd():
    price, spread = np.array([50.0]), np.array([0.0])
    # $30: commission capped at 1% each side -> 2% + fx 0.06% + slippage 0.2%
    assert abs(bt.round_trip_cost(30, price, spread)[0] - (0.02 + 0.0006 + 0.002)) < 1e-12
    # $500 at $50 = 10 shares -> $0.05 -> $1 minimum = 0.2% each side
    assert abs(bt.round_trip_cost(500, price, spread)[0] - (0.004 + 0.0006 + 0.002)) < 1e-12
    assert bt.round_trip_cost(500, price, np.array([0.01]), 2.0)[0] > bt.round_trip_cost(500, price, np.array([0.01]))[0]


def test_final_years_locked_without_flag():
    with pytest.raises(SystemExit, match="once"):
        bt.run("final", final=False)


def test_close_entry_holds_from_day1_close_and_rejects_bad_or_short_windows():
    o = np.ones(30); c = np.arange(1, 31, dtype=float); bad = np.zeros(30, bool)
    r, ok = bt.close_entry(o, c, bad, 2, 30, 20)
    assert ok and abs(r - (23 / 3 - 1)) < 1e-12           # close of day index 2 -> close of index 22
    assert not bt.close_entry(o, c, bad, 15, 30, 20)[1]    # runs past the data
    bad[10] = True
    assert not bt.close_entry(o, c, bad, 2, 30, 20)[1]     # stale bar inside the window
