import numpy as np
import pandas as pd

from backtest import decision_forecasts as d


def test_skill_is_zero_when_the_model_copies_the_market_and_positive_when_better():
    y = np.array([1.0, 0, 1, 0, 1, 0])
    q = np.array([0.6, 0.4, 0.6, 0.4, 0.6, 0.4])
    cl = np.array(list("abcdef"))
    assert abs(d.skill_ci(y, q, q, cl)[0]) < 1e-12
    assert d.skill_ci(y, np.where(y == 1, 0.9, 0.1), q, cl)[0] > 0


def test_trades_bet_the_favoured_side_at_its_ask_only_past_the_edge():
    e = pd.DataFrame({"p": [0.80, 0.55, 0.20], "mid": [0.60, 0.50, 0.40], "bid": [0.59, 0.49, 0.39],
                      "ask": [0.61, 0.51, 0.41], "y": [1.0, 1.0, 1.0]})
    r = d.trades(e)
    assert list(r.index) == [0, 2]                       # the 5-point gap is not traded
    n = 10 // 0.61
    assert np.isclose(r[0], (n - n * 0.61 - d.fee(int(n), 0.61)) / (n * 0.61))
    assert r[2] < -0.99                                   # bought No at 0.61, Yes won


def test_market_mode_adds_the_price_and_blind_mode_does_not():
    r = pd.Series({"t_snap": 1793000000, "end": 1793086400, "title": "T", "rules": "R", "bid": 0.30, "ask": 0.34})
    r = type("R", (), r.to_dict())
    assert "32%" in d.input_text(r, "M") and "%" not in d.input_text(r, "B")
