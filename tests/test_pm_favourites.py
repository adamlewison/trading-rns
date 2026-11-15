import gzip
import json

import numpy as np
import pandas as pd

from backtest import pm_favourites as p


def test_fee_rounds_up_and_return_to_the_cent():
    assert p.fee(123, 0.97) == 0.26                      # 0.07*123*0.97*0.03 = 0.2505 -> 0.26
    assert np.isclose(p.bet_return(120, 0.97, 1.0), (123 - 0.26 - 123 * 0.97) / (123 * 0.97))
    assert np.isclose(p.bet_return(120, 0.97, 0.0), (0 - 0.26 - 123 * 0.97) / (123 * 0.97))


def test_cluster_t_is_smaller_when_bets_move_together():
    r = pd.Series([0.03, 0.03, 0.03, 0.03, -1.0, -1.0, 0.03, 0.03])
    same = pd.Series(["a", "a", "a", "a", "b", "b", "c", "c"])
    assert abs(p.tstat(r, same)) < abs(p.tstat(r))


def _write(root, markets, candles):
    (root / "markets").mkdir(parents=True)
    (root / "candles").mkdir()
    (root / "series.json").write_text(json.dumps([{"series": "S", "category": "Test", "fee_multiplier": 1}]))
    with gzip.open(root / "markets" / "m.jsonl.gz", "wt") as f:
        f.writelines(json.dumps(m) + "\n" for m in markets)
    with gzip.open(root / "candles" / "c.jsonl.gz", "wt") as f:
        f.writelines(json.dumps(c) + "\n" for c in candles)


def _market(ticker, end, result="yes", event=None):
    return {"ticker": ticker, "event_ticker": event or ticker, "series": "S", "market_type": "binary",
            "open_time": "2025-01-01T00:00:00Z", "close_time": end, "expected_expiration_time": end,
            "latest_expiration_time": end, "settlement_ts": end, "result": result,
            "settlement_value_dollars": "1.0000" if result == "yes" else "0.0000", "volume_fp": "5000.00",
            "last_price_dollars": "0.99", "can_close_early": False, "status": "finalized", "title": "t"}


def _candle(ticker, t, ask, bid, last=0.5):
    return {"ticker": ticker, "t": t, "ask_o": ask, "ask_c": ask, "bid_o": bid, "bid_c": bid, "last": last, "vol": 1.0}


def test_signal_fill_period_and_one_per_event(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "ROOT", tmp_path)
    e = int(pd.Timestamp("2025-06-10T00:00:00Z").timestamp())
    h = 3600
    markets = [_market("A", "2025-06-10T00:00:00Z"),                       # yes favourite, wins
               _market("B", "2025-06-10T00:00:00Z", "yes"),                # no favourite, loses
               _market("C", "2025-06-10T00:00:00Z", event="A"),            # same event as A, signals later
               _market("D", "2025-06-10T00:00:00Z"),                       # ask jumps 2c before the fill: no trade
               _market("E", "2025-06-10T00:00:00Z"),                       # only inside the last 6 hours: no signal
               _market("F", "2026-03-01T00:00:00Z")]                       # final period: never loaded in choose
    candles = [_candle("A", e - 30 * h, 0.99, 0.98), _candle("A", e - 20 * h, 0.96, 0.94), _candle("A", e - 19 * h, 0.97, 0.95),
               _candle("B", e - 20 * h, 0.08, 0.04), _candle("B", e - 19 * h, 0.08, 0.05),
               _candle("C", e - 10 * h, 0.95, 0.93), _candle("C", e - 9 * h, 0.95, 0.93),
               _candle("D", e - 20 * h, 0.95, 0.93), _candle("D", e - 19 * h, 0.97, 0.93),
               _candle("E", e - 5 * h, 0.95, 0.93), _candle("E", e - 4 * h, 0.95, 0.93),
               _candle("F", e, 0.95, 0.93), _candle("F", e + h, 0.95, 0.93)]
    _write(tmp_path, markets, candles)
    con = p.connect(final=False)
    assert con.execute("SELECT count(*) FROM m WHERE ticker='F'").fetchone()[0] == 0
    b, n = p.make_bets(p.signals(con, p.BANDS["B95"], 2))
    got = b.set_index("ticker")
    assert sorted(got.index) == ["A", "B"] and n["no_fill"] == 1
    assert got.side["A"] == "yes" and np.isclose(got.p_fill["A"], 0.97) and got.payoff["A"] == 1
    assert got.side["B"] == "no" and np.isclose(got.p_fill["B"], 0.95) and got.payoff["B"] == 0 and got.ret["B"] < -1
    assert got.t_signal["A"] == e - 20 * h and got.t_fill["A"] == e - 19 * h
