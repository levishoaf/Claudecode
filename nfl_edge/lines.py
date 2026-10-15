"""Real odds from every sportsbook (The Odds API), for the server and the app.

For each line FanDuel offers, game lines and player props alike: FanDuel's price, the
best price at any US sportsbook, the true chance (every book's price with its cut
taken out, sharp books such as Pinnacle counting most) and the expected value of
betting it at FanDuel and at the best price.

Credits are spent carefully (see Feed): game lines refresh every MAIN_MINUTES, props
every PROP_MINUTES (more often close to the game), and nothing is fetched once the
plan's credits fall to MIN_CREDITS. A failed pull never stops the boards: the last
good pull is used, or none.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import NormalDist

from . import data
from .odds import DEVIG_METHODS, expected_value, implied_probability

API = "https://api.the-odds-api.com/v4"
SPORT_KEYS = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf",
              "mlb": "baseball_mlb", "ufc": "mma_mixed_martial_arts"}
MAIN_MARKETS = {"nfl": ["h2h", "spreads", "totals"], "ncaaf": ["h2h", "spreads", "totals"],
                "mlb": ["h2h", "spreads", "totals"], "ufc": ["h2h"]}
PROP_MARKETS = {
    "nfl": ["player_pass_yds", "player_pass_tds", "player_rush_yds", "player_reception_yds",
            "player_receptions", "player_anytime_td"],
    "ncaaf": ["player_pass_yds", "player_rush_yds", "player_reception_yds",
              "player_anytime_td"],
    "mlb": ["batter_hits", "batter_total_bases", "batter_home_runs", "pitcher_strikeouts"],
}
PROP_LABELS = {
    "player_pass_yds": "Passing Yds", "player_pass_tds": "Passing TDs",
    "player_rush_yds": "Rushing Yds", "player_reception_yds": "Receiving Yds",
    "player_receptions": "Receptions", "player_anytime_td": "Anytime TD",
    "batter_hits": "Hits", "batter_total_bases": "Total Bases", "batter_home_runs": "Home Runs",
    "pitcher_strikeouts": "Strikeouts",
}
TARGET = "fanduel"
# Sharp books set the true price: their opinion counts most (Pinnacle comes from the
# EU region). Every other book counts once.
SHARP = {"pinnacle": 3.0, "circasports": 2.0, "betonlineag": 1.5, "lowvig": 1.5}
MAIN_MINUTES = float(os.environ.get("ODDS_MAIN_MINUTES", "30"))
PROP_MINUTES = float(os.environ.get("ODDS_PROP_MINUTES", "240"))
PROP_SOON_MINUTES = float(os.environ.get("ODDS_PROP_SOON_MINUTES", "45"))  # within 3 hours
SOON = timedelta(hours=3)
PROP_GAMES = {"nfl": 16, "ncaaf": 12, "mlb": 15}  # most games whose props are pulled
PROP_DAYS = {"nfl": 7, "ncaaf": 7, "mlb": 2}  # how far ahead props are pulled
MIN_CREDITS = int(os.environ.get("ODDS_MIN_CREDITS", "50"))  # keep this many in reserve


class OddsError(RuntimeError):
    pass


# ---------------------------------------------------------------- fetching

def _get(path: str, params: dict, key: str) -> tuple[object, int | None]:
    query = urllib.parse.urlencode(dict(params, apiKey=key, oddsFormat="american"))
    try:
        with urllib.request.urlopen(f"{API}{path}?{query}", timeout=30) as resp:
            left = resp.headers.get("x-requests-remaining")
            return json.load(resp), int(float(left)) if left else None
    except urllib.error.HTTPError as e:
        raise OddsError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}") from e
    except (OSError, ValueError) as e:
        raise OddsError(f"couldn't reach The Odds API ({getattr(e, 'reason', e)})") from e


@dataclass
class Feed:
    """Pulls and caches odds, spending credits only when a pull is due.

    `get` is the fetcher (path, params) -> (json, credits left); tests pass a fake."""
    key: str | None = None
    folder: Path | None = None
    get: object = None
    credits: int | None = None
    pulled: dict = field(default_factory=dict)  # cache name -> unix time

    def __post_init__(self):
        self.key = self.key or os.environ.get("ODDS_API_KEY")
        self.folder = Path(self.folder or data.cache_dir() / "odds")
        self.folder.mkdir(parents=True, exist_ok=True)
        self._custom = self.get is not None
        if self.get is None:
            self.get = lambda path, params: _get(path, params, self.key)
        state = self.folder / "state.json"
        if state.exists():
            saved = json.loads(state.read_text())
            self.credits, self.pulled = saved.get("credits"), saved.get("pulled", {})

    @property
    def enabled(self) -> bool:
        """Off without a key (unless a test passed its own fetcher)."""
        return bool(self.key) or self._custom


    def _save_state(self) -> None:
        (self.folder / "state.json").write_text(json.dumps(
            {"credits": self.credits, "pulled": self.pulled}))

    def _cached(self, name: str, minutes: float, path: str, params: dict):
        """The saved answer if pulled within `minutes`, else a fresh pull (if credits
        allow); the last good answer when a pull fails."""
        file = self.folder / f"{name}.json"
        if not self.enabled:
            return None
        fresh = time.time() - self.pulled.get(name, 0) < minutes * 60
        if file.exists() and fresh:
            return json.loads(file.read_text())
        if self.credits is not None and self.credits <= MIN_CREDITS:
            return json.loads(file.read_text()) if file.exists() else None
        try:
            body, left = self.get(path, params)
        except OddsError:
            return json.loads(file.read_text()) if file.exists() else None
        if left is not None:
            self.credits = left
        file.write_text(json.dumps(body))
        self.pulled[name] = time.time()
        self._save_state()
        return body

    def main(self, sport: str) -> list[dict]:
        """Every upcoming game's moneyline, spread and total at every US book and Pinnacle."""
        body = self._cached(f"main-{sport}", MAIN_MINUTES, f"/sports/{SPORT_KEYS[sport]}/odds",
                            {"regions": "us,eu", "markets": ",".join(MAIN_MARKETS[sport])})
        return body if isinstance(body, list) else []

    def props(self, sport: str, events: list[dict], now: datetime | None = None) -> list[dict]:
        """Player props for the soonest games FanDuel lists (see PROP_GAMES, PROP_DAYS):
        each game's odds with its prop markets added."""
        if sport not in PROP_MARKETS:
            return []
        now = now or datetime.now(timezone.utc)
        soon = sorted((e for e in events if now < _start(e) < now + timedelta(days=PROP_DAYS[sport])
                       and any(b["key"] == TARGET for b in e.get("bookmakers", []))),
                      key=_start)[:PROP_GAMES[sport]]
        out = []
        for e in soon:
            minutes = PROP_SOON_MINUTES if _start(e) - now < SOON else PROP_MINUTES
            body = self._cached(f"props-{sport}-{e['id']}", minutes,
                                f"/sports/{SPORT_KEYS[sport]}/events/{e['id']}/odds",
                                {"regions": "us", "markets": ",".join(PROP_MARKETS[sport])})
            if isinstance(body, dict) and body.get("bookmakers"):
                out.append(body)
        return out


def _start(event: dict) -> datetime:
    return datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))


# ---------------------------------------------------------------- pricing

@dataclass
class Offer:
    """One side of one line: FanDuel's price against every book's."""
    sport: str
    event_id: str
    game: str
    start: datetime
    market: str
    subject: str  # the player for a prop, else ""
    side: str  # team, "Over"/"Under", or "Yes" for anytime TD
    point: float | None
    fanduel: int
    best: int
    best_book: str
    chance: float  # true chance: every book's price with its cut taken out
    books: int  # how many other books set the true chance
    sharp: bool  # a sharp book was among them
    check: bool = False  # FanDuel is far from every other book: news or a stale line
    rough: bool = False  # true chance estimated from "Yes"-only prices (an assumed cut)

    @property
    def ev(self) -> float:
        """Expected profit per $1 at FanDuel's price."""
        return expected_value(self.chance, self.fanduel)

    @property
    def best_ev(self) -> float:
        return expected_value(self.chance, self.best)

    @property
    def label(self) -> str:
        if self.market in PROP_LABELS:
            if self.market == "player_anytime_td":
                return f"{self.subject} Anytime TD"
            return f"{self.subject} {self.side} {self.point:g} {PROP_LABELS[self.market]}"
        if self.market == "h2h":
            return f"{self.side} ML"
        if self.market == "totals":
            return f"{self.side} {self.point:g}"
        return f"{self.side} {self.point:+g}"

    def to_json(self) -> dict:
        return {"sport": self.sport, "eventId": self.event_id, "game": self.game,
                "start": self.start.isoformat(), "market": self.market, "label": self.label,
                "subject": self.subject, "side": self.side, "point": self.point,
                "fanduel": self.fanduel, "best": self.best, "bestBook": self.best_book,
                "chance": round(self.chance, 4), "ev": round(self.ev, 4),
                "bestEv": round(self.best_ev, 4), "books": self.books, "sharp": self.sharp,
                "check": self.check, "rough": self.rough}


def _pairs(event: dict) -> dict[tuple, dict[str, dict]]:
    """{(market, subject, line): {book: {side: (price, point)}}}: each two-sided line
    at each book. A spread's line is the home team's point, a total's or a prop's its
    number, so both sides land together."""
    out: dict[tuple, dict[str, dict]] = {}
    home = event.get("home_team")
    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            key = market["key"]
            if key in ("h2h", "spreads") and len(market["outcomes"]) != 2:
                continue  # three-way (draw) markets aren't two-sided
            for o in market["outcomes"]:
                subject = o.get("description", "") if key not in ("h2h", "spreads", "totals") else ""
                point = o.get("point")
                if key == "spreads":
                    line = point if o["name"] == home else (-point if point is not None else None)
                else:
                    line = point
                side = o["name"]
                if key == "player_anytime_td" and side in ("Yes", "Over"):
                    side = "Yes"
                elif key == "player_anytime_td":
                    side = "No"
                out.setdefault((key, subject, line), {}).setdefault(book["key"], {})[side] = (
                    o["price"], point)
    return out


ONE_SIDED_CUT = 1.15  # a "Yes"-only market's typical overround (anytime TD)
MIN_WEIGHT = 2.0  # at least two other books, or one sharp one, must price a line
FAR = 0.12  # FanDuel this far from the market: news or a stale line, not free money


def _sane(prices: list[int]) -> bool:
    """A book's two prices make sense together (an exchange can post junk like
    -10000 on both sides)."""
    total = sum(implied_probability(p) for p in prices)
    return 0.97 <= total <= 1.25


# Yardage props: other books' lines a few yards off FanDuel's are moved to FanDuel's
# number assuming a normal spread of outcomes with this standard deviation (yards).
# Counts (receptions, TDs, strikeouts, hits) only compare at the same number.
SHIFT_SD = {"player_pass_yds": 60.0, "player_rush_yds": 26.0, "player_reception_yds": 26.0}
SHIFT_MAX = 0.25  # at most a quarter of a standard deviation away
SHIFTED_WEIGHT = 0.6  # a moved line counts for less than the same line


def _over_at(p_over: float, from_line: float, to_line: float, sd: float) -> float:
    """P(over to_line) from P(over from_line), on a normal spread of outcomes."""
    p_over = min(max(p_over, 1e-6), 1 - 1e-6)
    z = NormalDist().inv_cdf(1 - p_over)  # from_line sits z sd above the mean
    return 1 - NormalDist().cdf(z + (to_line - from_line) / sd)


def price(event: dict, sport: str, devig: str = "power") -> list[Offer]:
    """Every side FanDuel offers on this game, priced against the other books.
    FanDuel's own price is left out of the true chance it's judged against."""
    fn = DEVIG_METHODS[devig]
    start = _start(event)
    game = f"{event.get('away_team')} @ {event.get('home_team')}"
    pairs = _pairs(event)
    by_subject: dict[tuple, list] = {}
    for (market, subject, line), books in pairs.items():
        by_subject.setdefault((market, subject), []).append((line, books))
    out = []
    for (market, subject, fd_line), books in pairs.items():
        fd = books.get(TARGET)
        if not fd:
            continue
        sides = sorted({s for o in books.values() for s in o})
        totals, weight, used, sharp = {s: 0.0 for s in sides}, 0.0, 0, False
        sd = SHIFT_SD.get(market)
        for line, at_line in by_subject[(market, subject)]:
            same = line == fd_line
            if not same and not (sd and line is not None and fd_line is not None
                                 and abs(line - fd_line) <= SHIFT_MAX * sd):
                continue
            for book, o in at_line.items():
                if book == TARGET or len(sides) != 2 or set(o) != set(sides) \
                        or not _sane([o[s][0] for s in sides]):
                    continue
                probs = dict(zip(sides, fn([implied_probability(o[s][0]) for s in sides])))
                if not same:  # move this book's line to FanDuel's number
                    over = _over_at(probs["Over"], line, fd_line, sd)
                    probs = {"Over": over, "Under": 1 - over}
                w = SHARP.get(book, 1.0) * (1.0 if same else SHIFTED_WEIGHT)
                for s in sides:
                    totals[s] += w * probs[s]
                weight += w
                used += 1
                sharp = sharp or book in SHARP
        others = {b: o for b, o in books.items() if b != TARGET}
        for side, (fd_price, point) in fd.items():
            if weight >= MIN_WEIGHT:
                chance, books_used = totals[side] / weight, used
            elif market == "player_anytime_td" and side == "Yes":
                # Usually posted "Yes" only: the other books' average, less a typical cut.
                yes = [implied_probability(o["Yes"][0]) for o in others.values() if "Yes" in o]
                if len(yes) < 3:
                    continue
                chance, books_used = sum(yes) / len(yes) / ONE_SIDED_CUT, len(yes)
            else:
                continue
            us = {b: o[side][0] for b, o in books.items() if side in o and b != "pinnacle"}
            best_book = max(us, key=lambda b: (us[b], b == TARGET))
            offer = Offer(sport, event["id"], game, start, market, subject, side, point,
                          fd_price, us[best_book], best_book, chance, books_used, sharp)
            offer.check = abs(implied_probability(fd_price) - chance) > FAR
            offer.rough = weight < MIN_WEIGHT
            out.append(offer)
    return out


def board(sport: str, feed: Feed, props: bool = True, now: datetime | None = None) -> list[Offer]:
    """Every line FanDuel offers in a league, game lines and props, priced."""
    events = feed.main(sport)
    offers = [o for e in events for o in price(e, sport)]
    if props:
        offers += [o for e in feed.props(sport, events, now) for o in price(e, sport)]
    now = now or datetime.now(timezone.utc)
    return [o for o in offers if o.start > now]
