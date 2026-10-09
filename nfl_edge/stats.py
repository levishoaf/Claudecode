"""Team ratings from this season's completed games (nflverse results data).

Each team gets an offense and a defense rating, fit by ridge regression on
points scored:

    home_pts = league_avg + home_field + off[home] + def[away]
    away_pts = league_avg +              off[away] + def[home]

Ridge shrinks ratings toward league average, which matters early in the
season when a team has only a handful of games. Predicted margin and total
are turned into probabilities with a normal distribution.
"""

from __future__ import annotations

import csv
import io
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

# Historical NFL standard deviations of final margin and total vs. expectation.
MARGIN_SD = 13.5
TOTAL_SD = 10.5
# Home field is ~1.5-2 points in the modern NFL; 5 games can't estimate it.
HOME_FIELD = 1.7
# Residual variance (~100) / prior variance of a team rating (~16).
RIDGE_LAMBDA = 6.0
# Games per team before the model gets its full blend weight.
FULL_WEIGHT_GAMES = 8

TEAM_ABBR = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Los Angeles Rams": "LA", "Los Angeles Chargers": "LAC",
    "Las Vegas Raiders": "LV", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "Seattle Seahawks": "SEA", "San Francisco 49ers": "SF", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


class StatsError(RuntimeError):
    pass


def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass
class Game:
    home: str
    away: str
    home_pts: int
    away_pts: int
    neutral: bool


def load_games(season: int, source: str | Path | None = None) -> list[Game]:
    """Completed regular-season games for `season` from a URL or local CSV."""
    url = str(source or GAMES_URL)
    if "://" not in url:
        try:
            text = Path(url).read_text()
        except OSError as e:
            raise StatsError(f"Could not read game results: {e}") from e
    else:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                text = resp.read().decode()
        except urllib.error.URLError as e:
            raise StatsError(f"Could not download game results: {e}") from e

    games = []
    for row in csv.DictReader(io.StringIO(text)):
        if row["season"] != str(season) or row["game_type"] != "REG":
            continue
        if row["home_score"] == "" or row["away_score"] == "":
            continue
        games.append(Game(
            home=row["home_team"],
            away=row["away_team"],
            home_pts=int(row["home_score"]),
            away_pts=int(row["away_score"]),
            neutral=row["location"] == "Neutral",
        ))
    return games


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


@dataclass
class Rating:
    team: str
    offense: float  # points scored above average
    defense: float  # points allowed above average (lower is better)
    games: int

    @property
    def net(self) -> float:
        return self.offense - self.defense


class SeasonModel:
    def __init__(self, games: list[Game], ridge: float = RIDGE_LAMBDA,
                 home_field: float = HOME_FIELD):
        if not games:
            raise StatsError("No completed games found for this season")
        self.home_field = home_field
        self.games_played: dict[str, int] = {}
        for g in games:
            for t in (g.home, g.away):
                self.games_played[t] = self.games_played.get(t, 0) + 1

        teams = sorted(self.games_played)
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)
        size = 1 + 2 * n  # intercept, offenses, defenses

        # Accumulate normal equations X'X and X'y directly; each row has 3 ones.
        xtx = [[0.0] * size for _ in range(size)]
        xty = [0.0] * size

        def add_row(off_team: str, def_team: str, y: float) -> None:
            cols = (0, 1 + idx[off_team], 1 + n + idx[def_team])
            for i in cols:
                xty[i] += y
                for j in cols:
                    xtx[i][j] += 1

        for g in games:
            hfa = 0.0 if g.neutral else home_field
            add_row(g.home, g.away, g.home_pts - hfa)
            add_row(g.away, g.home, g.away_pts)

        for i in range(1, size):
            xtx[i][i] += ridge

        beta = _solve(xtx, xty)
        self.league_avg = beta[0]
        self.ratings = {
            t: Rating(t, beta[1 + idx[t]], beta[1 + n + idx[t]], self.games_played[t])
            for t in teams
        }
        self.num_games = len(games)

    def _rating(self, team: str) -> Rating:
        return self.ratings.get(team, Rating(team, 0.0, 0.0, 0))

    def predict(self, home: str, away: str, neutral: bool = False) -> tuple[float, float]:
        """Expected (home_pts, away_pts) for abbreviated team names."""
        h, a = self._rating(home), self._rating(away)
        hfa = 0.0 if neutral else self.home_field
        return (self.league_avg + hfa + h.offense + a.defense,
                self.league_avg + a.offense + h.defense)

    def sample_weight(self, home: str, away: str) -> float:
        """0..1 confidence based on the less-experienced team's games played."""
        played = min(self._rating(home).games, self._rating(away).games)
        return min(1.0, played / FULL_WEIGHT_GAMES)

    def sample_weight_for(self, home_name: str, away_name: str) -> float:
        home, away = TEAM_ABBR.get(home_name), TEAM_ABBR.get(away_name)
        if home is None or away is None:
            return 0.0
        return self.sample_weight(home, away)

    def outcome_probabilities(
        self, home_name: str, away_name: str, market: str, outcomes: list[dict]
    ) -> dict[str, float] | None:
        """Model probability for each outcome of a two-way market, normalized
        to sum to 1 so it is comparable with no-vig market probabilities."""
        home, away = TEAM_ABBR.get(home_name), TEAM_ABBR.get(away_name)
        if home is None or away is None:
            return None
        home_pts, away_pts = self.predict(home, away)
        margin = home_pts - away_pts

        raw = {}
        for o in outcomes:
            name, point = o["name"], o.get("point") or 0.0
            if market == "totals":
                p_over = 1 - normal_cdf((point - (home_pts + away_pts)) / TOTAL_SD)
                raw[name] = p_over if name == "Over" else 1 - p_over
            elif market in ("h2h", "spreads"):
                team_margin = margin if name == home_name else -margin
                raw[name] = normal_cdf((team_margin + point) / MARGIN_SD)
            else:
                return None
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}
