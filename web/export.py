"""Build the web Bet Builder page: run the board for every known week and embed it.

    python3 web/export.py OUT.html [--placed placed.json]

Picks shown for the coming week are logged in web/history.json (commit it)
and graded after the games, for the page's Not placed tab. --placed takes
the page's placed bets (its db "placed" collection, as a JSON list) so bets
placed on the page are graded too, including ones no longer on the board.
"""
import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bets as launcher  # noqa: E402  (leg records for grading)
from nfl_edge import board, edges, hitrate, learn, tracker  # noqa: E402
from nfl_edge.board import EASTERN, season_for  # noqa: E402
from nfl_edge.cli import _describe, _fmt_american, _slip_market, _slip_selection, clock  # noqa: E402
from nfl_edge.odds import decimal_to_american  # noqa: E402
from nfl_edge.picks import BET_TYPES, SURE_TYPES, UFC_TYPES, league_types  # noqa: E402
from nfl_edge.picks import worth_it, worth_parlay  # noqa: E402

LO, HI = launcher.MIN_PROB / 100, launcher.MAX_PROB / 100  # no chance limit: the likeliest bets
PARLAY_PAYS = 4.0  # parlays pay at least 4x the wager: $40 on $10
HISTORY = ROOT / "web" / "history.json"


def be(p):
    return _fmt_american(decimal_to_american(1 / p))


_HISTORY = {}  # season -> (games, player model), loaded once per export
LEG_TEAMS = {}  # leg id -> team name


def track_record(b, sport):
    """How often this bet would have won in games already played (NFL): this season,
    last season and, for a moneyline, against this week's opponent."""
    if sport != "nfl":
        return None
    season = season_for(datetime.now().date())
    try:
        if season not in _HISTORY:
            from nfl_edge import data
            from nfl_edge.extras import load_player_model

            games = data.games(None)
            _HISTORY[season] = (games, load_player_model(season, games))
        games, players = _HISTORY[season]
        r = hitrate.rates(b, players, games, season)
    except Exception:
        traceback.print_exc()
        return None
    if not r:
        return None
    out = {"now": [r[season].hits, r[season].games] if r.get(season) else None,
           "last": [r[season - 1].hits, r[season - 1].games] if r.get(season - 1) else None,
           "season": season}
    h = r.get("h2h")
    if h is not None:
        opp = next((t for t in b.game.split(" @ ") if t != b.pick), "")
        out["h2h"] = {"w": h.hits, "n": h.games, "games": list(h.results), "opp": opp,
                      "since": season - hitrate.H2H_SEASONS + 1}
    return out


def player_team(name):
    """A player's current NFL team code (for grouping Bet History by team)."""
    season = season_for(datetime.now().date())
    if season not in _HISTORY:
        try:
            from nfl_edge import data
            from nfl_edge.extras import load_player_model

            games = data.games(None)
            _HISTORY[season] = (games, load_player_model(season, games))
        except Exception:
            traceback.print_exc()
            return None
    from nfl_edge.context import name_key

    return _HISTORY[season][1].current_team.get(name_key(name))


def bet(b, sport):
    k = b.commence_time.astimezone(EASTERN)
    rec = launcher.leg_record(b, sport)
    if rec:
        rec.update(kickoff=b.commence_time.isoformat(), market=_slip_market(b), pick=_describe(b),
                   game=b.game)
    if rec:  # the team it's on, for grouping Bet History (bets placed on the page too)
        LEG_TEAMS[tracker.leg_id(rec)] = tracker.leg_team(
            rec, sport, player_team if sport == "nfl" else lambda n: None) or "Other"
    return {"id": f"{_describe(b)} ({b.game})", "lid": tracker.leg_id(rec) if rec else None,
            "rec": rec, "label": _describe(b), "market": _slip_market(b),
            "selection": _slip_selection(b), "game": b.game, "kickoff": k.isoformat(),
            "when": clock(k, with_date=True), "prob": round(b.fair_prob, 4),
            "breakeven": be(b.fair_prob),
            "price": _fmt_american(b.fd_price) if b.priced else None, "note": b.note or "",
            "confirm": b.confirm_line, "hist": track_record(b, sport),
            "week": b.week, "raw": round(b.raw_prob, 4) if getattr(b, "raw_prob", None) else None,
            "tier": tier_of(b), "league": getattr(b, "league", None),
            "worth": list(worth_it(b)),
            "fd": {"price": b.fd_price, "ev": round(b.ev, 4), "best": getattr(b, "best_price", b.fd_price),
                   "bestBook": launcher.book_name(getattr(b, "best_book", "fanduel")),
                   "books": getattr(b, "books_count", 0), "sharp": getattr(b, "sharp", False),
                   "check": getattr(b, "check", False), "rough": getattr(b, "rough", False)}
            if getattr(b, "on_fanduel", False) else None}


def tier_of(b):
    """The bet's confidence tier, reasons, notes and warnings (NFL)."""
    ins = getattr(b, "insight", None)
    if ins is None:
        return None
    from nfl_edge.insights import TIER_WORDS

    return {"tier": ins.tier, "word": TIER_WORDS[ins.tier], "reasons": ins.reasons,
            "notes": ins.notes, "flags": ins.flags, "shift": round(ins.shift, 4)}


def annotate(bets, sport):
    """Confidence tiers for NFL bets (extra information: never blocks the export)."""
    if sport == "nfl":
        try:
            from nfl_edge import insights

            insights.annotate(bets, "nfl", season_for(datetime.now().date()))
        except Exception:
            traceback.print_exc()
    return bets


def season_of(sport):
    today = datetime.now().date()
    return launcher.season_of(sport, today)


OFFERS: dict = {}  # (league, week) -> real odds (nfl_edge/lines.py), pulled once per export


def offers_for(sport, week=None):
    key = (sport, week if sport == "nfl" else None)
    if key not in OFFERS:
        OFFERS[key] = launcher.real_offers(sport, key[1])
    return OFFERS[key]


def type_of(b):
    """The Bet type menu entry a bet falls under (picks.BET_TYPES)."""
    return next((k for k, markets in BET_TYPES.items() if markets
                 and b.market.startswith(markets)), "Other")


def edge_bet(b, sport):
    """A bet for the page, with what makes it an edge: expected profit on $10 at FanDuel's
    price and the worst odds that still keep the edge."""
    out = bet(b, sport)
    out["type"] = type_of(b)
    out["edge"] = {"profit": round(edges.profit_per(b), 2), "worst": edges.worst_odds(b.fair_prob)}
    return out


def edge_parlay(p):
    return {"legs": [edge_bet(b, b.league) for b in p.legs], "prob": round(p.win_prob, 6),
            "breakeven": be(p.win_prob), "worth": list(worth_parlay(p)),
            "fd": {"price": p.american, "ev": round(p.ev, 4)},
            "edge": {"profit": round(edges.profit_per(p), 2)}}


def lean_bet(b, sport):
    """A "worth a look" bet: like an edge, plus what it misses of the edge rule."""
    return dict(edge_bet(b, sport), why=edges.why_not_edge(b))


def league_view(sport, week, label, found, injuries_week=None, leans=()):
    """One Games-menu entry: its edges and its "worth a look" bets, each best first."""
    out = {"label": label, "week": week, "singles": [edge_bet(b, sport) for b in found],
           "leans": [lean_bet(b, sport) for b in leans],
           "parlays": [], "money": [], "types": {}}
    if sport == "nfl":
        try:
            _, players, note = board.injury_report(injuries_week)
            out["injuries"] = {"note": note, "players": [
                {**p, "kickoff": p["kickoff"].astimezone(EASTERN).isoformat()
                 if hasattr(p.get("kickoff"), "astimezone") else p.get("kickoff")}
                for p in players]}
        except Exception:
            traceback.print_exc()
            out["injuries"] = {"note": "Injury report unavailable.", "players": []}
    return out


def league_edges(sport, week=None):
    """(edges, worth a look) for a league's games."""
    try:
        return edges.league_board(sport, week=week, offers=offers_for(sport, week))
    except Exception:
        traceback.print_exc()
        return [], []


def placed_entries(path):
    """The page's placed bets as tracker entries, so their legs get graded."""
    out = []
    for doc in json.loads(Path(path).read_text()):
        doc = doc.get("data", doc)
        legs = [leg.get("rec") for leg in doc.get("legs", [])]
        if legs and all(legs):
            out.append(tracker.make_entry(doc.get("kind", "single"), legs, doc.get("prob", 0.5),
                                          doc.get("sport", "nfl"), doc.get("season") or
                                          season_for(datetime.now().date())))
    return out


def build_result(history: Path = HISTORY, placed: str | None = None,
                 import_saved: bool = True) -> dict:
    """Everything the page (and the phone app's server) shows: every league's weeks with
    their singles, parlays, money makers and bet types, the Multi-Lays and Bet History.
    Picks shown for this week are logged (and graded) in the `history` ledger."""
    ledger = tracker.load(history)
    learn.remember(tracker.all_entries(ledger))  # learn from every graded pick so far
    ledger["placed"] = []  # the page keeps its own placed bets
    result = {"updated": clock(datetime.now(EASTERN), with_date=True) + " ET",
              "nfl": [], "ncaaf": [], "ufc": [], "mlb": [], "nba": [], "betTypes": list(BET_TYPES),
              "ufcTypes": list(UFC_TYPES), "sureTypes": list(SURE_TYPES),
              "leagueTypes": {s: list(league_types(s)) for s in launcher.LEAGUES}}
    found_all = []  # every league's current edges, for parlays
    shown = []  # (league, edges, worth a look) logged as picks the builder made
    weeks = board.nfl_weeks()
    for i, (w, posted) in enumerate(weeks[:2]):  # FanDuel prices this week and next
        found, leans = league_edges("nfl", w)
        result["nfl"].append(league_view("nfl", w, f"Week {w}" + (" · this week" if i == 0
                                                                  else ""), found, w, leans))
        found_all += found
        if i == 0:
            shown.append(("nfl", found, leans))
    for i, w in enumerate(board.cfb_weeks()[:1]):
        found, leans = league_edges("ncaaf", w)
        result["ncaaf"].append(league_view("ncaaf", w, f"Week {w} · this week", found,
                                           leans=leans))
        found_all += found
        shown.append(("ncaaf", found, leans))
    for i, (eid, name, start) in enumerate(board.ufc_cards()[:2]):
        short = name.split(":", 1)[-1].strip()
        found, leans = league_edges("ufc", int(eid))
        result["ufc"].append(league_view("ufc", int(eid), f"{short} · "
                                         f"{start.astimezone(EASTERN):%b} "
                                         f"{start.astimezone(EASTERN).day}", found, leans=leans))
        found_all += found
        if i == 0:
            shown.append(("ufc", found, leans))
    for sport in ("mlb", "nba"):  # the next 7 days, then each day
        found, leans = league_edges(sport)
        found_all += found
        shown.append((sport, found, leans))
        result[sport].append(league_view(sport, board.MLB_ALL_DAYS, "Next 7 days", found,
                                         leans=leans))
        days = sorted({b.commence_time.astimezone(EASTERN).date() for b in found + leans})

        def on(d, some):
            return [b for b in some if b.commence_time.astimezone(EASTERN).date() == d]
        for d in days:
            result[sport].append(league_view(sport, int(f"{d:%Y%m%d}"), f"{d:%a %b} {d.day}",
                                             on(d, found), leans=on(d, leans)))
    for sport, found, leans in shown:
        tracker.record_generated(ledger, launcher.tracker_entries(
            found, [], sport, season_of(sport)), edge=True)
        tracker.record_generated(ledger, launcher.tracker_entries(
            leans, [], sport, season_of(sport)), lean=True)
    try:  # parlays of edges only, any leagues: the same whichever league is picked
        parlays = edges.edge_parlays(found_all, 10)
        tracker.record_generated(ledger, launcher.tracker_entries(
            [], parlays, "multi", datetime.now().year), edge=True)
        result["edgeParlays"] = [edge_parlay(p) for p in parlays]
    except Exception:
        traceback.print_exc()
        result["edgeParlays"] = []
    result["oddsNote"] = launcher.ODDS_NOTE.get("note", "")
    result["edgeRule"] = {"ev": edges.EDGE_MIN_EV, "prob": edges.EDGE_MIN_PROB}
    result["noneNote"] = edges.none_note()

    if placed:
        ledger["placed"] = placed_entries(placed)
        for e in ledger["placed"]:  # their teams, for Bet History
            for leg in e["legs"]:
                LEG_TEAMS.setdefault(leg["id"], tracker.leg_team(
                    leg, e["sport"], player_team if e["sport"] == "nfl" else lambda n: None)
                    or "Other")
    try:  # paper bets saved as files in bets/ join the Bet History
        if import_saved:
            tracker.import_saved(ledger, sorted((ROOT / "bets").glob("*.json")))
    except Exception:
        traceback.print_exc()
    try:
        tracker.withdraw_injured(ledger)  # since listed Out/Doubtful/Questionable
        tracker.regrade(ledger)
    except Exception:
        traceback.print_exc()
    try:
        tracker.annotate_weeks(ledger)
    except Exception:
        traceback.print_exc()
    # Week of every game a tracked bet is on, for bets placed on the page.
    result["gameWeeks"] = {leg["game_id"]: e["week"] for e in tracker.all_entries(ledger)
                           if e.get("week") for leg in e["legs"]}
    results = {leg["id"]: [leg["status"], leg["detail"]]
               for e in tracker.all_entries(ledger) for leg in e["legs"]}
    ledger["placed"] = []
    tracker.save(ledger, history)
    result["legResults"] = results
    result["legTeams"] = LEG_TEAMS
    try:  # how the model did, and what it has learned from that
        from nfl_edge import insights

        # The record never counts picks on lines FanDuel may not have offered.
        record = [e for e in tracker.all_entries(ledger) if not tracker.model_line(e)]
        result["report"] = insights.report_card(record)
        from nfl_edge import clv, data as nfl_data

        result["clv"] = clv.report(record, nfl_data.games(None))
        result["learned"] = [list(r) for r in learn.summary()
                             if abs(r[4] - r[3]) >= 0.01 or r[2]]
    except Exception:
        traceback.print_exc()
    def brief(e, src):
        return {"id": e["id"], "src": src, "kind": e["kind"], "sport": e["sport"],
                "week": e.get("week"),
                "prob": e["win_prob"], "odds": e["odds"], "stake": e.get("stake", 100),
                "status": e["status"], "created": e["created"], "name": e.get("source", ""),
                "model": tracker.model_line(e), "edge": bool(e.get("edge")),
                "lean": bool(e.get("lean")),
                "team": (tracker.leg_team(e["legs"][0], e["sport"], player_team) or "Other")
                if e["kind"] == "single" else "Parlays",
                "legs": [{"lid": leg["id"], "pick": leg.get("pick") or leg["label"],
                          "game": leg.get("game", ""), "kickoff": leg.get("kickoff", ""),
                          "prob": leg.get("win_prob"), "type": tracker.leg_type_name(leg)}
                         for leg in e["legs"]]}
    result["history"] = [brief(e, "not placed") for e in ledger["generated"]]
    result["edgeRecord"] = tracker.edge_record(ledger["generated"])
    result["leanRecord"] = tracker.edge_record(ledger["generated"], key="lean")
    result["leanRule"] = {"ev": edges.LEAN_MIN_EV, "prob": edges.LEAN_MIN_PROB}
    result["saved"] = [brief(e, "saved") for e in ledger["saved"]]

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--placed", help="JSON list of the page's placed bets")
    a = ap.parse_args()
    result = build_result(placed=a.placed)
    payload = json.dumps(result, default=str).replace("</", "<\\/")
    page = (ROOT / "web" / "template.html").read_text().replace("__DATA__", payload)
    Path(a.out).write_text(page)
    print({s: [(x["label"], len(x["singles"])) for x in result[s]]
           for s in launcher.LEAGUES}, "edge parlays:", len(result["edgeParlays"]),
          "history:", len(result["history"]))


if __name__ == "__main__":
    main()
