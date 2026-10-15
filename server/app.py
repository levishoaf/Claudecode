"""Bet Builder's server for the iPhone app.

Rebuilds every league's board in the background (the same picks as the web page:
`web/export.build_result`) and serves them a league, week and bet type at a time.
Subscribers (checked with RevenueCat) get everything; everyone else gets a preview
of the top picks. The builder's graded record is open to all.

    pip install -r server/requirements.txt
    uvicorn server.app:app --port 8000

Settings (environment):
    REVENUECAT_SECRET   RevenueCat secret API key; without it nobody is a subscriber,
                        unless ALLOW_ALL=1 (for testing)
    ENTITLEMENT         the RevenueCat entitlement that unlocks everything (default "pro")
    DATA_DIR            where the server's own pick ledger lives (default ./server-data)
    REFRESH_MINUTES     how often boards are rebuilt (default 15)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

LEAGUES = ("nfl", "ncaaf", "ufc", "mlb")
LEAGUE_NAMES = {"nfl": "NFL", "ncaaf": "College Football", "ufc": "UFC", "mlb": "MLB"}
FREE_SINGLES = 3  # what a non-subscriber sees of each board
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "server-data"))
REFRESH = float(os.environ.get("REFRESH_MINUTES", "15")) * 60
ENTITLEMENT = os.environ.get("ENTITLEMENT", "pro")
ENTITLEMENT_TTL = 10 * 60

app = FastAPI(title="Bet Builder API", version="1")


# ---------------------------------------------------------------- the boards

class Snapshot:
    """The latest build of every board, rebuilt in the background."""

    def __init__(self):
        self.value: dict[str, list[dict]] = {}  # league -> every FanDuel line, priced
        self.value_at = 0.0
        self.feed = None
        self.result: dict | None = None
        self.built_at = 0.0
        self.error = ""
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def rebuild(self) -> None:
        import export  # web/export.py

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ledger = DATA_DIR / "ledger.json"
        if not ledger.exists() and (ROOT / "web" / "history.json").exists():
            # Start from the builder's graded history, so the record is there from day one.
            ledger.write_text((ROOT / "web" / "history.json").read_text())
        try:
            result = export.build_result(history=ledger, import_saved=False)
        except Exception as e:  # keep serving the last good build
            traceback.print_exc()
            self.error = str(e)
            return
        with self._lock:
            self.result, self.built_at, self.error = result, time.time(), ""

    def rebuild_value(self) -> None:
        """Real odds from every sportsbook (nfl_edge/lines.py), when ODDS_API_KEY is set.
        The feed decides what's due, so this spends credits only on schedule."""
        from nfl_edge import lines

        if self.feed is None:
            self.feed = lines.Feed(folder=DATA_DIR / "odds")
        if not self.feed.enabled:
            return
        value = {}
        for league in LEAGUES:
            try:
                offers = lines.board(league, self.feed)
            except Exception:  # odds are extra: never stop the boards
                traceback.print_exc()
                offers = []
            # Firm value first; rough estimates and lines to check after, then the rest.
            value[league] = [o.to_json() for o in sorted(offers, key=lambda o: (
                o.ev > 0 and not o.rough and not o.check, o.ev), reverse=True)]
        with self._lock:
            self.value, self.value_at = value, time.time()

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop():
            while True:
                self.rebuild()
                self.rebuild_value()
                time.sleep(REFRESH)
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def get(self) -> dict:
        if self.result is None:
            raise HTTPException(503, "Bets are being built; try again in a minute.")
        return self.result


SNAPSHOT = Snapshot()


@app.on_event("startup")
def _start() -> None:
    if os.environ.get("NO_BACKGROUND") != "1":  # tests fill the snapshot themselves
        SNAPSHOT.start()


# ---------------------------------------------------------------- subscribers

_entitled: dict[str, tuple[float, bool]] = {}


def subscriber(user_id: str | None) -> bool:
    """True if this RevenueCat user has the entitlement (cached for a few minutes)."""
    if os.environ.get("ALLOW_ALL") == "1":
        return True
    secret = os.environ.get("REVENUECAT_SECRET")
    if not user_id or not secret:
        return False
    hit = _entitled.get(user_id)
    if hit and time.time() - hit[0] < ENTITLEMENT_TTL:
        return hit[1]
    req = urllib.request.Request(
        f"https://api.revenuecat.com/v1/subscribers/{urllib.request.quote(user_id, safe='')}",
        headers={"Authorization": f"Bearer {secret}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            info = json.load(resp)
    except (OSError, ValueError, urllib.error.HTTPError):
        return hit[1] if hit else False  # RevenueCat down: keep the last answer
    ent = ((info.get("subscriber") or {}).get("entitlements") or {}).get(ENTITLEMENT) or {}
    expires = ent.get("expires_date")
    active = bool(ent) and (expires is None or expires > time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                        time.gmtime()))
    _entitled[user_id] = (time.time(), active)
    return active


# ---------------------------------------------------------------- endpoints

@app.get("/v1/status")
def status() -> dict:
    r = SNAPSHOT.result
    return {"ready": r is not None, "updated": r["updated"] if r else None,
            "age_minutes": round((time.time() - SNAPSHOT.built_at) / 60, 1) if r else None,
            "error": SNAPSHOT.error or None}


@app.get("/v1/leagues")
def leagues() -> dict:
    """Each league's weeks (or cards, or days) and bet types, for the app's menus."""
    r = SNAPSHOT.get()
    return {"updated": r["updated"], "leagues": [
        {"id": s, "name": LEAGUE_NAMES[s], "types": r["leagueTypes"].get(s, ["All bets"]),
         "weeks": [{"week": w["week"], "label": w["label"]} for w in r.get(s, [])]}
        for s in LEAGUES]}


def _week(r: dict, sport: str, week: int | None) -> dict:
    if sport not in LEAGUES:
        raise HTTPException(404, f"Unknown league {sport}")
    weeks = r.get(sport) or []
    if not weeks:
        raise HTTPException(404, f"No {LEAGUE_NAMES[sport]} games right now")
    return next((w for w in weeks if w["week"] == week), weeks[0])


@app.get("/v1/board/{sport}")
def board(sport: str, week: int | None = None, type: str = Query("All bets"),
          x_user_id: str | None = Header(None)) -> dict:
    """One board: singles, parlays and money makers for a league, week and bet type.
    Non-subscribers get the top few singles and a count of what's locked."""
    r = SNAPSHOT.get()
    wk = _week(r, sport, week)
    picks = wk if type == "All bets" else (wk.get("types") or {}).get(type) or \
        {"singles": [], "parlays": [], "money": []}
    out = {"league": sport, "week": wk["week"], "label": wk["label"], "type": type,
           "updated": r["updated"], "note": wk.get("note") or "",
           "modelNote": wk.get("modelNote") or "", "starters": wk.get("starters") or "",
           "singles": picks.get("singles", []), "parlays": picks.get("parlays", []),
           "money": picks.get("money", [])}
    if subscriber(x_user_id):
        return dict(out, locked=False)
    return dict(out, locked=True, singles=out["singles"][:FREE_SINGLES], parlays=[], money=[],
                lockedCounts={"singles": max(len(out["singles"]) - FREE_SINGLES, 0),
                              "parlays": len(out["parlays"]), "money": len(out["money"])})


@app.get("/v1/multi")
def multi(x_user_id: str | None = Header(None)) -> dict:
    """Multi-Lays: parlays mixing leagues (subscribers)."""
    r = SNAPSHOT.get()
    found = r.get("multi") or []
    if subscriber(x_user_id):
        return {"locked": False, "parlays": found, "updated": r["updated"]}
    return {"locked": True, "parlays": [], "lockedCount": len(found), "updated": r["updated"]}


@app.get("/v1/record")
def record(limit: int = Query(100, ge=1, le=500)) -> dict:
    """The builder's graded record (open to all): the report card, and its newest
    settled picks on lines FanDuel always offers."""
    r = SNAPSHOT.get()
    picks = [e for e in r.get("history", []) if not e.get("model")]
    done = [e for e in picks if e["status"] in ("won", "lost", "push")]
    won = sum(e["status"] == "won" for e in done)
    lost = sum(e["status"] == "lost" for e in done)
    done.sort(key=lambda e: e.get("created", ""), reverse=True)
    return {"updated": r["updated"], "won": won, "lost": lost,
            "pending": sum(e["status"] == "pending" for e in picks),
            "report": r.get("report"), "recent": done[:limit]}


@app.get("/v1/value/{sport}")
def value(sport: str, kind: str = Query("all", pattern="^(all|lines|props)$"),
          min_ev: float = -1.0, x_user_id: str | None = Header(None)) -> dict:
    """Every line FanDuel offers in a league (game lines and player props), priced
    against every sportsbook: FanDuel's price, the best price and where, the true
    chance and the value at each. Best value first. Subscribers only; others get the
    counts."""
    if sport not in LEAGUES:
        raise HTTPException(404, f"Unknown league {sport}")
    offers = SNAPSHOT.value.get(sport, [])
    if kind != "all":
        props = {"lines": False, "props": True}[kind]
        offers = [o for o in offers if (o["market"] not in ("h2h", "spreads", "totals")) == props]
    offers = [o for o in offers if o["ev"] >= min_ev]
    summary = {"lines": len(offers), "plusEv": sum(o["ev"] > 0 and not o["check"] for o in offers),
               "betterElsewhere": sum(o["best"] > o["fanduel"] for o in offers)}
    updated = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(SNAPSHOT.value_at))
               if SNAPSHOT.value_at else None)
    if subscriber(x_user_id):
        return {"locked": False, "updated": updated, "summary": summary, "offers": offers}
    return {"locked": True, "updated": updated, "summary": summary, "offers": []}
