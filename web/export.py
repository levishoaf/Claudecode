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
from nfl_edge import board, tracker  # noqa: E402
from nfl_edge.board import EASTERN, cfb_board, nfl_board, season_for  # noqa: E402
from nfl_edge.cli import _describe, _fmt_american, _slip_market, _slip_selection, clock  # noqa: E402
from nfl_edge.odds import decimal_to_american  # noqa: E402
from nfl_edge.picks import BET_TYPES, SURE_TYPES, of_type, payout_parlays, rank_singles  # noqa: E402

LO, HI = launcher.MIN_PROB / 100, launcher.MAX_PROB / 100  # no chance limit: the likeliest bets
PARLAY_PAYS = 4.0  # parlays pay at least 4x the wager: $40 on $10
HISTORY = ROOT / "web" / "history.json"


def be(p):
    return _fmt_american(decimal_to_american(1 / p))


def bet(b, sport):
    k = b.commence_time.astimezone(EASTERN)
    rec = launcher.leg_record(b, sport)
    if rec:
        rec.update(kickoff=b.commence_time.isoformat(), market=_slip_market(b), pick=_describe(b),
                   game=b.game)
    return {"id": f"{_describe(b)} ({b.game})", "lid": tracker.leg_id(rec) if rec else None,
            "rec": rec, "label": _describe(b), "market": _slip_market(b),
            "selection": _slip_selection(b), "game": b.game, "kickoff": k.isoformat(),
            "when": clock(k, with_date=True), "prob": round(b.fair_prob, 4),
            "breakeven": be(b.fair_prob),
            "price": _fmt_american(b.fd_price) if b.priced else None, "note": b.note or "",
            "week": b.week}


def run(sport, week, label, ledger, this_week=False):
    bets = (cfb_board(None, LO, HI, week=week) if sport == "ncaaf"
            else nfl_board(None, LO, HI, week=week))
    singles = rank_singles(bets, 30, rank_by="prob", per_game=3,
                           prop_share=0.5 if sport == "nfl" else None)
    parlays = payout_parlays(bets, PARLAY_PAYS, 10)  # at least $40 back on $10
    if this_week:  # only the current week's picks count as picks the builder made
        tracker.record_generated(ledger, launcher.tracker_entries(
            singles, parlays, sport, season_for(datetime.now().date())))
    def picks(singles, parlays):
        return {"singles": [bet(b, sport) for b in singles],
                "parlays": [{"legs": [bet(b, sport) for b in p.legs],
                             "prob": round(p.win_prob, 6), "breakeven": be(p.win_prob)}
                            for p in parlays]}

    out = {"label": label, "week": week, **picks(singles, parlays),
           "note": board.NOTES.get("injuries", "") if sport == "nfl" else "", "types": {}}
    for kind in BET_TYPES:  # each type's own top 30 and parlays, for the Bet type menu
        some = of_type(bets, kind)
        if kind != "All bets" and some:
            out["types"][kind] = picks(rank_singles(some, 30, rank_by="prob", per_game=3),
                                       payout_parlays(some, PARLAY_PAYS, 10))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--placed", help="JSON list of the page's placed bets")
    a = ap.parse_args()

    ledger = tracker.load(HISTORY)
    ledger["placed"] = []  # the page keeps its own placed bets
    result = {"updated": clock(datetime.now(EASTERN), with_date=True) + " ET",
              "nfl": [], "ncaaf": [], "betTypes": list(BET_TYPES), "sureTypes": list(SURE_TYPES)}
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

    if a.placed:
        ledger["placed"] = placed_entries(a.placed)
    try:  # paper bets saved as files in bets/ join the Bet History
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
    tracker.save(ledger, HISTORY)
    result["legResults"] = results
    def brief(e, src):
        return {"id": e["id"], "src": src, "kind": e["kind"], "sport": e["sport"],
                "week": e.get("week"),
                "prob": e["win_prob"], "odds": e["odds"], "stake": e.get("stake", 100),
                "status": e["status"], "created": e["created"], "name": e.get("source", ""),
                "legs": [{"lid": leg["id"], "pick": leg.get("pick") or leg["label"],
                          "game": leg.get("game", ""), "kickoff": leg.get("kickoff", ""),
                          "prob": leg.get("win_prob")} for leg in e["legs"]]}
    result["history"] = [brief(e, "not placed") for e in ledger["generated"]]
    result["saved"] = [brief(e, "saved") for e in ledger["saved"]]

    payload = json.dumps(result, default=str).replace("</", "<\\/")
    page = (ROOT / "web" / "template.html").read_text().replace("__DATA__", payload)
    Path(a.out).write_text(page)
    print({s: [(x["label"], len(x["singles"]), len(x["parlays"])) for x in result[s]]
           for s in ("nfl", "ncaaf")}, "history:", len(ledger["generated"]))


if __name__ == "__main__":
    main()
