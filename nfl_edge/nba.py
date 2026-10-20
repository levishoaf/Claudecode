"""NBA: games, scores and DraftKings lines from ESPN, and a backup game model.

Each game's moneyline, spread and total. A posted line's chance (the sportsbook's cut
taken out) is used as is; the model (team Elo ratings, home court and rest) only fills
in games with no line yet. `python -m nfl_edge.nba --write` refits nba_weights.json and
prints the test against last season's closing lines.

Seasons are named by the year they start: 2026 is the 2026-27 season."""

from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import NormalDist

from . import data
from .mlb import _get, _lines, no_vig

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={}"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={}"
FIRST_SEASON = 2020
TODAY_MAX_AGE = 10 * 60  # lines and injury news move during the day
RECENT_MAX_AGE = 3 * 3600
WEIGHTS_FILE = Path(__file__).with_name("nba_weights.json")
# The 30 teams as ESPN abbreviates them: All-Star and exhibition games are left out.
TEAMS = {"ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GS", "HOU", "IND",
         "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NO", "NY", "OKC", "ORL", "PHI", "PHX",
         "POR", "SAC", "SA", "TOR", "UTAH", "WSH"}


def _score(t: dict) -> int | None:
    try:
        return int(t.get("score"))
    except (TypeError, ValueError):
        return None


def _game(e: dict) -> dict | None:
    comp = (e.get("competitions") or [{}])[0]
    teams = {t.get("homeAway"): t for t in comp.get("competitors") or []}
    if "home" not in teams or "away" not in teams:
        return None
    status = (comp.get("status") or {}).get("type") or {}
    home, away = teams["home"], teams["away"]
    odds = (comp.get("odds") or [None])[0]
    return {
        "id": e.get("id"), "date": e.get("date"),
        "season_type": (e.get("season") or {}).get("type"),
        "home": home["team"].get("abbreviation", ""), "away": away["team"].get("abbreviation", ""),
        "home_name": home["team"].get("displayName", ""),
        "away_name": away["team"].get("displayName", ""),
        "home_score": _score(home), "away_score": _score(away),
        "state": status.get("state", ""), "done": bool(status.get("completed")),
        "status": status.get("name", ""),
        "lines": _lines(odds) if odds else None,
    }


def day(when: date, max_age: float | None = None) -> list[dict]:
    """Every game on a day (US date), cached: past days for good, today briefly."""
    path = data.cache_dir() / "nba" / f"{when:%Y%m%d}.json"
    path.parent.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).date()
    if max_age is None:
        max_age = (float("inf") if when < today - timedelta(days=2)
                   else TODAY_MAX_AGE if when >= today - timedelta(days=1) else RECENT_MAX_AGE)
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        return json.loads(path.read_text())
    try:
        events = _get(SCOREBOARD.format(f"{when:%Y%m%d}")).get("events", [])
    except Exception:
        if path.exists():
            return json.loads(path.read_text())  # stale beats nothing
        raise data.DataError("Could not reach ESPN for the NBA schedule")
    games = [g for g in (_game(e) for e in events) if g]
    path.write_text(json.dumps(games))
    return games


def season_days(year: int) -> list[date]:
    """Early October to the end of July (the 2020-21 finals ran late)."""
    return [date(year, 10, 1) + timedelta(days=i) for i in range(304)]


def _day_or_none(when: date) -> list[dict]:
    """A past day ESPN can't send right now is skipped (and tried again next time)."""
    try:
        return day(when)
    except data.DataError:
        return []


def season(year: int) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    days = [d for d in season_days(year) if d <= today + timedelta(days=7)]
    with ThreadPoolExecutor(8) as pool:
        return [g for games in pool.map(_day_or_none, days) for g in games]


def season_of(when: date) -> int:
    """The season a day belongs to (named by its starting year)."""
    return when.year if when.month >= 9 else when.year - 1


def history(first: int = FIRST_SEASON, last: int | None = None) -> list[dict]:
    last = last if last is not None else season_of(datetime.now(timezone.utc).date())
    games = [g for y in range(first, last + 1) for g in season(y)]
    seen, out = set(), []
    for g in sorted(games, key=lambda g: (g["date"] or "", g["id"] or "")):
        if (g["id"] not in seen and g.get("season_type") in (2, 3, 5)  # season, playoffs, play-in
                and g["home"] in TEAMS and g["away"] in TEAMS):
            seen.add(g["id"])
            out.append(g)
    return out


# ---------------------------------------------------------------- the model

START_ELO = 1500.0
K = 20.0
HOME_ELO = 70.0
SEASON_CARRY = 0.75  # share of a rating kept over the summer
FEATURES = ["elo", "home", "rest"]


def us_date(g: dict) -> date:
    from .board import EASTERN

    return datetime.fromisoformat(g["date"].replace("Z", "+00:00")).astimezone(EASTERN).date()


class Ratings:
    """Team ratings and each team's last game day, built in date order."""

    def __init__(self):
        self.elo: dict[str, float] = {}
        self.year: int | None = None
        self.last: dict[str, date] = {}  # team: the day of its last game

    def _new_season(self, year: int) -> None:
        if self.year != year:
            self.elo = {t: START_ELO + SEASON_CARRY * (r - START_ELO) for t, r in self.elo.items()}
            self.year = year

    def tired(self, team: str, on: date) -> float:
        """1 if the team played the day before (a back-to-back)."""
        return 1.0 if self.last.get(team) == on - timedelta(days=1) else 0.0

    def features(self, g: dict) -> dict:
        on = us_date(g)
        self._new_season(season_of(on))
        h, a = self.elo.get(g["home"], START_ELO), self.elo.get(g["away"], START_ELO)
        return {"elo": (h - a) / 100, "home": 1.0,
                "rest": self.tired(g["away"], on) - self.tired(g["home"], on)}

    def played(self, g: dict) -> None:
        """Note the game day for rest (also for games not finished yet)."""
        on = us_date(g)
        for t in (g["home"], g["away"]):
            if self.last.get(t) is None or self.last[t] < on:
                self.last[t] = on

    def update(self, g: dict) -> None:
        self.played(g)
        if not g.get("done") or g.get("home_score") is None or g["home_score"] == g["away_score"]:
            return
        self._new_season(season_of(us_date(g)))
        h, a = self.elo.get(g["home"], START_ELO), self.elo.get(g["away"], START_ELO)
        expect = 1 / (1 + 10 ** ((a - h - HOME_ELO) / 400))
        won = 1.0 if g["home_score"] > g["away_score"] else 0.0
        gap = (h + HOME_ELO - a) * (1 if won else -1)  # the winner's edge going in
        margin = (abs(g["home_score"] - g["away_score"]) + 3) ** 0.8 / (7.5 + 0.006 * gap)
        self.elo[g["home"]] = h + K * margin * (won - expect)
        self.elo[g["away"]] = a - K * margin * (won - expect)


def replay(games: list[dict]):
    """(Ratings after every finished game, [(game, features)] taken before each game)."""
    ratings, rows = Ratings(), []
    for g in games:
        if g.get("done") and g.get("home_score") is not None:
            rows.append((g, ratings.features(g)))
        ratings.update(g)
    return ratings, rows


# ---------------------------------------------------------------- closing lines (testing)

def closing_lines(game_ids: list[str]) -> dict[str, dict]:
    """{game id: DraftKings closing lines} from each game's ESPN page, cached."""
    path = data.cache_dir() / "nba" / "closing_lines.json"
    path.parent.mkdir(exist_ok=True)
    known = json.loads(path.read_text()) if path.exists() else {}
    todo = [i for i in game_ids if i not in known]

    def fetch(gid):
        try:
            pick = (_get(SUMMARY.format(gid)).get("pickcenter") or [None])[0]
        except Exception:
            return gid, None
        return gid, _lines(pick) if pick else {}
    if todo:
        with ThreadPoolExecutor(8) as pool:
            for gid, got in pool.map(fetch, todo):
                if got is not None:
                    known[gid] = got
        path.write_text(json.dumps(known))
    return {i: known[i] for i in game_ids if known.get(i)}


# ---------------------------------------------------------------- fitting and testing

def _ll(items) -> float:
    return sum(-math.log(min(max(p if y else 1 - p, 1e-6), 1)) for p, y in items) / len(items)


def _brier(items) -> float:
    return sum((p - y) ** 2 for p, y in items) / len(items)


def build(test_sample: int = 600) -> dict:
    """Fit the model on the seasons before last (the first one only warms the ratings
    up), test it and DraftKings' closing line on a sample of last season's games, and
    fit the home team's winning margin from the win chance, for spreads with no line."""
    from .signals import fit_weights, logit, sigmoid

    games = history()
    newest = season_of(max(us_date(g) for g in games))
    test_year = newest - 1  # refit each off-season: last season is the test
    _, rows = replay(games)
    decided = [(g, f) for g, f in rows if g["home_score"] != g["away_score"]]

    def year(g):
        return season_of(us_date(g))
    train = [(0.5, f, g["home_score"] > g["away_score"]) for g, f in decided
             if FIRST_SEASON < year(g) < test_year]
    weights = fit_weights(train, FEATURES, ridge=1.0)

    def model(f):
        return sigmoid(sum(weights[n] * f[n] for n in FEATURES))
    # Winning margin from the win chance: margin ~ slope * logit(p), spread sd.
    pairs = [(logit(model(f)), g["home_score"] - g["away_score"]) for g, f in decided
             if FIRST_SEASON < year(g) < test_year]
    slope = sum(x * y for x, y in pairs) / sum(x * x for x, _ in pairs)
    sd = math.sqrt(sum((y - slope * x) ** 2 for x, y in pairs) / len(pairs))
    tested = [(g, f) for g, f in decided if year(g) == test_year]
    step = max(1, len(tested) // test_sample)
    sample = tested[::step]
    lines = closing_lines([g["id"] for g, _ in sample])
    test = [(model(f), no_vig(lines[g["id"]]["home_ml"], lines[g["id"]]["away_ml"]),
             g["home_score"] > g["away_score"]) for g, f in sample
            if lines.get(g["id"], {}).get("home_ml") and lines[g["id"]].get("away_ml")]
    every = [(model(f), g["home_score"] > g["away_score"]) for g, f in tested]
    # Each team's rating as the newest season opened, so a fresh install only downloads
    # that season (see current_ratings).
    opening = Ratings()
    for g in games:
        if year(g) >= newest:
            break
        opening.update(g)
    opening._new_season(newest)
    result = {"weights": weights, "margin": {"slope": round(slope, 4), "sd": round(sd, 3)},
              "market_share": 1.0,
              "season_start": {str(newest): {t: round(r, 2) for t, r in opening.elo.items()}},
              "test": {
                  "season": f"{test_year}-{(test_year + 1) % 100:02d}",
                  "all_games": {"games": len(every),
                                "likelier_won": round(sum((q >= .5) == y for q, y in every)
                                                      / len(every), 4),
                                "log_loss": round(_ll(every), 4),
                                "brier": round(_brier(every), 4)}}}
    if test:
        result["test"].update({
            "games": len(test),
            "model": {"log_loss": round(_ll([(q, y) for q, _, y in test]), 4),
                      "likelier_won": round(sum((q >= .5) == y for q, _, y in test) / len(test), 4)},
            "market": {"log_loss": round(_ll([(m, y) for _, m, y in test]), 4),
                       "likelier_won": round(sum((m >= .5) == y for _, m, y in test) / len(test), 4)},
        })
    return result


_weights: dict | None = None


def weights() -> dict:
    global _weights
    if _weights is None:
        _weights = json.loads(WEIGHTS_FILE.read_text()) if WEIGHTS_FILE.exists() else {}
    return _weights


# ---------------------------------------------------------------- games to bet

def season_week(when: date) -> int:
    """Weeks since the season opened (late October), for grouping Bet History."""
    start = date(season_of(when), 10, 20)
    return max(1, (when - start).days // 7 + 1)


def upcoming_days(now: datetime | None = None, ahead: int = 6) -> list[date]:
    """The next few days that have NBA games still to start."""
    from .board import EASTERN

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(EASTERN).date()
    out = []
    for i in range(ahead + 1):
        d = today + timedelta(days=i)
        try:
            games = day(d)
        except data.DataError:
            continue
        if any(g["state"] == "pre" and g["home"] in TEAMS for g in games):
            out.append(d)
    return out


def current_ratings() -> Ratings:
    """Ratings after the latest game (every game day noted for rest). The
    builds carry each team's rating at the start of the newest season (nba_weights.json
    "season_start"), so only this season's days are downloaded."""
    year = season_of(datetime.now(timezone.utc).date())
    start = (weights().get("season_start") or {}).get(str(year))
    if not start:
        ratings, _ = replay(history())
    else:
        ratings = Ratings()
        ratings.elo, ratings.year = dict(start), year
        for g in history(year, year):
            ratings.update(g)
    return ratings


def chances(g: dict, ratings: Ratings) -> dict:
    """{'home', 'source', 'spread' (home points), 'home_cover', 'total', 'over'}: the home
    team's win chance, its chance to cover the posted spread (or the model's), and the
    over's chance at the posted total (None without one)."""
    from .signals import logit, sigmoid

    w = weights()
    if not w:
        raise data.DataError("The NBA model isn't fitted yet (python -m nfl_edge.nba --write)")
    f = ratings.features(g)
    model = sigmoid(sum(w["weights"].get(n, 0.0) * f[n] for n in FEATURES))
    lines = g.get("lines") or {}
    market = no_vig(lines.get("home_ml"), lines.get("away_ml"))
    share = w.get("market_share", 1.0)
    home = model if market is None else sigmoid(share * logit(market) + (1 - share) * logit(model))
    out = {"home": home, "source": "NBA DraftKings line" if market is not None else "NBA model",
           "spread": lines.get("home_rl_line"), "over": None, "total": lines.get("total")}
    cover = no_vig(lines.get("home_rl"), lines.get("away_rl"))
    if out["spread"] is not None and cover is not None:
        out["home_cover"] = cover
    else:  # no spread posted: the model's line, about a coin flip either side
        m = w["margin"]
        mean = m["slope"] * logit(home)
        line = -round(mean * 2) / 2  # home points, to the nearest half
        if line == int(line):
            line -= 0.5 if mean > 0 else -0.5  # a half point: no pushes
        out["spread"] = line
        out["home_cover"] = 1 - NormalDist(mean, m["sd"]).cdf(-line)
    if lines.get("over") and lines.get("under") and lines.get("total"):
        out["over"] = no_vig(lines["over"], lines["under"])
    return out


INJURIES = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
INJURY_MAX_AGE = 30 * 60


def injuries() -> dict[str, str]:
    """{player: status} from ESPN's NBA injury report ('Out' or 'Day-To-Day', which is
    the NBA's questionable), cached 30 minutes; {} if it can't be had."""
    path = data.cache_dir() / "nba" / "injuries.json"
    path.parent.mkdir(exist_ok=True)
    if path.exists() and time.time() - path.stat().st_mtime < INJURY_MAX_AGE:
        return json.loads(path.read_text())
    try:
        teams = _get(INJURIES, tries=2).get("injuries", [])
    except Exception:
        return json.loads(path.read_text()) if path.exists() else {}
    out = {i["athlete"]["displayName"]: i.get("status", "")
           for t in teams for i in t.get("injuries", []) if (i.get("athlete") or {}).get("displayName")}
    path.write_text(json.dumps(out))
    return out


def games_by_id(year: int) -> dict[str, dict]:
    """{game id: the game shaped like an nflverse row} for grading (scores blank until final)."""
    out = {}
    for g in season(year):
        final = g["done"] and g["home_score"] is not None
        out[g["id"]] = {"game_id": g["id"], "home_team": g["home_name"],
                        "away_team": g["away_name"],
                        "home_score": g["home_score"] if final else "",
                        "away_score": g["away_score"] if final else ""}
    return out


def game_weeks(year: int) -> dict[str, int]:
    return {g["id"]: season_week(us_date(g)) for g in season(year)}


def main(argv=None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="Fit and test the NBA game model.")
    p.add_argument("--write", action="store_true", help=f"save to {WEIGHTS_FILE.name}")
    args = p.parse_args(argv)
    result = build()
    print(json.dumps({k: v for k, v in result.items() if k != "season_start"}, indent=1))
    if args.write:
        WEIGHTS_FILE.write_text(json.dumps(result, indent=1) + "\n")
        print(f"wrote {WEIGHTS_FILE}")


if __name__ == "__main__":
    main()
