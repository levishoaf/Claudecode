"""Timestamped odds snapshots (data/snapshots.jsonl, gitignored) so closing-line value can be
computed from OUR OWN captured lines. Compact: only h2h / spreads / totals prices per book."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import odds_math as om


def compact(events: list[dict], ts: datetime) -> dict:
    out = []
    for e in events:
        books = {}
        for b in e["bookmakers"]:
            m = {}
            for mk in b["markets"]:
                if mk["key"] in ("h2h", "spreads", "totals"):
                    m[mk["key"]] = [[o["name"], o.get("point"), o["price"]] for o in mk["outcomes"]]
            if m:
                books[b["key"]] = {"title": b["title"], "m": m}
        if books:
            out.append({"id": e["id"], "t": e["commence_time"], "home": e["home_team"], "away": e["away_team"], "b": books})
    return {"ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "events": out}


def append(path: Path, events: list[dict], ts: datetime | None = None) -> int:
    snap = compact(events, ts or datetime.now(timezone.utc))
    if not snap["events"]:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(snap, separators=(",", ":")) + "\n")
    return len(snap["events"])


def closing_snapshots(path: Path) -> dict[str, dict]:
    """event_id -> {'ts', 'e'} for the LAST snapshot taken at or before that event's kickoff."""
    best: dict[str, dict] = {}
    if not path.exists():
        return best
    with path.open() as f:
        for line in f:
            try:
                s = json.loads(line)
            except ValueError:
                continue
            for e in s.get("events", []):
                if s["ts"] <= e["t"] and (e["id"] not in best or s["ts"] >= best[e["id"]]["ts"]):
                    best[e["id"]] = {"ts": s["ts"], "e": e}
    return best


def fair_prob(bet: dict, snap_event: dict, book_title: str):
    """No-vig probability of the same bet (same line) at the same book in a snapshot, or None."""
    book = next((b for b in snap_event["b"].values() if b["title"] == book_title), None)
    if not book:
        return None
    rows = book["m"].get(bet["market"])
    if not rows or len(rows) != 2:
        return None
    try:
        probs = om.devig([om.implied_prob_from_american(r[2]) for r in rows])
    except ValueError:
        return None
    for r, p in zip(rows, probs):
        if r[0] == bet["name"] and r[1] == bet.get("point"):
            return p
    return None  # that exact line is no longer posted: it moved
