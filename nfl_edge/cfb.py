"""College football (FBS) data and model.

Data comes from the sportsdataverse cfbfastR-data repository on GitHub:
season schedules with scores and Elo ratings, plus historical betting lines.

The model fits offense/defense ratings to this season's scores, like the NFL
model, but starts each team from its preseason Elo instead of from average.
College teams play ~5 games by October, many against much weaker opponents,
so that prior matters far more than in the NFL. All FCS opponents share one
pooled "FCS" rating.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from . import data
from .model import Prediction
from .stats import Game, SeasonModel, probabilities_from_points

SCHEDULE_URL = ("https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/"
                "schedules/csv/cfb_schedules_{season}.csv")
LINES_URL = ("https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/"
             "betting/csv/cfb_line_odds.csv.gz")

FCS = "FCS"
# Fit on 2023-2024 FBS games; see `python -m nfl_edge.cfb_backtest`.
ELO_POINTS = 0.041  # points of margin per Elo point
HOME_FIELD = 3.6
MARGIN_SD = 16.5
TOTAL_SD = 16.0
RIDGE = 2.0  # prior strength, in games
FCS_PRIOR = -22.0  # pooled FCS opponent vs. average FBS team, points per game
ELO_BLEND = 0.5  # weight on current Elo margin vs. ratings margin


def _num(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_schedule(season: int, source: str | None = None, current_season: int = 9999) -> list[dict]:
    return data.read_csv(source or SCHEDULE_URL.format(season=season),
                         permanent=season < current_season)


def completed(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["completed"] == "TRUE"
            and _num(r["home_points"]) is not None and _num(r["away_points"]) is not None]


def team_key(row: dict, side: str) -> str:
    return row[f"{side}_team"] if row[f"{side}_division"] == "fbs" else FCS


def to_games(rows: list[dict]) -> list[Game]:
    return [Game(team_key(r, "home"), team_key(r, "away"), int(_num(r["home_points"])),
                 int(_num(r["away_points"])), r["neutral_site"] == "TRUE") for r in completed(rows)]


def preseason_prior(rows: list[dict]) -> dict[str, float]:
    """Net points vs. average FBS team from each team's first pregame Elo."""
    first: dict[str, tuple[str, float]] = {}
    for r in rows:
        for side in ("home", "away"):
            elo = _num(r[f"{side}_pregame_elo"])
            team = team_key(r, side)
            if team == FCS or elo is None:
                continue
            if team not in first or r["start_date"] < first[team][0]:
                first[team] = (r["start_date"], elo)
    if not first:
        return {FCS: FCS_PRIOR}
    mean = sum(e for _, e in first.values()) / len(first)
    prior = {t: ELO_POINTS * (e - mean) for t, (_, e) in first.items()}
    prior[FCS] = FCS_PRIOR
    return prior


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9() ]", "", name.lower().replace("&", " and ")).strip()


# Spellings that differ between sportsbooks and the schedule data.
ALIASES = {
    "appalachian state": "app state", "southern mississippi": "southern miss",
    "louisiana monroe": "ul monroe", "ulm": "ul monroe", "umass": "massachusetts",
    "florida intl": "florida international", "fiu": "florida international",
    "sam houston state": "sam houston", "connecticut": "uconn",
    "miami fl": "miami", "miami (fl)": "miami", "miami ohio": "miami (oh)",
    "texas san antonio": "utsa", "ut san antonio": "utsa", "central florida": "ucf",
    "southern methodist": "smu", "brigham young": "byu", "louisiana state": "lsu",
    "mississippi": "ole miss", "north carolina state": "nc state",
}


def _longest_prefix(target: str, schools: list[str]) -> str | None:
    best = None
    for school in schools:
        key = normalize(school)
        if target == key or target.startswith(key + " "):
            if best is None or len(key) > len(normalize(best)):
                best = school
    return best


def match_team(odds_name: str, schools: list[str]) -> str | None:
    """Odds API names add the mascot ("Iowa State Cyclones"); pick the longest
    school name that the odds name starts with, then try known aliases."""
    target = normalize(odds_name)
    found = _longest_prefix(target, schools)
    covered = len(normalize(found)) if found else 0
    for alias, school in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if len(alias) > covered and (target == alias or target.startswith(alias + " ")):
            return _longest_prefix(school + target[len(alias):], schools)
    return found


class CfbModel:
    """Same interface as model.GameModel, for college games."""

    def __init__(self, season_rows: list[dict], ridge: float = RIDGE,
                 elo_blend: float = ELO_BLEND):
        self.rows = season_rows
        self.ratings = SeasonModel(to_games(season_rows), ridge=ridge, home_field=HOME_FIELD,
                                   prior=preseason_prior(season_rows))
        self.elo_blend = elo_blend
        self.schools = sorted({r[f"{s}_team"] for r in season_rows for s in ("home", "away")
                               if r[f"{s}_division"] == "fbs"})
        self._cache: dict[str, Prediction | None] = {}

    @property
    def num_games(self) -> int:
        return self.ratings.num_games

    def schedule_row(self, event: dict) -> dict | None:
        home = match_team(event["home_team"], self.schools)
        away = match_team(event["away_team"], self.schools)
        if home is None or away is None:
            return None
        kickoff = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        for r in self.rows:
            if {r["home_team"], r["away_team"]} != {home, away} or r["completed"] == "TRUE":
                continue
            start = datetime.fromisoformat(r["start_date"].replace("Z", "+00:00"))
            if abs(start - kickoff) <= timedelta(days=2):
                return r
        return None

    def predict_row(self, row: dict) -> tuple[float, float, list[str]]:
        """(home_pts, away_pts, notes) for a schedule row."""
        home, away = team_key(row, "home"), team_key(row, "away")
        neutral = row["neutral_site"] == "TRUE"
        home_pts, away_pts = self.ratings.predict(home, away, neutral)
        notes = []
        h_elo, a_elo = _num(row["home_pregame_elo"]), _num(row["away_pregame_elo"])
        if h_elo is not None and a_elo is not None and self.elo_blend:
            elo_margin = ELO_POINTS * (h_elo - a_elo) + (0 if neutral else HOME_FIELD)
            margin = home_pts - away_pts
            shift = self.elo_blend * (elo_margin - margin) / 2
            home_pts, away_pts = home_pts + shift, away_pts - shift
            notes.append(f"Elo {row['away_team']} {a_elo:.0f}, {row['home_team']} {h_elo:.0f}")
        if neutral:
            notes.append("neutral site")
        return home_pts, away_pts, notes

    def predict(self, event: dict) -> Prediction | None:
        key = event.get("id") or f"{event['away_team']}@{event['home_team']}"
        if key in self._cache:
            return self._cache[key]
        row = self.schedule_row(event)
        result = None
        if row is not None:
            # Orient to the event's home team (schedules and books occasionally differ).
            home_pts, away_pts, notes = self.predict_row(row)
            if match_team(event["home_team"], self.schools) != row["home_team"]:
                home_pts, away_pts = away_pts, home_pts
            base_h, base_a = self.ratings.predict(team_key(row, "home"), team_key(row, "away"),
                                                  row["neutral_site"] == "TRUE")
            result = Prediction(row["home_team"], row["away_team"], base_h, base_a,
                                home_pts, away_pts, [], notes)
        self._cache[key] = result
        return result

    def sample_weight_for(self, event: dict) -> float:
        row = self.schedule_row(event)
        if row is None:
            return 0.0
        played = min(self.ratings.games_played.get(team_key(row, s), 0) for s in ("home", "away"))
        return min(1.0, played / 6)

    def outcome_probabilities(self, event: dict, market: str,
                              outcomes: list[dict]) -> dict[str, float] | None:
        p = self.predict(event)
        if p is None:
            return None
        return probabilities_from_points(event["home_team"], p.home_pts, p.away_pts, market,
                                         outcomes, MARGIN_SD, TOTAL_SD)
