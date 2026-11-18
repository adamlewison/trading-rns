"""Matched-betting maths: back a bet at a bookmaker, lay the same outcome on an exchange so the result
does not matter. All odds decimal; commission is the exchange's cut of lay winnings (Betfair 5%, Smarkets 2%).

Qualifying bet (your own money, stake returned if it wins): lose a little to unlock a free bet.
Free bet, stake not returned (SNR): keep most of its value whatever happens.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hedge:
    lay_stake: float
    liability: float       # what the exchange holds while the bet runs: lay_stake x (lay - 1)
    if_back_wins: float    # net result if the bookmaker bet wins
    if_lay_wins: float     # net result if it loses (the lay wins)

    @property
    def result(self) -> float:
        return min(self.if_back_wins, self.if_lay_wins)


def qualifying(stake: float, back: float, lay: float, commission: float = 0.05) -> Hedge:
    """Equal-outcome lay for a cash bet: lay = stake x back / (lay - commission)."""
    _check(back, lay, commission)
    ls = stake * back / (lay - commission)
    return Hedge(ls, ls * (lay - 1), stake * (back - 1) - ls * (lay - 1), ls * (1 - commission) - stake)


def free_bet_snr(free: float, back: float, lay: float, commission: float = 0.05) -> Hedge:
    """Equal-outcome lay for a free bet whose stake is not returned: lay = free x (back - 1) / (lay - commission)."""
    _check(back, lay, commission)
    ls = free * (back - 1) / (lay - commission)
    return Hedge(ls, ls * (lay - 1), free * (back - 1) - ls * (lay - 1), ls * (1 - commission))


def _check(back: float, lay: float, commission: float) -> None:
    if back <= 1 or lay <= 1:
        raise ValueError("decimal odds must be above 1")
    if not 0 <= commission < 1:
        raise ValueError("commission is a fraction, e.g. 0.05")


def rating(back: float, lay: float) -> float:
    """Oddsmatcher 'rating': back / lay. 100% or more means no loss before commission."""
    return back / lay
