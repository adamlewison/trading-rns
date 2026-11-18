import numpy as np

from backtest import betfair_racing as b


def test_harville_place_probabilities_sum_to_places_and_order_is_kept():
    p = np.array([0.4, 0.3, 0.2, 0.1])
    two = b.harville_place(p, 2)
    assert np.isclose(two.sum(), 2.0) and np.all(np.diff(two) < 0)
    # runner 0 in top 2: 0.4 + 0.3*0.4/0.7 + 0.2*0.4/0.8 + 0.1*0.4/0.9
    assert np.isclose(two[0], 0.4 + 0.3 * 0.4 / 0.7 + 0.2 * 0.4 / 0.8 + 0.1 * 0.4 / 0.9)
    p6 = np.array([0.3, 0.25, 0.2, 0.12, 0.08, 0.05])
    assert np.isclose(b.harville_place(p6, 3).sum(), 3.0)


def test_returns_per_pound_with_commission():
    assert np.allclose(b.back_ret(np.array([3.0, 3.0]), np.array([True, False]), 0.05), [1.9, -1.0])
    # lay at 5.0 with £1 liability: stake 0.25; wins 0.25 x 0.95 when the runner loses
    assert np.allclose(b.lay_ret(np.array([5.0, 5.0]), np.array([False, True]), 0.05), [0.2375, -1.0])
