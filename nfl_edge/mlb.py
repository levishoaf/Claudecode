"""MLB: games, scores, probable starters and DraftKings lines from ESPN, and a game model.

Each game's moneyline and run line (FanDuel always offers both, the run line at 1.5)
and its total runs. The chance starts from the betting line with the sportsbook's cut
taken out and is nudged by the model (team Elo ratings and the starting pitchers'
ERAs) by MARKET_WEIGHT, a share tested on past games' closing lines
(`python -m nfl_edge.mlb --write` refits mlb_weights.json and prints the test)."""

from __future__ import annotations

import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import data

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={}"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary?event={}"
FIRST_SEASON = 2021
TODAY_MAX_AGE = 10 * 60  # lines and starters move during the day
RECENT_MAX_AGE = 3 * 3600
WEIGHTS_FILE = Path(__file__).with_name("mlb_weights.json")


def _get(url: str, tries: int = 4) -> dict:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * 2 ** i)  # ESPN turns away bursts; wait and retry


def american(text) -> int | None:
    t = str(text).strip().upper()
    if t in ("EVEN", "EV"):
        return 100
    try:
        return int(float(t.replace("+", "")))
    except ValueError:
        return None


def implied(odds: int | None) -> float | None:
    if odds is None or odds == 0:
        return None
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def no_vig(a: int | None, b: int | None) -> float | None:
    """The first side's chance from a pair of prices, the sportsbook's cut taken out."""
    pa, pb = implied(a), implied(b)
    if pa is None or pb is None:
        return None
    return pa / (pa + pb)


def _lines(odds: dict) -> dict:
    """{home_ml, away_ml, home_rl_line, home_rl, away_rl, total, over, under} from an
    ESPN odds entry (scoreboard or game page); missing prices are None."""
    out: dict = {}
    ml = odds.get("moneyline") or {}
    for side in ("home", "away"):
        close = ((ml.get(side) or {}).get("close") or {})
        out[f"{side}_ml"] = american(close.get("odds"))
        if out[f"{side}_ml"] is None:  # the game page's simpler shape
            out[f"{side}_ml"] = american((odds.get(f"{side}TeamOdds") or {}).get("moneyLine"))
    rl = odds.get("pointSpread") or {}
    for side in ("home", "away"):
        close = ((rl.get(side) or {}).get("close") or {})
        out[f"{side}_rl"] = american(close.get("odds"))
        if side == "home":
            try:
                out["home_rl_line"] = float(close.get("line"))
            except (TypeError, ValueError):
                out["home_rl_line"] = None
    total = odds.get("total") or {}
    out["total"] = odds.get("overUnder")
    out["over"] = american(((total.get("over") or {}).get("close") or {}).get("odds")) \
        or american(odds.get("overOdds"))
    out["under"] = american(((total.get("under") or {}).get("close") or {}).get("odds")) \
        or american(odds.get("underOdds"))
    return out


def _starter(team: dict) -> dict:
    for p in team.get("probables") or []:
        era = next((s.get("displayValue") for s in p.get("statistics") or []
                    if s.get("abbreviation") == "ERA"), None)
        try:
            era = float(era)
        except (TypeError, ValueError):
            era = None
        return {"id": (p.get("athlete") or {}).get("id"),
                "name": (p.get("athlete") or {}).get("displayName", ""), "era": era}
    return {}


def _game(e: dict) -> dict | None:
    comp = (e.get("competitions") or [{}])[0]
    teams = {t.get("homeAway"): t for t in comp.get("competitors") or []}
    if "home" not in teams or "away" not in teams:
        return None
    status = (comp.get("status") or {}).get("type") or {}
    home, away = teams["home"], teams["away"]

    def score(t):
        try:
            return int(t.get("score"))
        except (TypeError, ValueError):
            return None
    odds = (comp.get("odds") or [None])[0]
    return {
        "id": e.get("id"), "date": e.get("date"),
        "season_type": (e.get("season") or {}).get("type"),
        "home": home["team"]["abbreviation"], "away": away["team"]["abbreviation"],
        "home_name": home["team"]["displayName"], "away_name": away["team"]["displayName"],
        "home_score": score(home), "away_score": score(away),
        "state": status.get("state", ""), "done": bool(status.get("completed")),
        "status": status.get("name", ""),
        "home_sp": _starter(home), "away_sp": _starter(away),
        "lines": _lines(odds) if odds else None,
    }


def day(when: date, max_age: float | None = None) -> list[dict]:
    """Every game on a day (US date), cached: past days for good, today briefly."""
    path = data.cache_dir() / "mlb" / f"{when:%Y%m%d}.json"
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
        raise data.DataError("Could not reach ESPN for the MLB schedule")
    games = [g for g in (_game(e) for e in events) if g]
    path.write_text(json.dumps(games))
    return games


def season_days(year: int) -> list[date]:
    return [date(year, 3, 15) + timedelta(days=i) for i in range(235)]  # to early November


def _day_or_none(when: date) -> list[dict]:
    """A past day ESPN can't send right now is skipped (and tried again next time)
    instead of stopping the whole board."""
    try:
        return day(when)
    except data.DataError:
        return []


def season(year: int) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    days = [d for d in season_days(year) if d <= today + timedelta(days=7)]
    with ThreadPoolExecutor(8) as pool:
        out = [g for games in pool.map(_day_or_none, days) for g in games]
    return out


def history(first: int = FIRST_SEASON, last: int | None = None) -> list[dict]:
    last = last or datetime.now(timezone.utc).year
    games = [g for y in range(first, last + 1) for g in season(y)]
    seen, out = set(), []
    for g in sorted(games, key=lambda g: (g["date"] or "", g["id"] or "")):
        if g["id"] not in seen and g.get("season_type") in (2, 3):  # regular season, playoffs
            seen.add(g["id"])
            out.append(g)
    return out


# ---------------------------------------------------------------- the model

START_ELO = 1500.0
K = 4.0  # baseball is noisy: each game moves a rating a little
HOME_ELO = 24.0
SEASON_CARRY = 0.67  # share of a rating kept over the winter
LEAGUE_ERA = 4.2
ERA_PRIOR = 60.0  # innings-like weight of the league ERA in a starter's prior
START_PRIOR = 6.0  # starts of league average mixed into this season's runs allowed
FEATURES = ["elo", "home", "sp_now"]  # last season's ERA tested as no help: left out


class Ratings:
    """Team ratings and starters' records going into each game, built in date order."""

    def __init__(self, last_era: dict | None = None):
        self.elo: dict[str, float] = {}
        self.year: int | None = None
        self.last_era = last_era or {}  # {(season, pitcher id): ERA the season before}
        self.starts: dict[tuple, list] = {}  # (season, pitcher id): runs allowed in his starts
        self.league_runs = 4.4

    def _new_season(self, year: int) -> None:
        if self.year != year:
            self.elo = {t: START_ELO + SEASON_CARRY * (r - START_ELO) for t, r in self.elo.items()}
            self.year = year

    def _sp(self, year: int, sp: dict) -> tuple[float, float]:
        """(last season's ERA vs league, this season's runs allowed in his starts vs
        league), each shrunk toward average; 0 when unknown."""
        pid = sp.get("id")
        last = self.last_era.get((year, pid))
        last_gap = 0.0 if last is None else (last - LEAGUE_ERA) * 0.5
        runs = self.starts.get((year, pid), [])
        now_gap = (sum(runs) - len(runs) * self.league_runs) / (len(runs) + START_PRIOR)
        return last_gap, now_gap

    def features(self, g: dict) -> dict:
        year = int(g["date"][:4])
        self._new_season(year)
        h, a = self.elo.get(g["home"], START_ELO), self.elo.get(g["away"], START_ELO)
        h_last, h_now = self._sp(year, g.get("home_sp") or {})
        a_last, a_now = self._sp(year, g.get("away_sp") or {})
        return {"elo": (h - a) / 100, "home": 1.0,
                "sp_last": a_last - h_last,  # the away starter worse = good for home
                "sp_now": a_now - h_now}

    def update(self, g: dict) -> None:
        if not g.get("done") or g.get("home_score") is None or g["home_score"] == g["away_score"]:
            return
        year = int(g["date"][:4])
        self._new_season(year)
        h, a = self.elo.get(g["home"], START_ELO), self.elo.get(g["away"], START_ELO)
        expect = 1 / (1 + 10 ** ((a - h - HOME_ELO) / 400))
        won = 1.0 if g["home_score"] > g["away_score"] else 0.0
        margin = math.log1p(abs(g["home_score"] - g["away_score"]))
        self.elo[g["home"]] = h + K * margin * (won - expect)
        self.elo[g["away"]] = a - K * margin * (won - expect)
        for side, opp in (("home", "away"), ("away", "home")):
            pid = (g.get(f"{side}_sp") or {}).get("id")
            if pid:
                self.starts.setdefault((year, pid), []).append(g[f"{opp}_score"])
        self.league_runs += ((g["home_score"] + g["away_score"]) / 2 - self.league_runs) / 2000


def last_eras(games: list[dict]) -> dict:
    """{(season, pitcher id): his ERA the season before}. ESPN lists a finished season's
    final ERA on every start, so the season before is the newest one free of the future."""
    final: dict[tuple, float] = {}
    for g in games:
        for side in ("home", "away"):
            sp = g.get(f"{side}_sp") or {}
            if sp.get("id") and sp.get("era") is not None and g.get("done"):
                final[(int(g["date"][:4]), sp["id"])] = sp["era"]
    return {(year + 1, pid): era for (year, pid), era in final.items()}


def replay(games: list[dict]):
    """(Ratings after every finished game, [(game, features)] taken before each game)."""
    ratings, rows = Ratings(last_eras(games)), []
    for g in games:
        if g.get("done") and g.get("home_score") is not None:
            rows.append((g, ratings.features(g)))
        ratings.update(g)
    return ratings, rows


# ---------------------------------------------------------------- closing lines (testing)

def closing_lines(game_ids: list[str]) -> dict[str, dict]:
    """{game id: DraftKings closing lines} from each game's ESPN page, cached. The pages
    are large, so this is used on a sample of games to test the model."""
    path = data.cache_dir() / "mlb" / "closing_lines.json"
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
            for gid, lines in pool.map(fetch, todo):
                if lines is not None:
                    known[gid] = lines
        path.write_text(json.dumps(known))
    if not game_ids:  # everything cached
        return {i: v for i, v in known.items() if v}
    return {i: known[i] for i in game_ids if known.get(i)}


# ---------------------------------------------------------------- fitting and testing

def _ll(items) -> float:
    return sum(-math.log(min(max(p if y else 1 - p, 1e-6), 1)) for p, y in items) / len(items)


def build() -> dict:
    """Fit the model on 2022-2025, test it and the betting line on this season's games
    that have closing lines, and fit how often a team wins by 2+ runs (the run line)
    for games with no line posted yet."""
    from .signals import fit_weights, logit, sigmoid

    games = history()
    _, rows = replay(games)
    decided = [(g, f) for g, f in rows if g["home_score"] != g["away_score"]]
    train = [(0.5, f, g["home_score"] > g["away_score"]) for g, f in decided
             if "2022" <= g["date"] < "2026"]
    weights = fit_weights(train, FEATURES, ridge=1.0)

    def model(f):
        return sigmoid(sum(weights[n] * f[n] for n in FEATURES))
    # Run line without a posted line: chance of winning by 2+ from the win chance.
    cover = {}
    for side, sign in (("home", 1), ("away", -1)):
        pts = [(0.5, {"bias": 1.0, "p": logit(model(f) if side == "home" else 1 - model(f))},
                sign * (g["home_score"] - g["away_score"]) >= 2) for g, f in decided
               if "2022" <= g["date"] < "2026"]
        cover[side] = fit_weights(pts, ["bias", "p"], ridge=1.0)
    lines = closing_lines([])  # whatever sample is cached
    test = [(model(f), no_vig(lines[g["id"]]["home_ml"], lines[g["id"]]["away_ml"]),
             g["home_score"] > g["away_score"]) for g, f in decided
            if g["date"] >= "2026" and lines.get(g["id"], {}).get("home_ml")
            and lines[g["id"]].get("away_ml")]
    # Each team's rating as the newest season opened, so a fresh install only downloads
    # that season (see current_ratings).
    newest = max(int(g["date"][:4]) for g in games)
    opening = Ratings()
    for g in games:
        if int(g["date"][:4]) >= newest:
            break
        opening.update(g)
    opening._new_season(newest)
    result = {"weights": weights, "cover": cover, "market_share": 1.0,
              "season_start": {str(newest): {t: round(r, 2) for t, r in opening.elo.items()}}}
    if test:
        result["test"] = {
            "games": len(test),
            "model": {"log_loss": round(_ll([(q, y) for q, _, y in test]), 4),
                      "likelier_won": round(sum((q >= .5) == y for q, _, y in test) / len(test), 4)},
            "market": {"log_loss": round(_ll([(m, y) for _, m, y in test]), 4),
                       "likelier_won": round(sum((m >= .5) == y for _, m, y in test) / len(test), 4)},
            "note": "the line beat the model and no mix of the two helped, so a posted "
                    "line's chance is used as is (market_share 1.0)",
        }
    return result


_weights: dict | None = None


def weights() -> dict:
    global _weights
    if _weights is None:
        _weights = json.loads(WEIGHTS_FILE.read_text()) if WEIGHTS_FILE.exists() else {}
    return _weights


# ---------------------------------------------------------------- games to bet

def season_week(when: date) -> int:
    """Weeks since the season opened (late March), for grouping Bet History."""
    return max(1, (when - date(when.year, 3, 20)).days // 7 + 1)


def us_date(g: dict) -> date:
    from .board import EASTERN

    return datetime.fromisoformat(g["date"].replace("Z", "+00:00")).astimezone(EASTERN).date()


def upcoming_days(now: datetime | None = None, ahead: int = 6) -> list[date]:
    """The next few days that have MLB games still to start."""
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
        if any(g["state"] == "pre" for g in games):
            out.append(d)
    return out


def current_ratings() -> Ratings:
    """Ratings after the latest game. The builds carry each team's rating at the start of
    the newest season (mlb_weights.json "season_start"), so only this season's days are
    downloaded; without it, every season since FIRST_SEASON is replayed."""
    year = datetime.now(timezone.utc).year
    start = (weights().get("season_start") or {}).get(str(year))
    if not start:
        ratings, _ = replay(history())
        return ratings
    games = history(year, year)
    ratings = Ratings()
    ratings.elo, ratings.year = dict(start), year
    for g in games:
        ratings.update(g)
    return ratings


def chances(g: dict, ratings: Ratings) -> dict:
    """{'home', 'home_rl', 'away_rl', 'over', 'source'}: the home team's win chance, each
    side's run line chance (at the posted line, or +-1.5), and the over's chance at the
    posted total (None without one)."""
    from .signals import logit, sigmoid

    w = weights()
    if not w:
        raise data.DataError("The MLB model isn't fitted yet (python -m nfl_edge.mlb --write)")
    f = ratings.features(g)
    model = sigmoid(sum(w["weights"].get(n, 0.0) * f[n] for n in FEATURES))
    lines = g.get("lines") or {}
    market = no_vig(lines.get("home_ml"), lines.get("away_ml"))
    share = w.get("market_share", 1.0)
    home = model if market is None else sigmoid(share * logit(market) + (1 - share) * logit(model))
    out = {"home": home, "source": "DraftKings line" if market is not None else "MLB model",
           "rl_line": lines.get("home_rl_line") or -1.5, "over": None, "total": lines.get("total")}
    rl = no_vig(lines.get("home_rl"), lines.get("away_rl"))
    if rl is not None and out["rl_line"] in (-1.5, 1.5):
        out["home_rl"], out["away_rl"] = rl, 1 - rl
    else:  # no run line posted: home -1.5 / away +1.5 from the win chance
        c = w["cover"]["home"]
        out["rl_line"] = -1.5
        out["home_rl"] = sigmoid(c["bias"] + c["p"] * logit(home))
        out["away_rl"] = 1 - out["home_rl"]
    if lines.get("over") and lines.get("under") and lines.get("total"):
        out["over"] = no_vig(lines["over"], lines["under"])
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

    p = argparse.ArgumentParser(description="Fit and test the MLB game model.")
    p.add_argument("--write", action="store_true", help=f"save to {WEIGHTS_FILE.name}")
    args = p.parse_args(argv)
    result = build()
    print(json.dumps(result, indent=1))
    if args.write:
        WEIGHTS_FILE.write_text(json.dumps(result, indent=1) + "\n")
        print(f"wrote {WEIGHTS_FILE}")


if __name__ == "__main__":
    main()
