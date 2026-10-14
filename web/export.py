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
from nfl_edge import board, hitrate, learn, tracker  # noqa: E402
from nfl_edge.board import EASTERN, cfb_board, nfl_board, season_for  # noqa: E402
from nfl_edge.cli import _describe, _fmt_american, _slip_market, _slip_selection, clock  # noqa: E402
from nfl_edge.odds import decimal_to_american  # noqa: E402
from nfl_edge.picks import (BET_TYPES, SURE_TYPES, UFC_TYPES, every_line_of, of_type,  # noqa: E402
                            league_types, multi_lays, MULTI_LEGS,
                            MONEY_MAKER_LEGS, fanduel_estimate,
                            money_maker_parlays,
                            payout_parlays,
                            rank_singles, type_max_prob)

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
            "tier": tier_of(b), "league": getattr(b, "league", None)}


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
    return today.year if sport in ("ufc", "mlb") else season_for(today)


def run(sport, week, label, ledger, this_week=False):
    if sport == "ufc":
        bets = board.ufc_board(LO, HI, card=str(week))
    elif sport == "mlb":  # week: a day as YYYYMMDD, or MLB_ALL_DAYS for the next 7 days
        everyday = week == board.MLB_ALL_DAYS
        when = None if everyday else datetime.strptime(str(week), "%Y%m%d").date()
        full_mlb = board.mlb_board(LO, HI, day=when, all_days=everyday, every_line=True)
        bets = [b for b in full_mlb if b.market != "totals_runs"]
    else:
        bets = annotate(cfb_board(None, LO, HI, week=week) if sport == "ncaaf"
                        else nfl_board(None, LO, HI, week=week), sport)
    singles = rank_singles(bets, 30, rank_by="prob", per_game=3,
                           prop_share=0.5 if sport == "nfl" else None)

    def legs_pool(some):  # Strong legs when there are enough, never Weak ones
        if any(getattr(b, "tier", None) for b in some):
            from nfl_edge import insights

            return insights.parlay_pool(some)
        return some, ""
    pool, pool_note = legs_pool(bets)
    parlays = payout_parlays(pool, PARLAY_PAYS, 10)  # at least $40 back on $10
    if this_week:  # only the current week's picks count as picks the builder made
        tracker.record_generated(ledger, launcher.tracker_entries(
            singles, parlays, sport, season_of(sport)))
    def parlay(p):
        return {"legs": [bet(b, sport) for b in p.legs], "prob": round(p.win_prob, 6),
                "breakeven": be(p.win_prob)}

    def picks(singles, parlays, pool):
        strong = (hitrate.strong_leg_check(season_for(datetime.now().date()))
                  if sport == "nfl" else None)  # legs that keep winning come first
        money = money_maker_parlays([b for b in pool if getattr(b, "tier", "") != "red"], 10,
                                    strong=strong)
        return {"singles": [bet(b, sport) for b in singles],
                "parlays": [parlay(p) for p in parlays],
                "money": [dict(parlay(p), back=round(fanduel_estimate(p), 4)) for p in money]}

    out = {"label": label, "week": week, **picks(singles, parlays, bets),
           "note": board.NOTES.get("injuries", "") if sport == "nfl" else "", "types": {},
           "late": board.NOTES.get("late", "") if sport == "nfl" else "",
           "poolNote": pool_note,
           "modelNote": board.NOTES.get({"ufc": "ufc_model", "mlb": "mlb_model"}.get(sport, ""), ""),
           "starters": board.NOTES.get("mlb_starters", "") if sport == "mlb" else ""}
    # Every market, for bet types that aren't sure to be on FanDuel (marked to confirm).
    full = annotate(nfl_board(None, LO, HI, week=week, always_offered=False)
                    if sport == "nfl" else [], sport)
    if sport == "mlb":  # totals only when picked: the line may differ on FanDuel
        full = full_mlb
    for kind in (league_types(sport) if sport in ("ufc", "mlb") else BET_TYPES):  # top 30 each
        cap = type_max_prob(kind)
        if cap is not None and sport == "nfl":  # likeliest lines within the odds limit
            some = every_line_of(annotate(nfl_board(None, LO, min(HI, cap), week=week,
                                                    always_offered=False), sport), kind)
        else:
            some = (of_type(full, kind) if sport == "mlb" and kind == "Total runs"
                    else of_type(bets, kind) if kind in SURE_TYPES else every_line_of(full, kind))
        if kind != "All bets" and some:
            regular = payout_parlays(legs_pool(some)[0], PARLAY_PAYS, 10)
            out["types"][kind] = picks(rank_singles(some, 30, rank_by="prob", per_game=3,
                                                    every_line=True), regular, some)
    if sport == "nfl":
        try:
            _, players, note = board.injury_report(week)
            out["injuries"] = {"note": note, "players": [
                {**p, "kickoff": p["kickoff"].astimezone(EASTERN).isoformat()
                 if hasattr(p.get("kickoff"), "astimezone") else p.get("kickoff")}
                for p in players]}
        except Exception:
            traceback.print_exc()
            out["injuries"] = {"note": "Injury report unavailable.", "players": []}
    return out


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
              "moneyLegs": MONEY_MAKER_LEGS,
              "nfl": [], "ncaaf": [], "ufc": [], "mlb": [], "betTypes": list(BET_TYPES),
              "ufcTypes": list(UFC_TYPES), "sureTypes": list(SURE_TYPES),
              "leagueTypes": {s: list(league_types(s)) for s in ("nfl", "ncaaf", "ufc", "mlb")}}
    weeks = board.nfl_weeks()
    current = weeks[0][0] if weeks else None
    for w, posted in weeks:
        tag = "this week" if w == current else ("lines posted" if posted else "no lines yet")
        try:
            result["nfl"].append(run("nfl", w, f"Week {w} · {tag}", ledger, w == current))
        except Exception:
            traceback.print_exc()
    for i, w in enumerate(board.cfb_weeks()):
        try:
            result["ncaaf"].append(run("ncaaf", w, f"Week {w} · "
                                       + ("this week" if i == 0 else "upcoming"), ledger, i == 0))
        except Exception:
            traceback.print_exc()

    for i, (eid, name, start) in enumerate(board.ufc_cards()):
        short = name.split(":", 1)[-1].strip()
        try:
            result["ufc"].append(run("ufc", int(eid), f"{short} · {start.astimezone(EASTERN):%b} "
                                     f"{start.astimezone(EASTERN).day}", ledger, i == 0))
        except Exception:
            traceback.print_exc()

    mlb_days = board.mlb_days()
    for i, (d, lines) in enumerate(mlb_days):
        try:
            result["mlb"].append(run("mlb", int(f"{d:%Y%m%d}"), f"{d:%a %b} {d.day}"
                                     + ("" if lines else " · no line yet"), ledger, i == 0))
        except Exception:
            traceback.print_exc()
    if mlb_days:
        try:
            result["mlb"].append(run("mlb", board.MLB_ALL_DAYS, "Next 7 days", ledger))
        except Exception:
            traceback.print_exc()

    try:  # Multi-Lays: parlays mixing leagues, the same whichever league is picked
        found = multi_lays(launcher.league_pools(), PARLAY_PAYS, 10)
        tracker.record_generated(ledger, launcher.tracker_entries(
            [], found, "multi", datetime.now().year))
        result["multi"] = [{"legs": [bet(b, b.league) for b in p.legs],
                            "prob": round(p.win_prob, 6), "breakeven": be(p.win_prob)}
                           for p in found]
        result["multiLegs"] = list(MULTI_LEGS)
    except Exception:
        traceback.print_exc()
        result["multi"] = []

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
                "model": tracker.model_line(e),
                "team": (tracker.leg_team(e["legs"][0], e["sport"], player_team) or "Other")
                if e["kind"] == "single" else "Parlays",
                "legs": [{"lid": leg["id"], "pick": leg.get("pick") or leg["label"],
                          "game": leg.get("game", ""), "kickoff": leg.get("kickoff", ""),
                          "prob": leg.get("win_prob"), "type": tracker.leg_type_name(leg)}
                         for leg in e["legs"]]}
    result["history"] = [brief(e, "not placed") for e in ledger["generated"]]
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
    print({s: [(x["label"], len(x["singles"]), len(x["parlays"])) for x in result[s]]
           for s in ("nfl", "ncaaf", "ufc", "mlb")}, "history:", len(result["history"]))


if __name__ == "__main__":
    main()
