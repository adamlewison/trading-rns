import pandas as pd
import pytest

from paper.t212 import START_USD, allocate, net_orders, plan, ticker_map


def test_ticker_map_us_stocks_only():
    m = ticker_map([
        {"ticker": "AAPL_US_EQ", "type": "STOCK", "currencyCode": "USD", "shortName": "AAPL"},
        {"ticker": "BRK_B_US_EQ", "type": "STOCK", "currencyCode": "USD", "shortName": "BRK.B"},
        {"ticker": "TPR_EQ_US", "type": "STOCK", "currencyCode": "USD", "shortName": "TPR"},
        {"ticker": "FB_US_EQ", "type": "STOCK", "currencyCode": "USD", "shortName": "META"},
        {"ticker": "VODl_EQ", "type": "STOCK", "currencyCode": "GBX", "shortName": "VOD"},
        {"ticker": "SPY_US_EQ", "type": "ETF", "currencyCode": "USD", "shortName": "SPY"},
    ])
    assert m == {"AAPL": "AAPL_US_EQ", "BRK-B": "BRK_B_US_EQ", "TPR": "TPR_EQ_US", "META": "FB_US_EQ", "FB": "FB_US_EQ"}


def test_plan_first_month_equal_weight():
    picks = pd.DataFrame({"variant": ["Q"] * 30, "ticker": [f"T{i}" for i in range(30)]})
    close = pd.Series({f"T{i}": 100.0 for i in range(30)})
    p = plan(pd.DataFrame(columns=["variant", "ticker", "shares"]), {}, picks, close)
    assert len(p) == 30 and p.delta.iloc[0] == pytest.approx(START_USD / 30 / 100)


def test_plan_sells_dropped_and_rebalances_value():
    held = pd.DataFrame({"variant": ["Q", "Q"], "ticker": ["A", "B"], "shares": [10.0, 10.0]})
    picks = pd.DataFrame({"variant": ["Q"], "ticker": ["A"]})
    close = pd.Series({"A": 30.0, "B": 30.0})
    p = plan(held, {"Q": 0.0}, picks, close).set_index("ticker")
    assert p.at["B", "delta"] == -10.0                       # dropped name sold
    assert p.at["A", "delta"] == pytest.approx(600 / 30 / 30 - 10)   # value 600 spread over 30 slots


def test_net_and_allocate_scale_by_fill():
    intents = pd.DataFrame({"variant": ["Q", "QVM", "V", "V"], "ticker": ["A", "A", "A", "B"],
                            "delta": [2.0, 3.0, -1.0, -0.5], "ref_price": [10.0, 10.0, 10.0, 20.0]})
    n = net_orders(intents, {"A": "A_US_EQ", "B": "B_US_EQ"})
    assert list(n.ticker) == ["B", "A"] and n.qty.tolist() == [-0.5, 4.0]   # sells first
    orders = pd.DataFrame({"ticker": ["A", "B"], "qty": [4.0, -0.5], "filled_qty": [2.0, None],
                           "fill_price": [11.0, None]})
    b = allocate(intents, orders)
    a = b[b.ticker == "A"]
    assert a.shares.sum() == pytest.approx(2.0)              # books add up to what the account bought
    assert (a.price == 11.0).all() and b[b.ticker == "B"].shares.iloc[0] == 0


def test_allocate_netted_ticker_books_in_full_at_reference():
    intents = pd.DataFrame({"variant": ["Q", "V"], "ticker": ["A", "A"], "delta": [1.0, -1.0], "ref_price": [10.0, 10.0]})
    b = allocate(intents, pd.DataFrame(columns=["ticker", "qty", "filled_qty", "fill_price"]))
    assert b.shares.tolist() == [1.0, -1.0] and set(b.source) == {"netted"}
