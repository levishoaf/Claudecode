"""Check whether blending the season-stats model into market odds helps.

For each week of past seasons, fits the model on earlier weeks only, then
scores home-win probabilities against results. The "market" is the closing
spread from nflverse. Run: python -m nfl_edge.backtest [--start 2015 --end 2025]
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import urllib.request
from pathlib import Path

from .stats import GAMES_URL, MARGIN_SD, Game, SeasonModel, normal_cdf


def _read(source: str | None) -> list[dict]:
    if source and Path(source).exists():
        text = Path(source).read_text()
    else:
        with urllib.request.urlopen(source or GAMES_URL, timeout=30) as resp:
            text = resp.read().decode()
    return [
        r for r in csv.DictReader(io.StringIO(text))
        if r["game_type"] == "REG" and r["home_score"] != "" and r["spread_line"] != ""
    ]


def run(rows: list[dict], seasons: range, weights: list[float]) -> None:
    loss = {w: 0.0 for w in weights}
    games = ats_wins = ats_games = 0
    for season in seasons:
        season_rows = [r for r in rows if int(r["season"]) == season]
        for week in range(3, 19):
            train = [
                Game(r["home_team"], r["away_team"], int(r["home_score"]),
                     int(r["away_score"]), r["location"] == "Neutral")
                for r in season_rows if int(r["week"]) < week
            ]
            test = [r for r in season_rows if int(r["week"]) == week]
            if not train or not test:
                continue
            model = SeasonModel(train)
            for r in test:
                result, spread = int(r["result"]), float(r["spread_line"])
                if result == 0:
                    continue
                home_pts, away_pts = model.predict(
                    r["home_team"], r["away_team"], r["location"] == "Neutral"
                )
                p_market = normal_cdf(spread / MARGIN_SD)
                p_model = normal_cdf((home_pts - away_pts) / MARGIN_SD)
                sample = model.sample_weight(r["home_team"], r["away_team"])
                won = result > 0
                games += 1
                for w in weights:
                    p = (1 - w * sample) * p_market + w * sample * p_model
                    loss[w] -= math.log(p if won else 1 - p)
                if result != spread:
                    ats_games += 1
                    ats_wins += (home_pts - away_pts > spread) == (result > spread)

    print(f"{games} games, seasons {seasons.start}-{seasons.stop - 1}, weeks 3-18\n")
    print("Model weight  Log loss (lower is better)")
    for w in weights:
        print(f"{w:>12.2f}  {loss[w] / games:.5f}")
    print(f"\nStats model picking against the closing spread: "
          f"{ats_wins}/{ats_games} = {ats_wins / ats_games:.1%} (break-even at -110 is 52.4%)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", type=int, default=2015)
    p.add_argument("--end", type=int, default=2025)
    p.add_argument("--stats-file", help="nflverse games.csv path or URL")
    args = p.parse_args()
    run(_read(args.stats_file), range(args.start, args.end + 1), [0, 0.1, 0.2, 0.3, 0.5, 1.0])


if __name__ == "__main__":
    main()
