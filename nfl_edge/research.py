"""Walk-forward dataset of past games, used to fit and test the factors.

For each week W of each season, team ratings are fit on weeks < W only, and
each game's context uses only pre-kickoff information. The residual (actual
result minus the ratings-only prediction) is what the factors must explain.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import data
from .context import Features, SeasonData
from .stats import Game, SeasonModel


@dataclass
class Record:
    season: int
    week: int
    base_home: float
    base_away: float
    features: Features
    margin: int  # actual home minus away
    total: int
    spread_line: float  # closing, home perspective (positive = home favored)
    total_line: float | None
    sample_weight: float

    @property
    def base_margin(self) -> float:
        return self.base_home - self.base_away

    @property
    def base_total(self) -> float:
        return self.base_home + self.base_away


def to_game(row: dict) -> Game:
    return Game(row["home_team"], row["away_team"], int(row["home_score"]),
                int(row["away_score"]), row["location"] == "Neutral")


def build(seasons: range, games_source: str | None = None, data_dir: str | None = None,
          current_season: int = 9999, progress: bool = True) -> list[Record]:
    all_games = data.games(games_source)
    records = []
    for season in seasons:
        rows = [g for g in all_games if g["season"] == str(season) and g["game_type"] == "REG"]
        if not rows:
            continue
        sd = SeasonData(
            rows,
            data.release("injuries", season, current_season, data_dir),
            data.release("snap_counts", season, current_season, data_dir),
            history=all_games,
        )
        for week in range(3, 19):
            done_before = [to_game(g) for g in rows if int(g["week"]) < week and g["home_score"] != ""]
            tests = [g for g in rows if int(g["week"]) == week and g["home_score"] != ""
                     and g["spread_line"] != ""]
            if not done_before or not tests:
                continue
            model = SeasonModel(done_before)
            for g in tests:
                home_pts, away_pts = model.predict(g["home_team"], g["away_team"],
                                                   g["location"] == "Neutral")
                records.append(Record(
                    season=season, week=week, base_home=home_pts, base_away=away_pts,
                    features=sd.features(g, actual_qb=True),
                    margin=int(g["home_score"]) - int(g["away_score"]),
                    total=int(g["home_score"]) + int(g["away_score"]),
                    spread_line=float(g["spread_line"]),
                    total_line=float(g["total_line"]) if g["total_line"] else None,
                    sample_weight=model.sample_weight(g["home_team"], g["away_team"]),
                ))
        if progress:
            print(f"  built {season}", flush=True)
    return records
