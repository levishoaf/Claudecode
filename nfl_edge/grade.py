"""Grade a saved bet against final results from nflverse.

    python -m nfl_edge.grade bets/2026_week5_sgp5.json

Works for NFL bets and, with "sport": "ncaaf" in the file, college bets.
Supported legs: moneyline (team wins), spread (team + point), total
(Over/Under a line), team_total (one team's points Over/Under a line),
passing_tds (player throws at least `min` passing
touchdowns) and player_stat (player reaches `min` of any nflverse stat
column, e.g. receptions, receiving_yards, rushing_yards, passing_yards). Player stats usually post the day after a game.

A file with "kind": "singles" grades each leg as its own bet, using each
leg's "odds" and "stake"; otherwise the legs form one parlay.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import data
from .odds import american_to_decimal

STATS_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "stats_player/stats_player_week_{season}.csv")


def grade_leg(leg: dict, games: dict[str, dict], stats: list[dict]) -> tuple[str, str]:
    """('won' | 'lost' | 'push' | 'pending', detail)."""
    g = games.get(leg["game_id"])
    if g is None:
        return "pending", "game not found"
    if g["home_score"] == "":
        return "pending", "not final yet"
    home, away = int(float(g["home_score"])), int(float(g["away_score"]))
    score = f"{g['away_team']} {away} - {g['home_team']} {home}"
    if leg["type"] == "moneyline":
        if home == away:
            return "lost", f"tie, {score}"
        winner = g["home_team"] if home > away else g["away_team"]
        return ("won" if winner == leg["team"] else "lost"), score
    if leg["type"] == "spread":
        margin = home - away if leg["team"] == g["home_team"] else away - home
        covered = margin + leg["point"]
        return ("won" if covered > 0 else "push" if covered == 0 else "lost"), score
    if leg["type"] == "total":
        total = home + away
        diff = total - leg["line"] if leg["side"] == "Over" else leg["line"] - total
        return ("won" if diff > 0 else "push" if diff == 0 else "lost"), f"{score} (total {total})"
    if leg["type"] == "team_total":
        pts = home if leg["team"] == g["home_team"] else away
        diff = pts - leg["line"] if leg["side"] == "Over" else leg["line"] - pts
        return ("won" if diff > 0 else "push" if diff == 0 else "lost"), f"{score} ({leg['team']} {pts})"
    if leg["type"] in ("passing_tds", "player_stat"):
        stat = leg.get("stat", "passing_tds")
        row = next((s for s in stats if s["game_id"] == leg["game_id"]
                    and s["player_display_name"] == leg["player"]), None)
        if row is None:
            if not any(s["game_id"] == leg["game_id"] for s in stats):
                return "pending", "player stats not posted yet"
            return "lost", "no stats recorded (did not play)"
        value = int(sum(float(row.get(c) or 0) for c in stat.split("+")))
        if stat == "passing_tds":
            detail = f"{value} passing TD{'s' * (value != 1)}"
        else:
            detail = f"{value} {stat.replace('_', ' ')}"
        if "max" in leg:  # an Under
            return ("won" if value <= leg["max"] else "lost"), detail
        return ("won" if value >= leg["min"] else "lost"), detail
    return "pending", f"unknown leg type {leg['type']}"


def college_games(season: int, source: str | None = None) -> dict[str, dict]:
    """College schedule rows reshaped like nflverse games (scores blank until final)."""
    from . import cfb

    games = {}
    for r in cfb.load_schedule(season, source, current_season=season):
        final = r["completed"] == "TRUE"
        games[r["game_id"]] = {
            "game_id": r["game_id"], "home_team": r["home_team"], "away_team": r["away_team"],
            "home_score": r["home_points"] if final else "",
            "away_score": r["away_points"] if final else "",
        }
    return games


def grade(bet: dict, games_source: str | None = None) -> list[tuple[dict, str, str]]:
    if bet.get("sport") == "ncaaf":
        games = college_games(bet["season"], games_source)
    else:
        games = {g["game_id"]: g for g in data.games(games_source)}
    stats: list[dict] = []
    if any(leg["type"] in ("passing_tds", "player_stat") for leg in bet["legs"]):
        try:
            stats = data.read_csv(STATS_URL.format(season=bet["season"]))
        except data.DataError:
            stats = []
    return [(leg, *grade_leg(leg, games, stats)) for leg in bet["legs"]]


def profit_of(leg: dict, status: str) -> float:
    if status == "won":
        return leg["stake"] * (american_to_decimal(leg["odds"]) - 1)
    if status == "lost":
        return -leg["stake"]
    return 0.0


def print_report(path: Path, games_source: str | None = None) -> None:
    bet = json.loads(Path(path).read_text())
    results = grade(bet, games_source)
    print(bet["name"])
    mark = {"won": "✓", "lost": "✗", "push": "=", "pending": "…"}
    for leg, status, detail in results:
        print(f"  {mark[status]} {leg['label']:<36} {status:<8} {detail}")

    if bet.get("kind") == "singles":
        settled = [(leg, s) for leg, s, _ in results if s != "pending"]
        profit = sum(profit_of(leg, s) for leg, s in settled)
        staked = sum(leg["stake"] for leg, _ in settled)
        wins = sum(s == "won" for _, s in settled)
        print(f"\nSettled {len(settled)}/{len(results)}: {wins} won, "
              f"profit ${profit:+,.2f} on ${staked:,.0f} staked")
        return

    statuses = {s for _, s, _ in results}
    statuses.discard("push")  # a pushed leg drops out of the parlay
    if "lost" in statuses:
        verdict = "LOST"
    elif statuses <= {"won"}:
        verdict = "WON"
    else:
        verdict = "PENDING"
    print(f"\nParlay: {verdict}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("bet", type=Path, help="saved bet JSON file")
    p.add_argument("--stats-file", help="nflverse games.csv path or URL")
    args = p.parse_args()
    print_report(args.bet, args.stats_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
