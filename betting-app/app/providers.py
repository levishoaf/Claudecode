"""Pluggable, keyless odds providers.

Every provider returns events in one normalised shape (modelled on the common
"odds API" layout):

    {"id", "sport_key", "league", "home_team", "away_team", "commence_time" (ISO, Z),
     "bookmakers": [{"key", "title", "source",
                     "markets": [{"key": "h2h"|"spreads"|"totals"|<prop>,
                                  "outcomes": [{"name", "price" (American), "point"?, "description"?}]}]}]}

Providers are READ-ONLY and unauthenticated. No logins, no credentials, no API keys,
no bet placement, and no attempt to bypass blocks (a 403 / geo-block / bot-wall is
recorded as-is and the provider is skipped). One request per league per run, with a
polite delay and an on-disk cache.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

USER_AGENT = "weekly-picks-research/1.0 (personal, read-only, low-rate; contact: local user)"
SPORTS = {
    "americanfootball_nfl": "NFL",
    "americanfootball_ncaaf": "NCAAF",
}
DEFAULT_TTL = 6 * 3600
MIN_INTERVAL = 1.5  # seconds between outbound requests


class SourceStatus(dict):
    """{name, ok, detail, events, from_cache, fetched_at}"""


class DiskCache:
    def __init__(self, directory: Path, ttl: int = DEFAULT_TTL):
        self.dir = Path(directory)
        self.ttl = ttl

    def _path(self, key: str) -> Path:
        return self.dir / (re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".json")

    def get(self, key: str, allow_stale: bool = False):
        p = self._path(key)
        try:
            obj = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        age = time.time() - obj.get("saved_at", 0)
        if age <= self.ttl or allow_stale:
            return obj["data"], age
        return None

    def put(self, key: str, data) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(json.dumps({"saved_at": time.time(), "data": data}))


_last_request = [0.0]


def polite_get_json(url: str, timeout: int = 20):
    wait = MIN_INTERVAL - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return gunzip_json(resp.read())


def describe_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"network error: {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


class Provider:
    name = "base"
    label = "base"
    is_sample = False

    def __init__(self, cache: DiskCache | None = None, fetch: Callable = polite_get_json):
        self.cache = cache
        self._fetch = fetch

    def fetch_events(self, sport_key: str) -> tuple[list[dict], SourceStatus]:  # pragma: no cover
        raise NotImplementedError

    def _cached_fetch(self, cache_key: str, url: str):
        """Return (json, from_cache). Fresh cache wins; on error falls back to stale cache."""
        if self.cache:
            hit = self.cache.get(cache_key)
            if hit:
                return hit[0], True
        try:
            data = self._fetch(url)
        except Exception:
            if self.cache:
                stale = self.cache.get(cache_key, allow_stale=True)
                if stale:
                    return stale[0], True
            raise
        if self.cache:
            self.cache.put(cache_key, data)
        return data, False


# ----------------------------------------------------------------- ESPN (keyless)
def _a(s) -> int | None:
    """Parse an American-odds string ('-112', '+110', 'EVEN')."""
    if s is None:
        return None
    s = str(s).strip().upper()
    if s in ("EVEN", "EV", "PK"):
        return 100
    try:
        v = int(float(s))
    except ValueError:
        return None
    return v if abs(v) >= 100 else None


def _num(s) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(s or ""))
    return float(m.group()) if m else None


def parse_espn_scoreboard(data: dict, sport_key: str) -> list[dict]:
    """ESPN public scoreboard JSON -> normalised events (pre-game only)."""
    events = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        if (ev.get("status") or {}).get("type", {}).get("state") != "pre":
            continue
        teams = {c["homeAway"]: c["team"]["displayName"] for c in comp.get("competitors", []) if "team" in c}
        if "home" not in teams or "away" not in teams:
            continue
        books = []
        for o in comp.get("odds") or []:
            prov = (o.get("provider") or {}).get("name") or "ESPN"
            markets = []
            ml = o.get("moneyline") or {}
            h, a = _a(((ml.get("home") or {}).get("close") or {}).get("odds")), _a(((ml.get("away") or {}).get("close") or {}).get("odds"))
            if h and a:
                markets.append({"key": "h2h", "outcomes": [{"name": teams["home"], "price": h}, {"name": teams["away"], "price": a}]})
            ps = o.get("pointSpread") or {}
            hs, as_ = (ps.get("home") or {}).get("close") or {}, (ps.get("away") or {}).get("close") or {}
            hp, ap, hl, al = _a(hs.get("odds")), _a(as_.get("odds")), _num(hs.get("line")), _num(as_.get("line"))
            if hp and ap and hl is not None and al is not None:
                markets.append({"key": "spreads", "outcomes": [
                    {"name": teams["home"], "price": hp, "point": hl}, {"name": teams["away"], "price": ap, "point": al}]})
            tot = o.get("total") or {}
            ov, un = (tot.get("over") or {}).get("close") or {}, (tot.get("under") or {}).get("close") or {}
            op, up, line = _a(ov.get("odds")), _a(un.get("odds")), _num(ov.get("line"))
            if op and up and line is not None and line == _num(un.get("line")):
                markets.append({"key": "totals", "outcomes": [
                    {"name": "Over", "price": op, "point": line}, {"name": "Under", "price": up, "point": line}]})
            if markets:
                books.append({"key": re.sub(r"\W+", "", prov.lower()), "title": prov,
                              "source": f"ESPN public scoreboard ({prov} lines)", "markets": markets})
        if books:
            events.append({"id": f"espn-{ev.get('id')}", "sport_key": sport_key, "league": SPORTS[sport_key],
                           "home_team": teams["home"], "away_team": teams["away"],
                           "commence_time": _iso(ev.get("date")), "bookmakers": books,
                           "neutral": bool(comp.get("neutralSite"))})
    return events


def _iso(s: str | None) -> str:
    if not s:
        return ""
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EspnProvider(Provider):
    name = "espn"
    label = "ESPN public scoreboard (one book: DraftKings)"
    URLS = {
        "americanfootball_nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        "americanfootball_ncaaf": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80&limit=200",
    }

    def fetch_events(self, sport_key):
        st = SourceStatus(name=self.label, ok=False, detail="", events=0, from_cache=False,
                          fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        try:
            data, cached = self._cached_fetch(f"espn_{sport_key}", self.URLS[sport_key])
            events = parse_espn_scoreboard(data, sport_key)
            st.update(ok=True, events=len(events), from_cache=cached,
                      detail=f"{len(events)} upcoming events with odds" + (" (cached)" if cached else ""))
            return events, st
        except Exception as exc:
            st["detail"] = describe_error(exc)
            return [], st


def _err_body(exc: Exception) -> str:
    try:
        return exc.read(4000).decode("utf-8", "replace") if isinstance(exc, urllib.error.HTTPError) and exc.fp else ""
    except Exception:
        return ""


# ----------------------------------------------------------------- FanDuel (keyless, read-only)
_FD_ML = {"MONEY_LINE", "MATCH_BETTING", "MONEYLINE"}
_FD_SPREAD = {"MATCH_HANDICAP_(2-WAY)", "MATCH_HANDICAP", "SPREAD", "POINT_SPREAD"}
_FD_TOTAL = {"TOTAL_POINTS_(OVER/UNDER)", "TOTAL_POINTS", "MATCH_TOTAL", "TOTALS"}


def _fd_price(r: dict):
    """American odds from a FanDuel runner, tolerating several known/likely layouts."""
    wo = r.get("winRunnerOdds") or r.get("odds") or {}
    ado = wo.get("americanDisplayOdds") if isinstance(wo, dict) else None
    cands = []
    if isinstance(ado, dict):
        cands += [ado.get("americanOdds"), ado.get("americanOddsInt"), ado.get("americanDisplayOdds")]
    elif ado is not None:
        cands.append(ado)
    cands += [wo.get("americanOdds") if isinstance(wo, dict) else None, r.get("americanOdds")]
    for c in cands:
        v = _a(c)
        if v:
            return v
    dec = None
    try:
        dec = float(((wo.get("trueOdds") or {}).get("decimalOdds") or {}).get("decimalOdds")) if isinstance(wo, dict) else None
    except (TypeError, ValueError):
        pass
    if dec and dec > 1:
        from .odds_math import decimal_to_american
        return decimal_to_american(dec)
    return None


def _fd_kind(m: dict):
    mt = str(m.get("marketType", "")).upper()
    mn = str(m.get("marketName", "")).lower()
    if mt in _FD_ML or mn in ("moneyline", "money line"):
        return "h2h"
    if mt in _FD_SPREAD or "spread" in mn or "handicap" in mn:
        return "spreads"
    if mt in _FD_TOTAL or mn.startswith("total points") or mn in ("total", "totals", "game total"):
        return "totals"
    return None


def _fd_market(m: dict):
    kind = _fd_kind(m)
    if not kind or str(m.get("marketStatus", "OPEN")).upper() not in ("OPEN", "ACTIVE"):
        return None
    runners = [r for r in (m.get("runners") or []) if str(r.get("runnerStatus", "ACTIVE")).upper() == "ACTIVE"]
    if len(runners) != 2 or any(_fd_price(r) is None for r in runners):
        return None
    if kind == "h2h":
        return {"key": "h2h", "outcomes": [{"name": r.get("runnerName", ""), "price": _fd_price(r)} for r in runners]}
    try:
        if kind == "spreads":
            return {"key": "spreads", "outcomes": [{"name": r.get("runnerName", ""), "price": _fd_price(r), "point": float(r["handicap"])} for r in runners]}
        outs = []
        for r in runners:
            side = "Over" if str(r.get("runnerName", "")).lower().startswith("over") else "Under"
            outs.append({"name": side, "price": _fd_price(r), "point": abs(float(r.get("handicap")))})
        return {"key": "totals", "outcomes": outs} if {o["name"] for o in outs} == {"Over", "Under"} else None
    except (KeyError, TypeError, ValueError):
        return None


def _fd_split(name: str):
    parts = re.split(r"\s+\(?@\)?\s+|\s+at\s+", name or "")
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else None


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def parse_fanduel_page(data: dict, sport_key: str) -> list[dict]:
    """Parse FanDuel's public content-managed-page JSON.

    Strict path: attachments.events / attachments.markets. If that finds nothing, a tolerant path
    walks the whole document for event-like dicts (name 'A @ B' + eventId) and market-like dicts
    (runners + eventId). NOTE: written from the publicly observable layout; never verified against
    live data from the build sandbox. Use `refresh.py --debug-dump DIR` locally to capture a real
    response so the parser can be fixed. Unrecognised shapes yield no events, never guesses."""
    att = (data or {}).get("attachments") or {}
    evs, mks = att.get("events") or {}, att.get("markets") or {}
    events = {str(k): v for k, v in evs.items() if isinstance(v, dict)}
    markets = [m for m in mks.values() if isinstance(m, dict)]
    if not events or not markets:  # tolerant fallback
        events, markets = {}, []
        for d in _walk(data):
            eid = d.get("eventId", d.get("id"))
            if eid is not None and _fd_split(d.get("name")) and (d.get("openDate") or d.get("startTime") or d.get("startDate")):
                events.setdefault(str(eid), d)
            if isinstance(d.get("runners"), list) and d.get("eventId") is not None:
                markets.append(d)
    by_event: dict[str, list] = {}
    for m in markets:
        by_event.setdefault(str(m.get("eventId")), []).append(m)
    out = []
    for eid, ev in events.items():
        teams = _fd_split(ev.get("name"))
        if not teams:
            continue
        away, home = teams
        mk = [x for x in (_fd_market(m) for m in by_event.get(eid, [])) if x]
        if mk:
            out.append({"id": f"fd-{eid}", "sport_key": sport_key, "league": SPORTS[sport_key], "home_team": home, "away_team": away,
                        "commence_time": _iso(ev.get("openDate") or ev.get("startTime") or ev.get("startDate")),
                        "bookmakers": [{"key": "fanduel", "title": "FanDuel", "source": "FanDuel public JSON", "markets": mk}]})
    return out


class FanDuelProvider(Provider):
    """Read-only GET of the public JSON the FanDuel web sportsbook itself loads.
    On ANY non-200 (403, geo-block, bot-wall) we record the exact status and return nothing."""
    name = "fanduel"
    label = "FanDuel public sportsbook JSON"
    PAGES = {"americanfootball_nfl": "nfl", "americanfootball_ncaaf": "college-football"}

    def __init__(self, cache=None, fetch=polite_get_json, state: str = "nj", debug_dir: str | None = None):
        super().__init__(cache, fetch)
        self.state = state
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self._raw_fetch = fetch
        self._fetch = self._debug_fetch

    def _debug_fetch(self, url: str):
        """Fetch once; with --debug-dump, save the raw response (or the exact error) for parser debugging."""
        sport = next((k for k, v in self.PAGES.items() if f"customPageId={v}&" in url), "unknown")
        try:
            data = self._raw_fetch(url)
        except Exception as exc:
            if self.debug_dir:
                self._dump(f"fanduel_{sport}.error.json", {"url": url, "error": describe_error(exc),
                                                           "body_head": _err_body(exc)})
            raise
        if self.debug_dir:
            self._dump(f"fanduel_{sport}.json", data)
        return data

    def _dump(self, name: str, obj) -> None:
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        (self.debug_dir / name).write_text(json.dumps(obj, indent=1))

    def url(self, sport_key: str) -> str:
        return (f"https://sbapi.{self.state}.sportsbook.fanduel.com/api/content-managed-page"
                f"?page=CUSTOM&customPageId={self.PAGES[sport_key]}&timezone=America%2FNew_York")

    def fetch_events(self, sport_key):
        st = SourceStatus(name=self.label, ok=False, detail="", events=0, from_cache=False,
                          fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        try:
            if self.debug_dir:  # always make one fresh request so the dump reflects the live response
                data, cached = self._debug_fetch(self.url(sport_key)), False
            else:
                data, cached = self._cached_fetch(f"fanduel_{self.state}_{sport_key}", self.url(sport_key))
        except Exception as exc:
            st["detail"] = describe_error(exc) + " (not worked around; FanDuel skipped)"
            return [], st
        events = parse_fanduel_page(data, sport_key)
        st.update(ok=bool(events), events=len(events), from_cache=cached,
                  detail=(f"{len(events)} events" if events else "200 OK but no parseable events (response shape unrecognised)"))
        return events, st


# ----------------------------------------------------------------- merging
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def merge_events(event_lists: list[list[dict]]) -> list[dict]:
    """Merge events from several providers: same sport + same home/away (normalised) + start within 12h."""
    merged: list[dict] = []
    for events in event_lists:
        for ev in events:
            target = None
            for m in merged:
                if (m["sport_key"] == ev["sport_key"] and _norm(m["home_team"]) == _norm(ev["home_team"])
                        and _norm(m["away_team"]) == _norm(ev["away_team"]) and _close(m["commence_time"], ev["commence_time"])):
                    target = m
                    break
            if target is None:
                merged.append({**ev, "bookmakers": list(ev["bookmakers"])})
            else:
                have = {b["key"] for b in target["bookmakers"]}
                target["bookmakers"] += [b for b in ev["bookmakers"] if b["key"] not in have]
    return merged


def _close(a: str, b: str) -> bool:
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except ValueError:
        return True
    return abs((da - db).total_seconds()) <= 12 * 3600


# ----------------------------------------------------------------- shared ESPN fetch helpers
def gunzip_json(raw: bytes):
    import gzip
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))
