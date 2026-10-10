"""Track bets you placed and picks you didn't, and grade both after the games.

A ledger is one JSON file:

    {"placed": [entry, ...], "generated": [entry, ...]}

An entry is a single or a parlay:

    {"id", "kind": "single" | "parlay", "sport", "season", "created",
     "win_prob", "odds", "stake", "status", "legs": [leg, ...]}

Legs use the grader's format (nfl_edge/grade.py) plus "id", "kickoff",
"status" and "detail". "generated" keeps every pick the builder showed for
games in the coming week, as first shown (its chance at that moment), so the
Not placed tab shows how the picks would have done. "status" is "pending"
until every leg is settled, then "won", "lost" or "push".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import data
from .grade import STATS_URL, college_games, grade_leg
from .odds import american_to_decimal, decimal_to_american

TRACK_DAYS = 8  # only picks for games this soon are logged as generated


def leg_id(leg: dict) -> str:
    return f"{leg['game_id']}|{leg['label']}"


def entry_id(kind: str, legs: list[dict]) -> str:
    return kind + ":" + " + ".join(sorted(leg_id(leg) for leg in legs))


def make_entry(kind: str, legs: list[dict], win_prob: float, sport: str, season: int,
               odds: int | None = None, stake: float = 100.0) -> dict:
    legs = [dict(leg, id=leg_id(leg), status="pending", detail="") for leg in legs]
    return {"id": entry_id(kind, legs), "kind": kind, "sport": sport, "season": season,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "win_prob": round(win_prob, 4),
            "odds": odds if odds is not None else decimal_to_american(1 / win_prob),
            "stake": stake, "status": "pending", "legs": legs}


def load(path: Path) -> dict:
    try:
        ledger = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        ledger = {}
    ledger.setdefault("placed", [])
    ledger.setdefault("generated", [])
    ledger.setdefault("saved", [])
    return ledger


def save(ledger: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1) + "\n")
    tmp.replace(path)


def soon(legs: list[dict], now: datetime | None = None) -> bool:
    """Every leg kicks off within TRACK_DAYS (and isn't long past)."""
    now = now or datetime.now(timezone.utc)
    try:
        times = [datetime.fromisoformat(leg["kickoff"]) for leg in legs]
    except (KeyError, ValueError):
        return False
    return all(now - timedelta(days=1) < t < now + timedelta(days=TRACK_DAYS) for t in times)


def record_generated(ledger: dict, entries: list[dict], now: datetime | None = None) -> int:
    """Add picks not seen before (first appearance wins). Returns how many were added."""
    known = {e["id"] for e in ledger["generated"]}
    added = 0
    for e in entries:
        if e["id"] not in known and soon(e["legs"], now):
            ledger["generated"].append(e)
            known.add(e["id"])
            added += 1
    return added


def place(ledger: dict, entry: dict, odds: int | None, stake: float) -> dict:
    """Log a bet you placed, at the odds you got (break-even odds if blank)."""
    placed = dict(entry, odds=odds if odds is not None else entry["odds"], stake=stake,
                  created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  legs=[dict(leg) for leg in entry["legs"]])
    ledger["placed"] = [e for e in ledger["placed"] if e["id"] != placed["id"]] + [placed]
    return placed


def remove_placed(ledger: dict, entry_id_: str) -> None:
    ledger["placed"] = [e for e in ledger["placed"] if e["id"] != entry_id_]


def combine(statuses: list[str]) -> str:
    """A single's or parlay's result from its legs' results (pushed legs drop out)."""
    if "lost" in statuses:
        return "lost"
    if "pending" in statuses:
        return "pending"
    if all(s == "push" for s in statuses):
        return "push"
    return "won"


def profit(entry: dict) -> float:
    if entry["status"] == "won":
        dec = american_to_decimal(entry["odds"])
        if entry["kind"] == "parlay":
            # A pushed leg drops out of the parlay: take its share out of the price.
            for leg in entry["legs"]:
                if leg["status"] == "push":
                    dec *= leg["win_prob"]
        return entry["stake"] * (max(dec, 1.0) - 1)
    if entry["status"] == "lost":
        return -entry["stake"]
    return 0.0


def all_entries(ledger: dict) -> list[dict]:
    return ledger["placed"] + ledger["generated"] + ledger.get("saved", [])


def history(ledger: dict) -> list[tuple[str, dict]]:
    """Every bet with where it came from: 'placed', 'not placed' or 'saved'.

    A pick you placed shows once, as placed."""
    placed = {e["id"] for e in ledger["placed"]}
    out = [("placed", e) for e in ledger["placed"]]
    out += [("not placed", e) for e in ledger["generated"] if e["id"] not in placed]
    out += [("saved", e) for e in ledger.get("saved", []) if e["id"] not in placed]
    return out


def first_kickoff(e: dict) -> str:
    return min((leg.get("kickoff") or "9999") for leg in e["legs"])


def kickoffs(sport: str, season: int, games_source: str | None = None) -> dict[str, tuple]:
    """{game_id: (kickoff ISO time, 'Away @ Home')} for one season."""
    if sport == "ncaaf":
        from . import cfb

        return {r["game_id"]: (r["start_date"].replace("Z", "+00:00"),
                               f"{r['away_team']} @ {r['home_team']}")
                for r in cfb.load_schedule(season, games_source, current_season=season)}
    from .board import ABBR_NAME, EASTERN

    out = {}
    for g in data.games(games_source):
        if g["season"] == str(season) and g.get("gameday"):
            hh, mm = (g.get("gametime") or "13:00").split(":")[:2]
            when = datetime.fromisoformat(g["gameday"]).replace(hour=int(hh), minute=int(mm),
                                                                tzinfo=EASTERN)
            out[g["game_id"]] = (when.isoformat(), f"{ABBR_NAME.get(g['away_team'], g['away_team'])}"
                                                   f" @ {ABBR_NAME.get(g['home_team'], g['home_team'])}")
    return out


def import_saved(ledger: dict, paths, games_source: str | None = None) -> int:
    """Add bets saved as files (bets/*.json, the grader's format) to ledger["saved"].

    A "kind": "singles" file becomes one single per leg; any other file is one
    parlay. Returns how many entries were added."""
    known = {e["id"] for e in ledger["saved"]}
    times: dict[tuple, dict] = {}
    added = 0
    for path in paths:
        try:
            bet = json.loads(Path(path).read_text())
            sport, season = bet.get("sport", "nfl"), int(bet["season"])
            if (sport, season) not in times:
                times[(sport, season)] = kickoffs(sport, season, games_source)
        except (OSError, ValueError, KeyError, data.DataError):
            continue
        legs = []
        for leg in bet["legs"]:
            when, game = times[(sport, season)].get(leg["game_id"], ("", ""))
            legs.append(dict(leg, kickoff=when, game=game, pick=leg["label"],
                             win_prob=leg.get("win_prob") or 0.5))
        groups = [("single", [leg]) for leg in legs] if bet.get("kind") == "singles" \
            else [("parlay", legs)]
        for kind, group in groups:
            if kind == "single":
                prob, odds = group[0]["win_prob"], group[0].get("odds")
            else:
                prob = bet.get("estimated_win_prob")
                if not prob:
                    prob = 1.0
                    for leg in group:
                        prob *= leg["win_prob"]
                odds = bet.get("odds")
            entry = make_entry(kind, group, prob, sport, season, odds=odds,
                               stake=group[0].get("stake", 100) if kind == "single" else 100)
            entry["source"] = bet.get("name", Path(path).stem)
            if entry["id"] not in known:
                ledger["saved"].append(entry)
                known.add(entry["id"])
                added += 1
    return added


def regrade(ledger: dict, games_source: str | None = None) -> int:
    """Grade every unsettled leg whose game has kicked off. Returns legs newly settled."""
    now = datetime.now(timezone.utc)
    entries = [e for e in all_entries(ledger) if e["status"] == "pending"]
    todo = [(e, leg) for e in entries for leg in e["legs"] if leg["status"] == "pending"
            and (not leg.get("kickoff")
                 or datetime.fromisoformat(leg["kickoff"]) < now - timedelta(hours=2))]
    if not todo:
        return 0
    games: dict[tuple, dict] = {}
    stats: dict[int, list] = {}
    settled = 0
    for e, leg in todo:
        key = (e["sport"], e["season"])
        try:
            if key not in games:
                games[key] = (college_games(e["season"], games_source) if e["sport"] == "ncaaf"
                              else {g["game_id"]: g for g in data.games(games_source)})
            if leg["type"] in ("passing_tds", "player_stat") and e["season"] not in stats:
                try:
                    stats[e["season"]] = data.read_csv(STATS_URL.format(season=e["season"]),
                                                       max_age=1800)
                except data.DataError:
                    stats[e["season"]] = []
        except data.DataError:
            continue
        status, detail = grade_leg(leg, games[key], stats.get(e["season"], []))
        leg["detail"] = detail
        if status != "pending":
            leg["status"] = status
            settled += 1
    for e in entries:
        e["status"] = combine([leg["status"] for leg in e["legs"]])
    return settled


def summary(entries: list[dict], money: bool) -> str:
    """'7-3-1 · profit +$84.20 on $1,100' or 'Hit 7 of 10 settled (70%) · predicted 74%'."""
    done = [e for e in entries if e["status"] != "pending"]
    won = sum(e["status"] == "won" for e in done)
    lost = sum(e["status"] == "lost" for e in done)
    pushed = len(done) - won - lost
    pending = len(entries) - len(done)
    tail = f" · {pending} pending" if pending else ""
    if not done:
        return f"No results yet{tail}"
    if money:
        net = sum(profit(e) for e in done)
        staked = sum(e["stake"] for e in done)
        record = f"{won}-{lost}" + (f"-{pushed}" if pushed else "")
        return f"Record {record} · profit {'+' if net >= 0 else '-'}${abs(net):,.2f} on ${staked:,.0f}{tail}"
    decided = [e for e in done if e["status"] != "push"]
    rate = won / len(decided) if decided else 0
    expected = sum(e["win_prob"] for e in decided) / len(decided) if decided else 0
    return (f"Hit {won} of {len(decided)} ({rate:.0%}) · the builder expected "
            f"{expected:.0%}{tail}")
