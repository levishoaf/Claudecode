"""Build the web Bet Builder page: run the board for every known week and embed it.

    python3 web/export.py web/bet-builder.html
"""
import json, sys, traceback
from pathlib import Path
from datetime import datetime
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from nfl_edge import board
from nfl_edge.board import EASTERN, cfb_board, nfl_board
from nfl_edge.cli import _describe, _slip_market, _slip_selection, _fmt_american, clock
from nfl_edge.odds import decimal_to_american
from nfl_edge.picks import mixed_parlays, rank_singles

LO, HI = 0.60, 0.80

def be(p): return _fmt_american(decimal_to_american(1 / p))

def bet(b):
    k = b.commence_time.astimezone(EASTERN)
    return {"id": f"{_describe(b)} ({b.game})", "label": _describe(b), "market": _slip_market(b),
            "selection": _slip_selection(b), "game": b.game, "kickoff": k.isoformat(),
            "when": clock(k, with_date=True), "prob": round(b.fair_prob, 4), "breakeven": be(b.fair_prob),
            "price": _fmt_american(b.fd_price) if b.priced else None, "note": b.note or "", "week": b.week}

def run(sport, week, label):
    bets = cfb_board(None, LO, HI, week=week) if sport == "ncaaf" else nfl_board(None, LO, HI, week=week)
    singles = rank_singles(bets, 30, rank_by="prob", per_game=3)
    parlays = mixed_parlays(bets, 3, 5, 10)
    out = {"label": label, "week": week, "singles": [bet(b) for b in singles],
           "parlays": [{"legs": [bet(b) for b in p.legs], "prob": round(p.win_prob, 4),
                        "breakeven": be(p.win_prob)} for p in parlays],
           "note": board.NOTES.get("injuries", "") if sport == "nfl" else ""}
    if sport == "nfl":
        try:
            _, players, note = board.injury_report(week)
            out["injuries"] = {"note": note, "players": [
                {**p, "kickoff": p["kickoff"].astimezone(EASTERN).isoformat() if hasattr(p.get("kickoff"), "astimezone") else p.get("kickoff")}
                for p in players]}
        except Exception:
            traceback.print_exc(); out["injuries"] = {"note": "Injury report unavailable.", "players": []}
    return out

result = {"updated": clock(datetime.now(EASTERN), with_date=True) + " ET", "nfl": [], "ncaaf": []}
weeks = board.nfl_weeks()
current = weeks[0][0] if weeks else None
for w, posted in weeks:
    tag = "this week" if w == current else ("lines posted" if posted else "no lines yet")
    try: result["nfl"].append(run("nfl", w, f"Week {w} · {tag}"))
    except Exception: traceback.print_exc()
for i, w in enumerate(board.cfb_weeks()):
    try: result["ncaaf"].append(run("ncaaf", w, f"Week {w} · " + ("this week" if i == 0 else "upcoming")))
    except Exception: traceback.print_exc()
payload = json.dumps(result, default=str).replace("</", "<\\/")
page = (ROOT / "web" / "template.html").read_text().replace("__DATA__", payload)
Path(sys.argv[1]).write_text(page)
print({s: [(x["label"], len(x["singles"]), len(x["parlays"])) for x in result[s]] for s in ("nfl", "ncaaf")})
