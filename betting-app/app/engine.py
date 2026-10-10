"""Turns normalised events into ranked singles and parlays. All outputs are ESTIMATES."""
from __future__ import annotations

import itertools
import re
from datetime import datetime, timezone

from . import odds_math as om
from .providers import SPORTS, merge_events

SHARP_WEIGHTS = {"pinnacle": 5.0, "circasports": 3.0, "betfair_ex_us": 3.0, "betcris": 2.0, "matchbook": 2.0, "lowvig": 2.0, "betonlineag": 2.0}
SHARP_KEYS = {"pinnacle", "circasports", "betfair_ex_us"}
MARKET_LABELS = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total",
                 "player_pass_yds": "Pass Yds", "player_rush_yds": "Rush Yds", "player_reception_yds": "Rec Yds"}

DEFAULTS = dict(min_prob=0.25, kelly_fraction=0.25, stake_cap=0.02, parlay_stake_cap=0.005, devig="power",
                top_n=30, n_parlays=5, parlay_pool=22, min_parlay_prob=0.05, max_ev_sanity=0.25, flag_ev=0.08, candidates=200)


def book_weight(key: str) -> float:
    return SHARP_WEIGHTS.get(key, 1.0)


def fmt_point(x: float, signed: bool = False) -> str:
    s = f"{x:+g}" if signed else f"{x:g}"
    return s


def confidence(n_books: int, has_sharp: bool) -> str:
    if n_books <= 1:
        return "none"
    if has_sharp and n_books >= 3:
        return "high"
    return "medium" if n_books >= 4 else "low"


def _groups(event: dict) -> dict:
    """(market, description, outcome-signature) -> {book_key: {'title','source','prices': {(name, point): american}}}.
    Books are only compared on the identical set of lines."""
    groups: dict = {}
    for bk in event["bookmakers"]:
        for m in bk["markets"]:
            by_desc: dict = {}
            for o in m["outcomes"]:
                by_desc.setdefault(o.get("description"), []).append(o)
            for desc, outs in by_desc.items():
                if len(outs) != 2:
                    continue
                sig = tuple(sorted((o["name"], o.get("point")) for o in outs))
                g = groups.setdefault((m["key"], desc, sig), {})
                g[bk["key"]] = {"title": bk["title"], "source": bk.get("source", ""),
                                "prices": {(o["name"], o.get("point")): o["price"] for o in outs}}
    return groups


def _label(event, market, desc, name, point) -> str:
    if market == "h2h":
        return f"{name} ML"
    if market == "spreads":
        return f"{name} {fmt_point(point, True)}"
    if market == "totals":
        return f"{name} {fmt_point(point)}"
    return f"{desc} {name} {fmt_point(point)} {MARKET_LABELS.get(market, market)}"


def evaluate_event(event: dict, p: dict) -> list[dict]:
    picks = []
    game = f"{event['away_team']} @ {event['home_team']}"
    for (market, desc, sig), books in _groups(event).items():
        keys = list(books)
        outcomes = list(sig)
        vecs, weights, per_book = [], [], {}
        for k in keys:
            pr = books[k]["prices"]
            if set(pr) != set(outcomes):
                continue
            implied = [om.implied_prob_from_american(pr[o]) for o in outcomes]
            nv = om.devig(implied, p["devig"])
            vecs.append(nv)
            weights.append(book_weight(k))
            per_book[k] = nv
        if not vecs:
            continue
        cons = om.weighted_consensus(vecs, weights)
        n_books = len(vecs)
        has_sharp = any(k in SHARP_KEYS for k in per_book)
        conf = confidence(n_books, has_sharp)
        sources = sorted({books[k]["source"] for k in per_book if books[k]["source"]})
        for idx, o in enumerate(outcomes):
            best_k = max(per_book, key=lambda k: om.american_to_decimal(books[k]["prices"][o]))
            price = books[best_k]["prices"][o]
            dec = om.american_to_decimal(price)
            prob = cons[idx]
            ev = om.expected_value(prob, dec)
            flags = []
            if conf == "none":
                flags.append("Single source: probability is just that book's own no-vig line, so EV here is only the vig (negative by construction). No edge can be estimated.")
            elif conf == "low":
                flags.append("Low confidence: few books and no sharp book in the consensus.")
            if ev > p["flag_ev"]:
                flags.append("Unusually large edge: often a stale or mis-listed line. Verify the price is still live.")
            if ev > p["max_ev_sanity"]:
                continue  # almost certainly a data error
            picks.append({
                "event_id": event["id"], "league": event["league"], "game": game,
                "commence_time": event["commence_time"], "market": market, "market_label": MARKET_LABELS.get(market, market),
                "selection": _label(event, market, desc, o[0], o[1]),
                "best_book": books[best_k]["title"], "best_odds": price, "best_decimal": round(dec, 4),
                "profit_per_100": round((dec - 1) * 100, 2),
                "model_prob": round(prob, 4), "implied_prob": round(1 / dec, 4), "edge": round(prob - 1 / dec, 4),
                "ev_per_dollar": round(ev, 4), "kelly_full": round(om.kelly_full(prob, dec), 4),
                "stake_pct": round(om.kelly_stake(prob, dec, p["kelly_fraction"], p["stake_cap"]), 4),
                "flags": flags, "n_books": n_books, "has_sharp": has_sharp, "confidence": conf, "sources": sources,
                "other_prices": {books[k]["title"]: books[k]["prices"][o] for k in per_book},
                "pinnacle_prob": round(per_book["pinnacle"][outcomes.index(o)], 4) if "pinnacle" in per_book else None,
            })
    return picks


def build_parlays(candidates: list[dict], p: dict) -> list[dict]:
    """Search 3-5 leg combos from the top candidates. One leg per game (so legs are independent
    under our assumption and never same-game correlated); no duplicate legs."""
    pool = [c for c in candidates if c["model_prob"] >= max(p["min_prob"], 0.35)]
    pool = sorted(pool, key=lambda c: -c["ev_per_dollar"])[: p["parlay_pool"]]
    scored = []
    for n in (3, 4, 5):
        for combo in itertools.combinations(pool, n):
            if len({c["event_id"] for c in combo}) != n:
                continue
            s = om.parlay_summary([(c["model_prob"], c["best_decimal"]) for c in combo])
            if s["prob"] < p["min_parlay_prob"]:
                continue
            scored.append((s["ev"], combo, s))
    scored.sort(key=lambda t: -t[0])
    chosen, out = [], []
    for ev, combo, s in scored:
        ids = {c["event_id"] + c["selection"] for c in combo}
        if any(len(ids & prev) > 2 for prev in chosen):
            continue  # keep the 5 parlays meaningfully different
        chosen.append(ids)
        confs = [c["confidence"] for c in combo]
        order = ["none", "low", "medium", "high"]
        notes = ["Legs are from different games and treated as independent; one leg per game, no duplicates.",
                 "Parlay vig compounds: each leg's vig multiplies, so parlays are usually worse value than the same legs as singles.",
                 "Best prices may sit at different books; a real parlay must be placed at ONE book, so its payout will be lower than shown."]
        if ev <= 0:
            notes.insert(0, "NEGATIVE EV: no positive-EV parlay exists from the current data. This is only the least-bad combination; skipping it is the better bet.")
        out.append({
            "legs": [{k: c[k] for k in ("league", "game", "market_label", "selection", "best_book", "best_odds", "model_prob", "commence_time")} for c in combo],
            "n_legs": len(combo), "combined_decimal": round(s["decimal"], 3), "combined_odds": s["american"],
            "payout_per_100": round((s["decimal"] - 1) * 100, 2), "win_prob": round(s["prob"], 4),
            "implied_prob": round(s["implied_prob"], 4), "ev_per_dollar": round(s["ev"], 4),
            "stake_pct": round(om.kelly_stake(s["prob"], s["decimal"], p["kelly_fraction"], p["parlay_stake_cap"]), 4),
            "confidence": min(confs, key=order.index), "notes": notes,
        })
        if len(out) >= p["n_parlays"]:
            break
    return out


def run(providers: list, params: dict | None = None, now: datetime | None = None, force_sample_reason: str | None = None) -> dict:
    """Fetch from providers (one request per league each), merge, evaluate, rank."""
    p = {**DEFAULTS, **(params or {})}
    now = now or datetime.now(timezone.utc)
    statuses, lists = [], []
    for prov in providers:
        for sport in SPORTS:
            ev, st = prov.fetch_events(sport)
            st["league"] = SPORTS[sport]
            statuses.append(st)
            if ev:
                lists.append(ev)
    events = merge_events(lists)
    events = [e for e in events if not e["commence_time"] or e["commence_time"] > now.strftime("%Y-%m-%dT%H:%M:%SZ")]
    picks = [pick for e in events for pick in evaluate_event(e, p)]
    picks.sort(key=lambda x: -x["ev_per_dollar"])
    cands = picks[: p["candidates"]]
    eligible = [c for c in picks if c["model_prob"] >= p["min_prob"]]
    top = eligible[: p["top_n"]]
    is_sample = any(getattr(pr, "is_sample", False) for pr in providers)
    max_books = max((c["n_books"] for c in picks), default=0)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "mode": "sample" if is_sample else "live",
        "sample_reason": force_sample_reason,
        "params": p,
        "sources": statuses,
        "n_events": len(events), "n_markets_evaluated": len(picks),
        "positive_ev_count": sum(1 for c in picks if c["ev_per_dollar"] > 0),
        "max_books_per_market": max_books,
        "edge_detectable": max_books >= 2,
        "top_singles": top,
        "candidates": cands,
        "parlays": build_parlays(cands, p),
    }
