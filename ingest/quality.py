"""Price data-quality flags (pure functions; tested)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def flag_prices(df: pd.DataFrame, glitch_ratio: float = 30.0) -> pd.DataFrame:
    """Add is_stale / is_suspect to one ticker's daily OHLCV (sorted by date).

    is_stale:   volume == 0 or O=H=L=C (Yahoo fills untraded days with the last price).
    is_suspect: impossible bar (high < low, non-positive price) or a jump of >= glitch_ratio x vs both neighbours that reverses (typical
                Yahoo GBP-vs-GBp 100x unit glitch). Flagged, never silently "fixed".

    NOTE: close (and open) outside [low, high] is NOT flagged. For LSE small caps Yahoo's
    open/close are usually official auction/mid prices while high/low are trade prices,
    so this happens routinely and is not a data error (see ASSUMPTIONS.md).
    """
    df = df.sort_values("date").copy()
    o, h, l, c, v = (df[k] for k in ("open", "high", "low", "close", "volume"))
    df["is_stale"] = (v.fillna(0) <= 0) | ((o == h) & (h == l) & (l == c))
    bad_bar = (h < l) | (c <= 0) | (l <= 0) | c.isna()
    prev, nxt = c.shift(1), c.shift(-1)
    r_prev, r_next = c / prev, c / nxt
    spike = ((r_prev >= glitch_ratio) & (r_next >= glitch_ratio)) | (
        (r_prev <= 1 / glitch_ratio) & (r_next <= 1 / glitch_ratio))
    # persistent level shift (e.g. series switches units for a stretch): flag the jump day
    shift = ((r_prev >= glitch_ratio) | (r_prev <= 1 / glitch_ratio)) & ~spike.shift(1, fill_value=False)
    df["is_suspect"] = (bad_bar | spike | shift).fillna(False).astype(bool)
    return df
