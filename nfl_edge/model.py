"""Full game model: this season's team ratings plus context adjustments
(injuries, starting QB, travel, divisional game, weather)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .context import Features, SeasonData
from .factors import Coefficients
from .research import to_game
from .stats import TEAM_ABBR, SeasonModel, probabilities_from_points


@dataclass
class Prediction:
    home: str
    away: str
    base_home: float
    base_away: float
    home_pts: float
    away_pts: float
    adjustments: list[tuple[str, float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    matched: bool = True  # found in the schedule, so context applies


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class GameModel:
    def __init__(self, season_rows: list[dict], season_data: SeasonData,
                 coefs: Coefficients, weather_fn=None):
        completed = [to_game(g) for g in season_rows
                     if g["game_type"] == "REG" and g["home_score"] != ""]
        self.ratings = SeasonModel(completed)
        self.rows = [g for g in season_rows if g["game_type"] == "REG"]
        self.data = season_data
        self.coefs = coefs
        self.weather_fn = weather_fn
        self._cache: dict[str, Prediction | None] = {}

    @property
    def num_games(self) -> int:
        return self.ratings.num_games

    def schedule_row(self, event: dict) -> dict | None:
        home, away = TEAM_ABBR.get(event["home_team"]), TEAM_ABBR.get(event["away_team"])
        kickoff = _parse(event["commence_time"]).date()
        for g in self.rows:
            if g["home_team"] == home and g["away_team"] == away and g["home_score"] == "":
                day = datetime.fromisoformat(g["gameday"]).date()
                if abs(day - kickoff) <= timedelta(days=1):
                    return g
        return None

    def predict(self, event: dict) -> Prediction | None:
        key = event.get("id") or f"{event['away_team']}@{event['home_team']}"
        if key not in self._cache:
            self._cache[key] = self._predict(event)
        return self._cache[key]

    def _predict(self, event: dict) -> Prediction | None:
        home, away = TEAM_ABBR.get(event["home_team"]), TEAM_ABBR.get(event["away_team"])
        if home is None or away is None:
            return None
        row = self.schedule_row(event)
        neutral = row is not None and row["location"] == "Neutral"
        base_home, base_away = self.ratings.predict(home, away, neutral)
        if row is None:
            return Prediction(home, away, base_home, base_away, base_home, base_away,
                              notes=["not found in schedule; ratings only"], matched=False)

        f: Features = self.data.features(row, actual_qb=False)
        if not f.indoor and self.weather_fn:
            wx = self.weather_fn(row["stadium_id"], _parse(event["commence_time"]))
            if wx:
                f.wind, f.temp = wx["wind"], wx["temp"]
                note = f"forecast {wx['temp']:.0f}F, wind {wx['wind']:.0f} mph"
                if wx.get("precip"):
                    note += f", precip {wx['precip']:.2f} in/hr"
                f.notes.append(note)
            else:
                f.notes.append("no weather forecast yet")
        elif f.indoor:
            f.notes.append("indoors")

        base_margin = base_home - base_away
        adjustments = self.coefs.adjustments(f, base_margin)
        dm = sum(m for _, m, _ in adjustments)
        dt = sum(t for _, _, t in adjustments)
        return Prediction(
            home, away, base_home, base_away,
            base_home + (dt + dm) / 2, base_away + (dt - dm) / 2,
            adjustments, f.notes,
        )

    def sample_weight_for(self, event: dict) -> float:
        p = self.predict(event)
        return 0.0 if p is None else self.ratings.sample_weight(p.home, p.away)

    def outcome_probabilities(self, event: dict, market: str,
                              outcomes: list[dict]) -> dict[str, float] | None:
        p = self.predict(event)
        if p is None:
            return None
        return probabilities_from_points(event["home_team"], p.home_pts, p.away_pts,
                                         market, outcomes)
