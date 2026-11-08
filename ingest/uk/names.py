"""Ticker and company-name normalisation helpers (pure functions; tested)."""
from __future__ import annotations

import re

_SUFFIXES = [
    "PUBLIC LIMITED COMPANY", "PLC", "LIMITED", "LTD", "INC", "CORPORATION", "CORP", "S A", "SA",
    "N V", "NV", "AG", "SE", "LLC", "LP", "NPV", "ORD", "DI", "CDI", "THE",
]
_SUFFIX_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in _SUFFIXES) + r")\b")


def normalise_name(name: str | None) -> str:
    """'Dillistone Group PLC' / 'DILLISTONE GROUP PLC' / 'Dillistone Group (DI)' -> 'DILLISTONE GROUP'."""
    if not isinstance(name, str) or not name.strip():
        return ""
    s = name.upper().replace("&", " AND ")
    s = re.sub(r"\(.*?\)", " ", s)          # drop parentheticals: (DI), (CDI), (TIDM)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = _SUFFIX_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def tidm_to_yahoo(tidm: str, suffix: str = ".L") -> str:
    """LSE TIDM -> Yahoo symbol. 'BT.A' -> 'BT-A.L', 'RR.' -> 'RR.L', 'DSG' -> 'DSG.L'."""
    t = tidm.strip().upper().rstrip(".")
    return t.replace(".", "-") + suffix
