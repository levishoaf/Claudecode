"""Check bets against FanDuel's own odds feed (the data its website loads).

No API key: this reads the same public JSON the FanDuel Sportsbook site uses,
from your computer, for the state you bet in. It is unofficial and
undocumented, so FanDuel can change or block it at any time; automated reading
may also go against FanDuel's terms of use. Use it at your own risk.

verify() keeps only bets it finds listed on FanDuel, at FanDuel's price, and
drops everything else. When the feed can't be read or understood, nothing is
confirmed (FeedError), so an unconfirmed bet is never shown as confirmed.
"""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass

from .odds import american_to_decimal

APP_KEY = "FhMFpcPWXMeyZxOx"  # the public key FanDuel's own website sends
BASE = "https://sbapi.{state}.sportsbook.fanduel.com/api"
PAGE_IDS = {"nfl": "nfl", "ncaaf": "ncaaf"}
# Event-page tabs that hold touchdown scorers, player props and team totals.
EVENT_TABS = ("td-scorer-props", "passing-props", "receiving-props", "rushing-props",
              "team-props", "popular")
STATES = ("az", "co", "ct", "dc", "ia", "il", "in", "ks", "ky", "la", "ma", "md", "me", "mi",
          "nc", "nj", "ny", "oh", "pa", "tn", "va", "vt", "wv", "wy")
CACHE_SECONDS = 15 * 60
TIMEOUT = 12  # seconds per request
TIME_BUDGET = 40  # seconds for all of a check's game pages together
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
           "Accept": "application/json", "Origin": "https://sportsbook.fanduel.com",
           "Referer": "https://sportsbook.fanduel.com/"}
_cache: dict[str, tuple[float, object]] = {}


class FeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Selection:
    event: str        # "Baltimore Ravens @ Atlanta Falcons"
    market: str       # FanDuel's market name, e.g. "Moneyline", "Any Time Touchdown Scorer"
    market_type: str  # e.g. "MONEY_LINE", "MATCH_HANDICAP_(2-WAY)"
    runner: str       # team, "Over"/"Under", or player
    handicap: float | None
    odds: int


# ------------------------------------------------------------------ reading

def _get(url: str, opener=None):
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        raise FeedError(f"FanDuel answered {e.code}"
                        + (" (not available from your location?)" if e.code in (401, 403) else ""))
    except (OSError, http.client.HTTPException) as e:
        raise FeedError(f"couldn't reach FanDuel ({getattr(e, 'reason', e)})")
    except ValueError:
        raise FeedError("FanDuel sent something that isn't odds data")
    _cache[url] = (time.monotonic(), payload)
    return payload


def league_url(state: str, sport: str) -> str:
    q = urllib.parse.urlencode({"page": "CUSTOM", "customPageId": PAGE_IDS[sport],
                                "pbHorizontal": "false", "_ak": APP_KEY,
                                "timezone": "America/New_York"})
    return f"{BASE.format(state=state)}/content-managed-page?{q}"


def event_url(state: str, event_id: str, tab: str) -> str:
    q = urllib.parse.urlencode({"_ak": APP_KEY, "eventId": event_id, "tab": tab,
                                "useCombinedTouchdownsVirtualMarket": "true",
                                "timezone": "America/New_York"})
    return f"{BASE.format(state=state)}/event-page?{q}"


def _attachments(payload) -> list[dict]:
    """Every {"events": ..., "markets": ...} block anywhere in a feed response."""
    found = []

    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("markets"), dict) and isinstance(x.get("events"), dict):
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(payload)
    return found


def _odds(runner: dict) -> int | None:
    win = runner.get("winRunnerOdds") or {}
    am = (win.get("americanDisplayOdds") or {})
    for v in (am.get("americanOddsInt"), am.get("americanOdds"), win.get("americanOdds")):
        try:
            return int(str(v).replace("+", ""))
        except (TypeError, ValueError):
            continue
    return None


def selections(payload) -> tuple[list[Selection], dict[str, str]]:
    """(open selections with prices, {event id: event name}) from a feed response."""
    out, events = [], {}
    for block in _attachments(payload):
        for eid, ev in block["events"].items():
            if isinstance(ev, dict) and ev.get("name"):
                events[str(ev.get("eventId", eid))] = ev["name"]
        for m in block["markets"].values():
            if not isinstance(m, dict) or m.get("marketStatus", "OPEN") != "OPEN":
                continue
            event = events.get(str(m.get("eventId")), "")
            for r in m.get("runners") or []:
                odds = _odds(r)
                if odds is None or r.get("runnerStatus", "ACTIVE") != "ACTIVE":
                    continue
                h = r.get("handicap")
                out.append(Selection(event, m.get("marketName", ""), m.get("marketType", ""),
                                     r.get("runnerName", ""), float(h) if h not in (None, "") else None,
                                     odds))
    return out, events


# ------------------------------------------------------------------ matching

# Words FanDuel uses in market names for each "X+" prop ladder.
LADDER_WORDS = {
    "player_receptions_alternate": ("receptions",),
    "player_reception_yds_alternate": ("receiving yds", "receiving yards"),
    "player_rush_yds_alternate": ("rushing yds", "rushing yards"),
    "player_pass_yds_alternate": ("passing yds", "passing yards"),
    "player_pass_tds_alternate": ("passing tds", "passing touchdowns", "passing td"),
}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower().replace("&", "and")).strip()


def same_event(ours: str, theirs: str) -> bool:
    """'Away @ Home' (ours) against FanDuel's event name, in either team order or wording."""
    teams = [norm(t) for t in re.split(r"\s+@\s+|\s+at\s+|\s+v\.?\s+|\s+vs\.?\s+", ours)]
    t = norm(theirs)
    return len(teams) == 2 and all(team and team in t for team in teams)


def _number(text: str) -> float | None:
    m = re.search(r"[-+]?\d+(\.\d+)?", text)
    return float(m.group()) if m else None


def _line(s: Selection) -> float | None:
    """The selection's line: its handicap, or a number in its name ("Over 43.5")."""
    n = _number(s.runner)
    return s.handicap if s.handicap is not None and (s.handicap != 0 or n is None) else n


def find(b, sels: list[Selection]) -> Selection | None:
    """The FanDuel selection that is exactly this bet, or None."""
    here = [s for s in sels if same_event(b.game, s.event)]
    mt = lambda s: (s.market_type + " " + s.market).upper()  # noqa: E731
    pick, point = b.pick, b.point
    if b.market == "h2h":
        return next((s for s in here if ("MONEY" in mt(s)) and norm(s.runner) == norm(pick)), None)
    if b.market in ("spreads", "alternate_spreads"):
        return next((s for s in here if ("HANDICAP" in mt(s) or "SPREAD" in mt(s))
                     and "TEAM" not in mt(s) and norm(s.runner).startswith(norm(pick))
                     and _line(s) == point), None)
    if b.market in ("totals", "alternate_totals"):
        return next((s for s in here if "TOTAL" in mt(s) and norm(pick) in norm(s.runner)
                     and _line(s) == point  # the game total, not a team's
                     and not any(norm(t) in norm(s.market) for t in b.game.split(" @ "))),
                    None)
    if b.market in ("team_totals", "alternate_team_totals"):
        team, side = pick.rsplit(" ", 1)
        return next((s for s in here if "TOTAL" in mt(s) and norm(team) in norm(s.market)
                     and norm(side) in norm(s.runner) and _line(s) == point), None)
    if b.market in LADDER_WORDS:
        # "X+" ladders: the player and the threshold, in a market for that stat.
        player = norm(pick.rsplit(" ", 1)[0])
        need = int(point + 0.5)  # Over 24.5 -> 25+
        words = LADDER_WORDS[b.market]

        def is_it(s):
            text = norm(s.market + " " + s.runner)
            stat_ok = any(w in norm(s.market) or w in norm(s.runner) for w in words)
            threshold = (re.search(rf"(^| ){need}\+", s.runner.lower()) is not None
                         or (s.handicap is not None and s.handicap in (need - 0.5, float(need))
                             and norm(s.runner).startswith(("over", player))))
            return player in text and stat_ok and threshold and "under" not in norm(s.runner)
        return next((s for s in here if is_it(s)), None)
    if b.market == "player_anytime_td":
        player = pick.rsplit(" ", 1)[0]
        return next((s for s in here if ("ANY" in mt(s) and "TOUCHDOWN" in mt(s))
                     and norm(s.runner) == norm(player)), None)
    return None  # other markets aren't checked: never confirmed


def verify(bets: list, state: str, sport: str = "nfl", opener=None) -> tuple[list, str]:
    """(only the bets found on FanDuel, at FanDuel's price; a note). Raises FeedError
    when FanDuel can't be read, its data can't be understood, or nothing matches, so
    the caller can fall back instead of showing an empty or half-checked board."""
    try:
        return _verify(bets, state, sport, opener)
    except FeedError:
        raise
    except Exception as e:  # FanDuel's data in a shape we don't expect
        raise FeedError(f"FanDuel's data wasn't in the expected format ({type(e).__name__})")


def _verify(bets: list, state: str, sport: str, opener) -> tuple[list, str]:
    state = state.lower()
    if state not in STATES:
        raise FeedError(f"FanDuel isn't available in {state.upper()}")
    league = _get(league_url(state, sport), opener)
    sels, events = selections(league)
    if not events:
        raise FeedError("FanDuel's page had no games in it (its format may have changed)")
    # Props and team totals live on each game's own page; only fetch games we need.
    need = {b.game for b in bets if b.market.startswith("player_")
            or b.market in ("team_totals", "alternate_team_totals")}
    pages = [event_url(state, eid, tab) for eid, name in events.items()
             if any(same_event(g, name) for g in need) for tab in EVENT_TABS]
    # Fetch the game pages side by side, within a time budget: a slow or throttled
    # connection confirms fewer props rather than holding up the whole board.
    pool = ThreadPoolExecutor(max_workers=8)
    futures = [pool.submit(_get, url, opener) for url in pages]
    try:
        for f in as_completed(futures, timeout=TIME_BUDGET):
            try:
                sels += selections(f.result())[0]
            except FeedError:
                continue  # a missing tab only means fewer bets confirmed
    except FuturesTimeout:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    kept = []
    for b in bets:
        s = find(b, sels)
        if s is None:
            continue
        b.fd_price, b.priced, b.on_fanduel = s.odds, True, True
        b.ev = b.fair_prob * american_to_decimal(s.odds) - 1
        kept.append(b)
    if bets and not kept:
        raise FeedError("none of the bets matched FanDuel's listings (its format may have "
                        "changed)")
    dropped = len(bets) - len(kept)
    from .board import EASTERN
    from datetime import datetime

    now = datetime.now(EASTERN)
    stamp = f"{now.hour % 12 or 12}:{now:%M %p} ET"
    note = (f"Checked against FanDuel ({state.upper()}) at {stamp}: {len(kept)} bets confirmed"
            + (f", {dropped} not found and hidden" if dropped else ""))
    return kept, note
