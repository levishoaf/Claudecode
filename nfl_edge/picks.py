"""Pick single bets and parlays by the user's standing rules.

- Rank by expected value; break ties toward the higher win probability.
- Parlays use legs from different games, and by default no two parlays
  share a game, so one bad game can't sink more than one ticket.
"""

from __future__ import annotations

from itertools import combinations
from math import comb

from .finder import Bet
from .parlays import Parlay

MAX_COMBOS = 200_000


def value_key(ev: float, win_prob: float) -> tuple[float, float]:
    # Round EV so near-ties are decided by the higher win probability.
    return round(ev, 3), win_prob


def subject_key(b: Bet) -> tuple[str, str, str]:
    """What a bet is about: alternate lines count as their main market, and
    props and team totals are per player or team. Only the best bet on each
    subject is kept, so the list isn't five versions of one player."""
    market = b.market.replace("alternate_", "").removesuffix("_alternate")
    if market.startswith("player_") or market == "team_totals":
        return b.game, market, b.pick.rsplit(" ", 1)[0]
    return b.game, market, ""


def sort_key(rank_by: str):
    """'ev': best value first (ties to the likelier bet). 'prob': likeliest
    first, for bets without real FanDuel prices."""
    if rank_by == "prob":
        return lambda b: (round(b.fair_prob, 3), b.ev)
    return lambda b: value_key(b.ev, b.fair_prob)


def rank_singles(bets: list[Bet], count: int, min_prob: float = 0.0, *,
                 rank_by: str = "ev", per_game: int | None = None) -> list[Bet]:
    """Best `count` singles, one per subject (see subject_key) and at most
    `per_game` from any one game."""
    seen, games, out = set(), {}, []
    for b in sorted(bets, key=sort_key(rank_by), reverse=True):
        key = subject_key(b)
        if b.fair_prob < min_prob or key in seen:
            continue
        if per_game and games.get(b.game, 0) >= per_game:
            continue
        seen.add(key)
        games[b.game] = games.get(b.game, 0) + 1
        out.append(b)
        if len(out) == count:
            break
    return out


def best_parlays(bets: list[Bet], legs: int, count: int, *, allow_overlap: bool = False,
                 min_prob: float = 0.0, rank_by: str = "ev") -> list[Parlay]:
    """Best `count` parlays of exactly `legs` legs from different games."""
    pool = rank_singles(bets, 10_000, min_prob, rank_by=rank_by)
    # Keep the search affordable: shrink the pool until the combinations fit.
    while len(pool) > legs and comb(len(pool), legs) > MAX_COMBOS:
        pool = pool[:-1]
    candidates = [Parlay(c) for c in combinations(pool, legs)
                  if len({b.game for b in c}) == legs]
    if rank_by == "prob":
        candidates.sort(key=lambda p: p.win_prob, reverse=True)
    else:
        candidates.sort(key=lambda p: value_key(p.ev, p.win_prob), reverse=True)

    chosen: list[Parlay] = []
    used_games: set[str] = set()
    for p in candidates:
        games = {b.game for b in p.legs}
        if not allow_overlap and games & used_games:
            continue
        chosen.append(p)
        used_games |= games
        if len(chosen) == count:
            break
    return chosen
