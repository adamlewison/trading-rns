import numpy as np
import pandas as pd
import pytest

from backtest import us_account as ua


def test_costs_per_broker_at_150():
    p, s = np.array([50.0]), np.array([0.001])
    # Trading 212: 2 x (0 + 0.15% fx + 0.15% slippage + 0.05% half spread)
    assert abs(ua.round_trip("trading212", p, s)[0] - 2 * (0.0015 + 0.0015 + 0.0005)) < 1e-12
    # IBKR fixed: 3 shares -> $0.015 -> $1 minimum (below the 1% = $1.50 cap) -> 0.667% a side
    assert abs(ua.round_trip("ibkr_fixed", p, s)[0] - 2 * (1 / 150 + 0.0003 + 0.0010 + 0.0005)) < 1e-12


def test_account_takes_best_first_and_skips_when_full():
    t = pd.to_datetime(["2020-01-02"] * 3 + ["2020-01-03", "2020-02-03"])
    x = pd.DataFrame({"trade_date": t, "pred": [0.1, 0.3, 0.2, 0.5, 0.1],
                      "exit_date": t + pd.offsets.BDay(10)})
    got = ua.simulate(x, slots=2)
    assert got[got.taken].pred.tolist() == [0.3, 0.2, 0.1]     # best two on day 1; full on day 2; free again in Feb


def test_final_locked():
    with pytest.raises(SystemExit, match="once"):
        ua.run("final", final=False)
