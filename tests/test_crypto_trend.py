import numpy as np
import pandas as pd

from backtest import crypto_trend as c


def _px(vals):
    return pd.DataFrame({"BTC-USD": vals}, index=pd.date_range("2020-01-01", periods=len(vals), freq="D"))


def test_trade_happens_one_day_after_the_signal():
    px = _px([100.0] * 5 + [200.0] * 5)
    tgt = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    tgt.iloc[3:] = 0.5                       # signal known at the close of day 3
    sim = c.simulate(px, tgt, str(px.index[1].date()))
    assert sim.traded.iloc[:3].sum() == 0    # days 1-3: nothing
    assert sim.traded.loc[px.index[4]] == 3000.0          # bought at day 4's close (price 100)
    cost = 3000 * c.COST
    assert np.isclose(sim.equity.loc[px.index[4]], 6000 - cost)
    # day 5: the price doubles, the weight drifts to about 2/3, beyond the band, so it is cut back to 1/2
    before = 6000 - cost + 3000
    sold = (6000 / before - 0.5) * before
    assert np.isclose(sim.equity.loc[px.index[5]], before - sold * c.COST)


def test_band_stops_small_rebalances_and_flip_sells():
    px = _px([100.0] * 4 + [105.0] * 3)
    tgt = pd.DataFrame(0.5, index=px.index, columns=px.columns)
    tgt.iloc[5:] = 0.0
    sim = c.simulate(px, tgt, str(px.index[1].date()))
    assert (sim.traded > 0).sum() == 2       # the first buy, then the sell after the flip; no drift trades
    assert sim.invested.iloc[-1] < 1e-9


def test_targets_use_only_past_and_need_history():
    vals = list(np.linspace(100, 300, 130))
    t = c.targets(_px(vals), "M100", "F")
    assert t.iloc[:99].sum().sum() == 0 and t.iloc[99, 0] == 0.5
    changed = vals[:120] + [1.0] * 10        # changing the future must not change earlier targets
    t2 = c.targets(_px(changed), "M100", "F")
    assert t.iloc[:120].equals(t2.iloc[:120])
    v = c.targets(_px(vals), "R28", "V")
    assert (v.values <= c.MAX_W + 1e-12).all()


def test_periods_do_not_overlap():
    assert c.CHOOSE[1] < c.FINAL_START
