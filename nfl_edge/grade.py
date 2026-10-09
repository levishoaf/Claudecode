"""Grade a saved bet against final results from nflverse.

    python -m nfl_edge.grade bets/2026_week5_sgp5.json

Supported legs: moneyline (team wins) and passing_tds (player throws at
least `min` passing touchdowns). Player stats usually post the day after
a game.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import data

STATS_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "stats_player/stats_player_week_{season}.csv")


def grade_leg(leg: dict, games: dict[str, dict], stats: list[dict]) -> tuple[str, str]:
    """('won' | 'lost' | 'pending', detail)."""
    g = games.get(leg["game_id"])
    if g is None:
        return "pending", "game not found"
    if g["home_score"] == "":
        return "pending", "not final yet"
    home, away = int(g["home_score"]), int(g["away_score"])
    score = f"{g['away_team']} {away} - {g['home_team']} {home}"
    if leg["type"] == "moneyline":
        if home == away:
            return "lost", f"tie, {score}"
        winner = g["home_team"] if home > away else g["away_team"]
        return ("won" if winner == leg["team"] else "lost"), score
    if leg["type"] == "passing_tds":
        row = next((s for s in stats if s["game_id"] == leg["game_id"]
                    and s["player_display_name"] == leg["player"]), None)
        if row is None:
            return "pending", "player stats not posted yet"
        tds = int(row["passing_tds"] or 0)
        return ("won" if tds >= leg["min"] else "lost"), f"{tds} passing TD{'s' * (tds != 1)}"
    return "pending", f"unknown leg type {leg['type']}"


def grade(bet: dict, games_source: str | None = None) -> list[tuple[dict, str, str]]:
    games = {g["game_id"]: g for g in data.games(games_source)}
    stats: list[dict] = []
    if any(leg["type"] == "passing_tds" for leg in bet["legs"]):
        try:
            stats = data.read_csv(STATS_URL.format(season=bet["season"]))
        except data.DataError:
            stats = []
    return [(leg, *grade_leg(leg, games, stats)) for leg in bet["legs"]]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("bet", type=Path, help="saved bet JSON file")
    p.add_argument("--stats-file", help="nflverse games.csv path or URL")
    args = p.parse_args()
    bet = json.loads(args.bet.read_text())

    results = grade(bet, args.stats_file)
    print(bet["name"])
    mark = {"won": "✓", "lost": "✗", "pending": "…"}
    for leg, status, detail in results:
        print(f"  {mark[status]} {leg['label']:<36} {status:<8} {detail}")
    statuses = {s for _, s, _ in results}
    if "lost" in statuses:
        verdict = "LOST"
    elif statuses == {"won"}:
        verdict = "WON"
    else:
        verdict = "PENDING"
    print(f"\nParlay: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
