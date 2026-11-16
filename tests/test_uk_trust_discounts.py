import numpy as np
import pandas as pd

from backtest import uk_trust_discounts as b


def _d(vals, start="2019-01"):
    m = pd.period_range(start, periods=len(vals), freq="M")
    return pd.DataFrame({"tidm": "T", "month": m, "discount": vals})


def test_z_uses_only_the_previous_twelve_months():
    vals = [-0.05, -0.07] * 6 + [-0.12]
    z = b.zscores(_d(vals)).set_index("month").z
    assert z.iloc[:12].isna().all()                                   # needs 12 earlier months
    prev = np.array(vals[:12])
    assert np.isclose(z.iloc[12], (-0.12 - prev.mean()) / prev.std(ddof=1))
    changed = b.zscores(_d(vals[:12] + [-0.30])).set_index("month").z   # a later value never changes earlier z
    assert changed.iloc[:12].isna().all()
    more = b.zscores(_d(vals + [0.5])).set_index("month").z
    assert np.isclose(more.iloc[12], z.iloc[12])


def test_z_needs_every_month_and_some_spread():
    vals = [-0.05, -0.07] * 6 + [-0.12]
    gap = _d(vals).drop(index=5)                                      # one month missing -> no z for a year after
    assert b.zscores(gap).z.isna().all()
    flat = b.zscores(_d([-0.05] * 12 + [-0.20]))                      # no spread in the history -> no z
    assert flat.z.isna().all()


def test_periods_do_not_overlap():
    assert b.CHOOSE[1] < b.FINAL_START
