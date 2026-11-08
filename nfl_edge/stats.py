"""Team ratings from this season's completed games.

Each team gets an offense and a defense rating, fit by ridge regression on
points scored:

    home_pts = league_avg + home_field + off[home] + def[away]
    away_pts = league_avg +              off[away] + def[home]

Ridge shrinks ratings toward league average, which matters early in the
season when a team has only a handful of games. Predicted margin and total
are turned into probabilities with a normal distribution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


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


def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass
class Game:
    home: str
    away: str
    home_pts: int
    away_pts: int
    neutral: bool


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
                 home_field: float = HOME_FIELD, prior: dict[str, float] | None = None):
        """`prior` maps team -> expected net points per game vs. average (split
        evenly between offense and defense); ridge pulls ratings toward it
        instead of toward 0. Teams with no games fall back to their prior."""
        if not games:
            raise ValueError("No completed games found for this season")
        self.home_field = home_field
        self.prior = prior or {}
        self.games_played: dict[str, int] = {}
        for g in games:
            for t in (g.home, g.away):
                self.games_played[t] = self.games_played.get(t, 0) + 1

        # Each game gives two rows: points = avg + offense[scorer] + defense[opponent].
        rows = []
        for g in games:
            hfa = 0.0 if g.neutral else home_field
            rows.append((g.home, g.away, g.home_pts - hfa))
            rows.append((g.away, g.home, g.away_pts))

        teams = sorted(self.games_played)
        off_prior = {t: self.prior.get(t, 0.0) / 2 for t in teams}
        def_prior = {t: -self.prior.get(t, 0.0) / 2 for t in teams}
        off, dfn = dict(off_prior), dict(def_prior)
        by_off: dict[str, list[tuple[str, float]]] = {t: [] for t in teams}
        by_def: dict[str, list[tuple[str, float]]] = {t: [] for t in teams}
        for o, d, y in rows:
            by_off[o].append((d, y))
            by_def[d].append((o, y))

        # Ridge regression solved by backfitting (coordinate descent). It
        # converges to the exact solution and scales to 130+ college teams.
        avg = sum(y for _, _, y in rows) / len(rows)
        for _ in range(500):
            change = 0.0
            new_avg = sum(y - off[o] - dfn[d] for o, d, y in rows) / len(rows)
            change = max(change, abs(new_avg - avg))
            avg = new_avg
            for t in teams:
                v = (sum(y - avg - dfn[d] for d, y in by_off[t]) + ridge * off_prior[t]) / (
                    len(by_off[t]) + ridge)
                change, off[t] = max(change, abs(v - off[t])), v
            for t in teams:
                v = (sum(y - avg - off[o] for o, y in by_def[t]) + ridge * def_prior[t]) / (
                    len(by_def[t]) + ridge)
                change, dfn[t] = max(change, abs(v - dfn[t])), v
            if change < 1e-7:
                break

        self.league_avg = avg
        self.ratings = {t: Rating(t, off[t], dfn[t], self.games_played[t]) for t in teams}
        self.num_games = len(games)

    def _rating(self, team: str) -> Rating:
        if team in self.ratings:
            return self.ratings[team]
        net = self.prior.get(team, 0.0)
        return Rating(team, net / 2, -net / 2, 0)

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


def probabilities_from_points(home_name: str, home_pts: float, away_pts: float,
                              market: str, outcomes: list[dict], margin_sd: float = MARGIN_SD,
                              total_sd: float = TOTAL_SD) -> dict[str, float] | None:
    """Probability of each outcome of a two-way market given projected points,
    normalized to sum to 1 so it is comparable with no-vig market odds."""
    margin = home_pts - away_pts
    raw = {}
    for o in outcomes:
        name, point = o["name"], o.get("point") or 0.0
        if market == "totals":
            p_over = 1 - normal_cdf((point - (home_pts + away_pts)) / total_sd)
            raw[name] = p_over if name == "Over" else 1 - p_over
        elif market in ("h2h", "spreads"):
            team_margin = margin if name == home_name else -margin
            raw[name] = normal_cdf((team_margin + point) / margin_sd)
        else:
            return None
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}
