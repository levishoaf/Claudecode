"""Edges: the only bets the builder shows (the user's rule).

An edge is a line FanDuel posts where FanDuel pays clearly more than the bet's true
chance is worth, and the bet is more likely to win than lose:

- the true chance comes from every other sportsbook's price with its cut taken out
  (nfl_edge/lines.py, sharp books counting most), so it needs real odds;
- value (expected profit per $1 at FanDuel's price) of at least EDGE_MIN_EV;
- a true chance of at least EDGE_MIN_PROB;
- nothing doubtful (picks._doubt): enough books, no rough or far-off price, no
  uncertain player, no Weak signals.

Thin edges are left out. Most days few bets qualify, some days none: the board says so
rather than showing anything weaker. Parlays are built only from edges (different
games, 2-3 legs); a parlay's value is every leg's edge compounded, though it wins less
often than any one leg."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
from math import prod

from . import board
from .finder import Bet
from .odds import expected_value
from .parlays import Parlay
from .picks import MAX_LEGS

from .picks import WORTH_MIN_EV as EDGE_MIN_EV  # at least +3% value at FanDuel's price
from .picks import WORTH_MIN_PROB as EDGE_MIN_PROB  # wins more often than it loses
PARLAY_LEGS = (2, 3)
PARLAY_MAX_USES = 2  # a leg in at most this many parlays


def is_edge(b) -> bool:
    from .picks import worth_it

    return worth_it(b)[0]


def rank(bets: list[Bet]) -> list[Bet]:
    """Best expected value first; ties go to the likelier bet (the user's rule). One bet
    per side of a line (the same pick at the same point once)."""
    seen, out = set(), []
    for b in sorted(bets, key=lambda b: (round(b.ev, 4), b.fair_prob), reverse=True):
        key = (b.game, b.market, b.pick, b.point)
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out


def full_board(sport: str, week=None, day=None, lo: float = 0.01, hi: float = 0.99) -> list[Bet]:
    """Every line the builder can price for a league, before real odds: game lines at
    every line, and the NFL's and NBA's player props (FanDuel's own lines replace the
    model's once real odds are applied)."""
    if sport == "nfl":
        return board.nfl_board(None, lo, hi, week=week, always_offered=False)
    if sport == "ncaaf":
        return board.cfb_board(None, lo, hi, week=week)
    if sport == "ufc":
        return board.ufc_board(lo, hi, card=str(week) if week else None)
    if sport == "mlb":
        return board.mlb_board(lo, hi, day=day, all_days=day is None, every_line=True)
    if sport == "nba":
        return board.nba_board(lo, hi, day=day, all_days=day is None, every_line=True)
    raise ValueError(f"unknown league {sport!r}")


def league_edges(sport: str, week=None, day=None, offers: list | None = None,
                 now: datetime | None = None) -> list[Bet]:
    """The league's edges, best first: its full board priced at FanDuel's real odds,
    games not yet started, every edge rule met. [] without real odds."""
    import bets as launcher
    from . import realodds

    if offers is None:
        offers = launcher.real_offers(sport, week if sport == "nfl" else None)
    if not offers:
        return []
    bets = realodds.apply(full_board(sport, week, day), sport, offers)
    if sport == "nfl":
        try:  # signal tiers: a Weak bet is never an edge
            from . import insights
            from .board import season_for

            insights.annotate(bets, "nfl", season_for(datetime.now().date()))
        except Exception:
            pass
    for b in bets:  # the true chance is the market's, never moved by the model
        if getattr(b, "on_fanduel", False) and b.market_prob is not None and \
                not getattr(b, "rough", False):
            b.fair_prob = b.market_prob
            b.ev = expected_value(b.fair_prob, b.fd_price)
    now = now or datetime.now(timezone.utc)
    out = [b for b in bets if b.commence_time > now and is_edge(b)]
    for b in out:
        b.league = sport
    return rank(out)


def _leg_key(b) -> tuple:
    return (getattr(b, "league", ""), b.game, b.market, b.pick, b.point)


class EdgeParlay(Parlay):
    """A parlay of edges: its chance is the legs' market chances multiplied (different
    games), with no correction learned from the model's own parlays."""

    @property
    def win_prob(self) -> float:
        return prod(b.fair_prob for b in self.legs)


def edge_parlays(bets: list[Bet], count: int = 10, legs: tuple[int, int] = PARLAY_LEGS,
                 max_uses: int = PARLAY_MAX_USES) -> list[Parlay]:
    """Parlays of edges only: 2-3 legs from different games (any leagues), each with at
    least EDGE_MIN_EV value at FanDuel's prices; likeliest first among them (the user's
    rule for parlays), a leg in at most `max_uses`."""
    pool = rank(bets)[:24]
    found = []
    for n in range(legs[0], min(legs[1], MAX_LEGS) + 1):
        for c in combinations(pool, n):
            if len({(getattr(b, "league", ""), b.game) for b in c}) < n:
                continue
            p = EdgeParlay(tuple(sorted(c, key=lambda b: b.commence_time)))
            if p.ev >= EDGE_MIN_EV:
                found.append(p)
    found.sort(key=lambda p: (round(p.win_prob, 4), p.ev), reverse=True)
    chosen, uses = [], {}
    for p in found:
        if len(chosen) == count:
            break
        if any(uses.get(_leg_key(b), 0) >= max_uses for b in p.legs):
            continue
        chosen.append(p)
        for b in p.legs:
            uses[_leg_key(b)] = uses.get(_leg_key(b), 0) + 1
    return chosen


def profit_per(b_or_p, wager: float = 10.0) -> float:
    """Expected profit on `wager` at FanDuel's price."""
    return wager * b_or_p.ev


def worst_odds(prob: float, need: float = EDGE_MIN_EV) -> int:
    """The worst FanDuel odds that still keep the edge rule (EDGE_MIN_EV) at this chance."""
    import math

    d = (1 + need) / prob  # the lowest decimal price that keeps the edge
    return math.ceil((d - 1) * 100) if d >= 2 else math.ceil(-100 / (d - 1))


NONE_NOTE = ("No edges right now: no FanDuel line pays at least {ev:.0%} more than its "
             "true chance is worth while winning {prob:.0%}+ of the time. That's normal: "
             "FanDuel's prices are usually close to fair. Check back as lines move.")


def none_note() -> str:
    return NONE_NOTE.format(ev=EDGE_MIN_EV, prob=EDGE_MIN_PROB)
