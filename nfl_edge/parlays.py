"""Combine individual bets into parlays.

A parlay pays the product of its legs' decimal odds and wins only if every
leg wins. FanDuel's margin compounds with each leg, so parlays built from
ordinary (negative-EV) bets lose money faster than singles. A parlay is only
worth playing when its legs each have an edge, so only those are used.

Legs must come from different games: legs in the same game are correlated
(FanDuel prices those separately as same-game parlays), so treating them as
independent would overstate the win probability.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import prod

from .finder import Bet
from .odds import american_to_decimal, decimal_to_american, kelly_fraction

MAX_CANDIDATES = 40


@dataclass
class Parlay:
    legs: tuple[Bet, ...]

    @property
    def win_prob(self) -> float:
        return prod(b.fair_prob for b in self.legs)

    @property
    def decimal(self) -> float:
        return prod(american_to_decimal(b.fd_price) for b in self.legs)

    @property
    def american(self) -> int:
        return decimal_to_american(self.decimal)

    @property
    def ev(self) -> float:
        return self.win_prob * self.decimal - 1

    @property
    def kelly(self) -> float:
        return kelly_fraction(self.win_prob, self.american)


def build_parlays(bets: list[Bet], *, max_legs: int = 3, min_ev: float = 0.0,
                  count: int = 5) -> list[Parlay]:
    """The `count` parlays with the highest win probability among those with
    at least `min_ev` expected value, using +EV legs from different games."""
    pool = sorted((b for b in bets if b.ev >= 0), key=lambda b: b.ev, reverse=True)
    pool = pool[:MAX_CANDIDATES]
    parlays = []
    for n in range(2, max_legs + 1):
        for legs in combinations(pool, n):
            if len({b.game for b in legs}) < n:
                continue
            parlay = Parlay(legs)
            if parlay.ev >= min_ev:
                parlays.append(parlay)
    parlays.sort(key=lambda p: (p.win_prob, p.ev), reverse=True)
    return parlays[:count]
