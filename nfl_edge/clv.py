"""Did the picks beat the closing line? The quickest honest sign of an edge.

When a pick's line moves its way before kickoff (a moneyline's chance rises, or the
spread moves so the pick's points are worth more), the market came around to the
pick: bets that beat the closing line win more than the line implies over time, and
it shows within weeks instead of seasons. NFL moneylines and spreads, compared with
the final line in the free schedule data; props and college have no free closing
lines, so they aren't measured.
"""

from __future__ import annotations

from .odds import devig_power, implied_probability


def leg_clv(leg: dict, game: dict) -> tuple[str, float] | None:
    """("moneyline", chance points gained) or ("spread", points gained) for one NFL
    leg, once its game has been played; None if it can't be measured."""
    if not game or not game.get("home_score"):
        return None
    side = "home" if leg.get("team") == game["home_team"] else \
        "away" if leg.get("team") == game["away_team"] else None
    if side is None:
        return None
    try:
        if leg.get("type") == "moneyline":
            ph, pa = devig_power([implied_probability(int(game["home_moneyline"])),
                                  implied_probability(int(game["away_moneyline"]))])
            close = ph if side == "home" else pa
            then = leg.get("market_prob") or leg.get("raw_prob") or leg.get("win_prob")
            return ("moneyline", close - float(then)) if then else None
        if leg.get("type") == "spread" and leg.get("main_line") is not None:
            # The team's main spread when picked vs at the close (alternate lines move
            # with it): a team that closed more favored than when picked beat the close.
            sp = float(game["spread_line"])  # positive = home favored
            close = -sp if side == "home" else sp
            return "spread", float(leg["main_line"]) - close
    except (TypeError, ValueError, KeyError):
        return None
    return None


def report(entries: list[dict], games: list[dict]) -> dict[str, tuple[int, int, float]]:
    """{"moneyline"|"spread": (picks measured, beat the close, average gained)} over
    every NFL pick's legs (each counted once)."""
    by_id = {g["game_id"]: g for g in games}
    seen, got = set(), {"moneyline": [], "spread": []}
    for e in entries:
        if e.get("sport", "nfl") != "nfl":
            continue
        for leg in e.get("legs", []):
            if leg.get("id") in seen:
                continue
            seen.add(leg.get("id"))
            r = leg_clv(leg, by_id.get(leg.get("game_id")))
            if r:
                got[r[0]].append(r[1])
    return {k: (len(v), sum(x > 1e-9 for x in v), sum(v) / len(v)) for k, v in got.items() if v}
