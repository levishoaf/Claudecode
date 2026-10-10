"""Paper-trading ledger and grading. No real bets are ever placed.

data/ledger.jsonl is append-only: each refresh appends the published top singles and parlays
(deduplicated, so re-running the same week does not double count). `grade` settles them from
ESPN final scores into data/grades.json and writes data/track.json.
1 unit = 1% of a notional bankroll (so a 2% Kelly stake = 2 units). Two views are reported:
  flat  - every pick at 1 unit (measures whether the picks themselves have an edge)
  kelly - the suggested stakes (measures the staking plan; picks with no +EV stake get 0)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import odds_math as om
from .backtest import market_p_home, parse_pickcenter
from .providers import DiskCache, polite_get_json
from .ratings import LEAGUES, SUMMARY


def _bet_key(b: dict) -> str:
    return f"{b['market']}|{b['name']}|{b.get('point')}|{b.get('description')}"


def entries_from_payload(payload: dict, now: datetime) -> list[dict]:
    if payload.get("mode") != "live":
        return []  # sample data is fictional: never ledgered
    run_at = now.isoformat(timespec="seconds")
    out = []
    for s in payload.get("top_singles", []):
        out.append({"id": f"s|{s['event_id']}|{_bet_key(s['bet'])}", "type": "single", "run_at": run_at,
                    "league": s["league"], "event_id": s["event_id"], "game": s["game"], "commence_time": s["commence_time"],
                    "selection": s["selection"], "market": s["market_label"], "bet": s["bet"], "book": s["best_book"],
                    "odds": s["best_odds"], "decimal": s["best_decimal"], "model_prob": s["model_prob"],
                    "market_prob": s.get("market_prob"), "power_prob": s.get("power_prob"), "ev": s["ev_per_dollar"],
                    "confidence": s["confidence"], "units": round(s["stake_pct"] * 100, 2), "flat_units": 1.0})
    for p in payload.get("parlays", []):
        legs = [{k: l[k] for k in ("event_id", "game", "selection", "bet", "best_odds", "best_decimal", "league", "commence_time")} for l in p["legs"]]
        key = "+".join(sorted(f"{l['event_id']}|{_bet_key(l['bet'])}" for l in legs))
        out.append({"id": f"p|{key}", "type": "parlay", "run_at": run_at, "legs": legs, "odds": p["combined_odds"],
                    "decimal": p["combined_decimal"], "model_prob": p["win_prob"], "ev": p["ev_per_dollar"],
                    "confidence": p["confidence"], "commence_time": max(l["commence_time"] for l in legs),
                    "units": round(p["stake_pct"] * 100, 2), "flat_units": 1.0})
    return out


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def append_picks(payload: dict, path: Path, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    have = {r["id"] for r in read_ledger(path)}
    new = [e for e in entries_from_payload(payload, now) if e["id"] not in have]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for e in new:
                f.write(json.dumps(e) + "\n")
    return len(new)


# ---------------------------------------------------------------- settlement
def settle_bet(bet: dict, home: str, away: str, hs: float, as_: float) -> str:
    """'win' | 'loss' | 'push' for one bet given final scores."""
    m, name, pt = bet["market"], bet["name"], bet.get("point")
    if m == "totals":
        tot = hs + as_
        if tot == pt:
            return "push"
        return "win" if (tot > pt) == (name == "Over") else "loss"
    if name == home:
        mine, other = hs, as_
    elif name == away:
        mine, other = as_, hs
    else:
        return "void"
    if m == "h2h":
        return "push" if mine == other else "win" if mine > other else "loss"
    if m == "spreads":
        adj = mine + pt - other
        return "push" if adj == 0 else "win" if adj > 0 else "loss"
    return "void"


def final_score(data: dict):
    try:
        c = data["header"]["competitions"][0]
        if not c["status"]["type"]["completed"]:
            return None
        sc = {x["homeAway"]: (x["team"]["displayName"], float(x["score"])) for x in c["competitors"]}
        return sc["home"][0], sc["away"][0], sc["home"][1], sc["away"][1]
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def closing_fair_prob(bet: dict, pc: dict | None, home: str):
    """No-vig closing probability of the SAME line (None if the line moved or is unavailable)."""
    if not pc:
        return None
    try:
        m, pt = bet["market"], bet.get("point")
        if m == "h2h":
            ph = market_p_home(pc)
            if ph is None:
                return None
            return ph if bet["name"] == home else 1 - ph
        if m == "spreads":
            sh = pc.get("spread_home")
            mine = sh if bet["name"] == home else -sh if sh is not None else None
            if mine is None or mine != pt or not pc.get("spread_odds_home") or not pc.get("spread_odds_away"):
                return None
            v = om.devig([om.implied_prob_from_american(pc["spread_odds_home"]), om.implied_prob_from_american(pc["spread_odds_away"])])
            return v[0] if bet["name"] == home else v[1]
        if m == "totals":
            if pc.get("total") != pt or not pc.get("over_odds") or not pc.get("under_odds"):
                return None
            v = om.devig([om.implied_prob_from_american(pc["over_odds"]), om.implied_prob_from_american(pc["under_odds"])])
            return v[0] if bet["name"] == "Over" else v[1]
    except (ValueError, TypeError):
        return None
    return None


def _summary(cache, fetch, sport: str, eid: str):
    """ESPN summary for an event; only cached once the game is final."""
    key = f"graded_{sport}_{eid}"
    hit = cache.get(key, allow_stale=True) if cache else None
    if hit:
        return hit[0]
    try:
        data = fetch(SUMMARY.format(path=LEAGUES[sport]["path"], eid=eid))
    except Exception:
        return None
    if cache and final_score(data):
        cache.put(key, data)
    return data


SPORT_BY_LEAGUE = {v["label"]: k for k, v in LEAGUES.items()}


def grade(ledger_path: Path, grades_path: Path, cache: DiskCache | None = None, fetch=polite_get_json,
          now: datetime | None = None, delay_hours: float = 4.0) -> dict:
    now = now or datetime.now(timezone.utc)
    grades = json.loads(grades_path.read_text()) if grades_path.exists() else {}
    entries = read_ledger(ledger_path)
    cutoff = (now - timedelta(hours=delay_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache_ev: dict = {}

    def event_info(league, eid):
        k = (league, eid)
        if k not in cache_ev:
            sport = SPORT_BY_LEAGUE[league]
            raw = _summary(cache, fetch, sport, eid.replace("espn-", ""))
            cache_ev[k] = (final_score(raw) if raw else None, parse_pc(raw))
        return cache_ev[k]

    for e in entries:
        if e["id"] in grades or e["commence_time"] > cutoff:
            continue
        res = grade_entry(e, event_info)
        if res:
            grades[e["id"]] = res
    grades_path.parent.mkdir(parents=True, exist_ok=True)
    grades_path.write_text(json.dumps(grades, indent=1))
    return track_summary(entries, grades, now)


def parse_pc(raw):
    pc = (raw or {}).get("pickcenter") or []
    return parse_pickcenter(pc[0]) if pc else None


def grade_entry(e: dict, event_info) -> dict | None:
    if e["type"] == "single":
        info = event_info(e["league"], e["event_id"])
        if not info[0]:
            return None
        home, away, hs, as_ = info[0]
        r = settle_bet(e["bet"], home, away, hs, as_)
        if r == "void":
            return None
        fair = closing_fair_prob(e["bet"], info[1], home)
        return {"result": r, "clv": round(e["decimal"] * fair - 1, 4) if fair else None}
    # parlay
    results, dec = [], 1.0
    for l in e["legs"]:
        info = event_info(l["league"], l["event_id"])
        if not info[0]:
            return None
        home, away, hs, as_ = info[0]
        r = settle_bet(l["bet"], home, away, hs, as_)
        if r == "void":
            return None
        results.append(r)
        if r != "push":
            dec *= l["best_decimal"]
    if "loss" in results:
        return {"result": "loss", "clv": None}
    if all(r == "push" for r in results):
        return {"result": "push", "clv": None}
    return {"result": "win", "clv": None, "paid_decimal": round(dec, 4)}


def profit_per_unit(entry: dict, g: dict) -> float:
    if g["result"] == "loss":
        return -1.0
    if g["result"] == "push":
        return 0.0
    return (g.get("paid_decimal") or entry["decimal"]) - 1


def _agg(rows):
    n = len(rows)
    wins = sum(1 for e, g in rows if g["result"] == "win")
    losses = sum(1 for e, g in rows if g["result"] == "loss")
    pushes = n - wins - losses
    flat = sum(profit_per_unit(e, g) * e["flat_units"] for e, g in rows)
    kst = sum(e["units"] for e, g in rows)
    kp = sum(profit_per_unit(e, g) * e["units"] for e, g in rows)
    clv = [g["clv"] for e, g in rows if g.get("clv") is not None]
    return {"n": n, "wins": wins, "losses": losses, "pushes": pushes,
            "flat_units_won": round(flat, 2), "flat_roi": round(flat / n, 4) if n else None,
            "kelly_units_staked": round(kst, 2), "kelly_units_won": round(kp, 2),
            "kelly_roi": round(kp / kst, 4) if kst else None,
            "avg_clv": round(sum(clv) / len(clv), 4) if clv else None, "clv_n": len(clv),
            "avg_model_ev_claimed": round(sum(e["ev"] for e, g in rows) / n, 4) if n else None}


def track_summary(entries: list[dict], grades: dict, now: datetime) -> dict:
    settled = [(e, grades[e["id"]]) for e in entries if e["id"] in grades]
    pending = len(entries) - len(settled)
    groups = {"all": _agg(settled),
              "singles": _agg([x for x in settled if x[0]["type"] == "single"]),
              "parlays": _agg([x for x in settled if x[0]["type"] == "parlay"])}
    for lab in ("none", "low", "medium", "high"):
        rows = [x for x in settled if x[0]["confidence"] == lab]
        if rows:
            groups[f"confidence_{lab}"] = _agg(rows)
    recent = [{"type": e["type"], "pick": e.get("selection") or " + ".join(l["selection"] for l in e["legs"]), "odds": e["odds"],
               "result": g["result"], "commence_time": e["commence_time"]}
              for e, g in sorted(settled, key=lambda x: x[0]["commence_time"], reverse=True)[:25]]
    return {"generated_at": now.isoformat(timespec="seconds"), "paper_trading_only": True,
            "unit": "1 unit = 1% of a notional bankroll", "pending": pending, "settled": len(settled),
            "groups": groups, "recent": recent}
