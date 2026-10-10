"""SAMPLE DATA ONLY. Entirely fictional teams, players and odds, generated deterministically
per ISO week so the app can be demoed offline. These are NOT real lines. Do not bet on them."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .odds_math import decimal_to_american
from .providers import SPORTS, Provider, SourceStatus

NFL_TEAMS = ["Metro Mustangs", "Harbor Hawks", "Summit Stags", "Prairie Pumas", "Bayou Barons", "Granite Grizzlies",
             "Lakeshore Lynx", "Canyon Condors", "Ridge Rams", "Delta Dragons", "Cedar Comets", "Tidewater Tritons",
             "Mesa Marauders", "Foothill Falcons", "Capital Cobras", "Riverbend Rhinos"]
CFB_TEAMS = [f"{p} {m}" for p, m in zip(
    ["Northern Plains", "Coastal Tech", "Mountain State", "Lakeland", "Valley A&M", "Pacific Union", "Heartland", "Southern Methodist Sample",
     "Bluegrass", "Piedmont", "Gulf Coast", "Highland", "Prairie State", "Eastern Ridge", "Cascade", "Sunbelt",
     "Midwest Poly", "Tidewater State", "Redwood", "Canyon State", "Great Basin", "Appalachian Sample", "Delta State", "Ozark"],
    ["Bison", "Engineers", "Wolves", "Lions", "Aggies", "Seahawks", "Bulldogs", "Mavericks", "Colonels", "Panthers", "Gators",
     "Fighting Elk", "Hornets", "Eagles", "Owls", "Scorpions", "Cyclones", "Pirates", "Giants", "Rattlers", "Miners", "Trailblazers", "Catfish", "Razorbacks"])]
BOOKS = [  # key, title, vig, noise sd, weight-class
    ("pinnacle", "Pinnacle (sample)", 0.025, 0.004),
    ("draftkings", "DraftKings (sample)", 0.048, 0.014),
    ("fanduel", "FanDuel (sample)", 0.048, 0.014),
    ("betmgm", "BetMGM (sample)", 0.052, 0.016),
    ("caesars", "Caesars (sample)", 0.052, 0.016),
    ("betrivers", "BetRivers (sample)", 0.05, 0.018),
]
PROPS = [("player_pass_yds", "Passing Yards", 245.5), ("player_rush_yds", "Rushing Yards", 68.5), ("player_reception_yds", "Receiving Yards", 61.5)]


def _price(p: float, vig: float) -> int:
    q = min(0.97, max(0.03, p * (1 + vig)))
    return decimal_to_american(1 / q)


def _two_way(rng, true_p, book, line_shift=False):
    _, _, vig, sd = book
    p = true_p + rng.gauss(0, sd)
    if rng.random() < 0.07:  # occasional stale/outlier price -> a genuine (sample) edge
        p += rng.choice([-1, 1]) * rng.uniform(0.03, 0.06)
    p = min(0.95, max(0.05, p))
    return _price(p, vig), _price(1 - p, vig)


def build_events(sport_key: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    iso = now.isocalendar()
    rng = random.Random(f"{sport_key}-{iso[0]}-{iso[1]}")
    teams = NFL_TEAMS if sport_key == "americanfootball_nfl" else CFB_TEAMS
    pool = teams[:]
    rng.shuffle(pool)
    n_games = len(pool) // 2 if sport_key == "americanfootball_nfl" else 12
    days = [3, 6, 6, 6, 0] if sport_key == "americanfootball_nfl" else [5]
    events = []
    for i in range(n_games):
        home, away = pool[2 * i], pool[2 * i + 1]
        true_home = min(0.88, max(0.12, rng.gauss(0.55, 0.2)))
        spread = round(-(true_home - 0.5) * 30 * 2) / 2 or -0.5  # home spread
        total = round(rng.uniform(40, 58) * 2) / 2
        # start in the future: next occurrence of chosen weekday (offset days from Monday)
        d = rng.choice(days)
        start = (now + timedelta(days=1 + (d - now.weekday() - 1) % 7)).replace(hour=rng.choice([17, 20, 23]), minute=0, second=0, microsecond=0)
        books = []
        for b in BOOKS:
            key, title, vig, sd = b
            markets = []
            h, a = _two_way(rng, true_home, b)
            markets.append({"key": "h2h", "outcomes": [{"name": home, "price": h}, {"name": away, "price": a}]})
            hp, ap = _two_way(rng, 0.5, b)
            shift = 0.5 if (key not in ("pinnacle", "draftkings") and rng.random() < 0.1) else 0  # a book on a different line
            markets.append({"key": "spreads", "outcomes": [{"name": home, "price": hp, "point": spread + shift},
                                                           {"name": away, "price": ap, "point": -(spread + shift)}]})
            op, up = _two_way(rng, 0.5, b)
            markets.append({"key": "totals", "outcomes": [{"name": "Over", "price": op, "point": total}, {"name": "Under", "price": up, "point": total}]})
            books.append({"key": key, "title": title, "source": "SAMPLE DATA (fictional)", "markets": markets})
        if sport_key == "americanfootball_nfl" and i < 4:  # a few fictional player props
            mk, label, line = PROPS[i % len(PROPS)]
            player = f"{home.split()[0]} Sample-{i + 1}"
            for b in BOOKS:
                op, up = _two_way(rng, 0.5, b)
                bk = next(x for x in books if x["key"] == b[0])
                bk["markets"].append({"key": mk, "outcomes": [
                    {"name": "Over", "description": player, "price": op, "point": line},
                    {"name": "Under", "description": player, "price": up, "point": line}]})
        events.append({"id": f"sample-{sport_key[-4:]}-{i}", "sport_key": sport_key, "league": SPORTS[sport_key],
                       "home_team": home, "away_team": away, "commence_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "bookmakers": books})
    return events


class SampleProvider(Provider):
    name = "sample"
    label = "SAMPLE DATA (fictional teams and odds, not real)"
    is_sample = True

    def fetch_events(self, sport_key):
        ev = build_events(sport_key)
        return ev, SourceStatus(name=self.label, ok=True, detail=f"{len(ev)} fictional events", events=len(ev), from_cache=False,
                                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
