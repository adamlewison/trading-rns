import pandas as pd

from ingest.shared.prices import flag_prices


def _df(closes, vols=None, opens=None):
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n).date,
        "open": opens or closes, "high": [c * 1.02 for c in closes], "low": [c * 0.98 for c in closes],
        "close": closes, "volume": vols or [1000] * n,
    })


def test_unit_glitch_flagged():
    df = flag_prices(_df([100, 101, 1.01, 102, 103]))
    assert df["is_suspect"].tolist() == [False, False, True, False, False]


def test_persistent_unit_shift_flags_jump_day():
    df = flag_prices(_df([100, 101, 1.01, 1.02, 1.03]))
    assert df["is_suspect"].tolist() == [False, False, True, False, False]


def test_normal_moves_not_flagged():
    df = flag_prices(_df([100, 130, 90, 95, 200]))   # big but plausible small-cap moves
    assert not df["is_suspect"].any()


def test_stale_days():
    d = _df([100, 100, 100], vols=[1000, 0, 1000])
    d.loc[2, ["open", "high", "low"]] = 100
    out = flag_prices(d)
    assert out["is_stale"].tolist() == [False, True, True]


def test_close_outside_range_is_not_suspect():
    d = _df([100, 101, 102])
    d.loc[1, "close"] = 104   # LSE mid/auction close above traded high: normal for small caps
    assert not flag_prices(d)["is_suspect"].any()
