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


def is_prop(b: Bet) -> bool:
    return b.market.startswith("player_")


def rank_singles(bets: list[Bet], count: int, min_prob: float = 0.0, *,
                 rank_by: str = "ev", per_game: int | None = None,
                 prop_share: float | None = None) -> list[Bet]:
    """Best `count` singles, one per subject (see subject_key) and at most
    `per_game` from any one game. With `prop_share` (e.g. 0.5), player props and
    game bets each fill at most that share of the list first, so the list mixes
    both; leftover places then go to the best of whichever remains."""
    ranked = [b for b in sorted(bets, key=sort_key(rank_by), reverse=True)
              if b.fair_prob >= min_prob]
    seen, games, out = set(), {}, []
    caps = None
    if prop_share is not None:
        n_props = round(count * prop_share)
        caps = {True: n_props, False: count - n_props}

    def take(b, capped: bool) -> None:
        key = subject_key(b)
        if key in seen or (per_game and games.get(b.game, 0) >= per_game):
            return
        if capped and caps is not None:
            if sum(is_prop(x) == is_prop(b) for x in out) >= caps[is_prop(b)]:
                return
        seen.add(key)
        games[b.game] = games.get(b.game, 0) + 1
        out.append(b)

    for capped in ((True, False) if caps else (False,)):
        for b in ranked:
            if len(out) == count:
                break
            take(b, capped)
    # Keep the list in rank order after the two passes.
    order = {id(b): i for i, b in enumerate(ranked)}
    return sorted(out, key=lambda b: order[id(b)])


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


def mixed_parlays(bets: list[Bet], min_legs: int, max_legs: int, count: int, *,
                  min_prob: float = 0.0, rank_by: str = "prob", max_uses: int = 3,
                  pool_size: int = 24) -> list[Parlay]:
    """`count` parlays spread across `min_legs`..`max_legs` legs.

    A week has too few games for many parlays without sharing games, so
    parlays may share games with each other. Each parlay still uses
    different games for its own legs, no two parlays are identical, and no
    single bet appears in more than `max_uses` parlays.
    """
    sizes = list(range(min_legs, max_legs + 1))
    quota = {n: count // len(sizes) for n in sizes}
    for n in sizes[: count % len(sizes)]:
        quota[n] += 1

    pool = rank_singles(bets, pool_size, min_prob, rank_by=rank_by)
    ranked = {}
    for n in sizes:
        candidates = [Parlay(c) for c in combinations(pool, n) if len({b.game for b in c}) == n]
        if rank_by == "prob":
            candidates.sort(key=lambda p: p.win_prob, reverse=True)
        else:
            candidates.sort(key=lambda p: value_key(p.ev, p.win_prob), reverse=True)
        ranked[n] = candidates

    uses: dict[int, int] = {}
    chosen: list[Parlay] = []
    taken = {n: 0 for n in sizes}
    limit = max_uses
    # Biggest parlays first: they need the most fresh legs. If the reuse limit
    # leaves gaps, loosen it one step at a time.
    while sum(taken.values()) < min(count, sum(len(c) for c in ranked.values())) and limit <= count:
        for n in reversed(sizes):
            for p in ranked[n]:
                if taken[n] == quota[n]:
                    break
                if p in chosen or any(uses.get(id(b), 0) >= limit for b in p.legs):
                    continue
                chosen.append(p)
                taken[n] += 1
                for b in p.legs:
                    uses[id(b)] = uses.get(id(b), 0) + 1
        limit += 1
    chosen.sort(key=lambda p: (len(p.legs), -p.win_prob))
    return chosen


def payout_parlays(bets: list[Bet], target: float, count: int, *, min_legs: int = 2,
                   max_legs: int = 6, tolerance: float = 0.05, rank_by: str = "prob",
                   max_uses: int = 3, pool_size: int = 20) -> list[Parlay]:
    """`count` parlays that each pay about `target` times the wager (stake
    included), likeliest to win first (best value first with rank_by="ev").

    Legs come from different games. If too few parlays land within
    `tolerance` of the target, the band widens step by step up to 50%.
    No bet appears in more than `max_uses` parlays when that can be avoided.
    """
    pool = rank_singles(bets, pool_size, rank_by="prob", per_game=2)
    candidates = [Parlay(c) for n in range(min_legs, max_legs + 1)
                  for c in combinations(pool, n) if len({b.game for b in c}) == n]
    if rank_by == "prob":
        key = lambda p: (p.win_prob, -abs(p.decimal / target - 1))  # noqa: E731
    else:
        key = lambda p: (*value_key(p.ev, p.win_prob), -abs(p.decimal / target - 1))  # noqa: E731
    band = tolerance
    near: list[Parlay] = []
    while band <= 0.5 + 1e-9:
        near = sorted((p for p in candidates if abs(p.decimal / target - 1) <= band),
                      key=key, reverse=True)
        if len(near) >= count:
            break
        band += 0.05
    chosen: list[Parlay] = []
    uses: dict[int, int] = {}
    for limit in range(max_uses, count + max_uses + 1):  # relax reuse only if needed
        for p in near:
            if len(chosen) == count:
                break
            if p in chosen or any(uses.get(id(b), 0) >= limit for b in p.legs):
                continue
            chosen.append(p)
            for b in p.legs:
                uses[id(b)] = uses.get(id(b), 0) + 1
        if len(chosen) == count or len(chosen) == len(near):
            break
    return sorted(chosen, key=key, reverse=True)
