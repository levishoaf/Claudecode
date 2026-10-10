#!/usr/bin/env python3
"""Build single bets and parlays for FanDuel from free public data.

No API key or account needed. Double-click Bets.command (Mac), Bets.bat
(Windows) or the standalone Bets program, or run:

    python3 bets.py                     # asks a few questions
    python3 bets.py --sport nfl --date 2026-10-11 --singles 30 --legs 4 --parlays 3

Without FanDuel's own prices, every bet shows its chance of winning and its
break-even odds (the worst price still worth taking). Type FanDuel's price
into the checker at the end to see whether a bet is worth it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

if getattr(sys, "frozen", False):
    # Standalone build: save bets next to the program, use bundled certificates.
    ROOT = Path(sys.executable).resolve().parent
    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
else:
    ROOT = Path(__file__).resolve().parent
    sys.path.insert(0, str(ROOT))

from nfl_edge import data, extras  # noqa: E402
from nfl_edge import board  # noqa: E402
from nfl_edge.board import EASTERN, cfb_board, nfl_board, season_for  # noqa: E402
from nfl_edge.cli import _describe, _fmt_american, _slip_market, clock, format_slip  # noqa: E402
from nfl_edge.grade import print_report  # noqa: E402
from nfl_edge.odds import american_to_decimal, decimal_to_american  # noqa: E402
from nfl_edge.picks import best_parlays, mixed_parlays, payout_parlays, rank_singles  # noqa: E402


def ask(question: str, default: str) -> str:
    answer = input(f"{question} [{default}]: ").strip()
    return answer or default


NOTE_TAGS = {"Questionable": "  [Q]", "Did not practice": "  [DNP]"}


def label(b) -> str:
    return f"{_describe(b)} ({b.game})"


def break_even(p: float) -> str:
    return _fmt_american(decimal_to_american(1 / p))


# ---------------------------------------------------------------- saving

def leg_record(b, sport: str) -> dict | None:
    """A bet in the grader's format (see nfl_edge/grade.py)."""
    if not b.game_id:
        return None
    if sport == "nfl":
        from nfl_edge.stats import TEAM_ABBR

        team_code = TEAM_ABBR.get
    else:
        def team_code(name):
            return name
    leg = {"game_id": b.game_id, "odds": b.fd_price, "stake": 100,
           "win_prob": round(b.fair_prob, 4), "label": label(b)}
    if b.market == "h2h":
        leg.update(type="moneyline", team=team_code(b.pick))
    elif b.market in ("spreads", "alternate_spreads"):
        leg.update(type="spread", team=team_code(b.pick), point=b.point)
    elif b.market in ("totals", "alternate_totals"):
        leg.update(type="total", side=b.pick, line=b.point)
    elif b.market in ("team_totals", "alternate_team_totals"):
        team, side = b.pick.rsplit(" ", 1)
        leg.update(type="team_total", team=team_code(team), side=side, line=b.point)
    elif b.market.startswith("player_"):
        player, side = b.pick.rsplit(" ", 1)
        stat = extras.PLAYER_MARKETS[extras.base_market(b.market)][0]
        leg.update(type="player_stat", player=player, stat=stat)
        if side in ("Over", "Yes"):
            leg["min"] = int(b.point) + 1
        else:
            leg["max"] = int(b.point)
    else:
        return None
    return leg


def tracker_entries(singles, parlays, sport: str, season: int) -> list[dict]:
    """The shown picks as bet-tracker entries (see nfl_edge/tracker.py)."""
    from nfl_edge import tracker

    def legs_of(bets_):
        legs = []
        for b in bets_:
            leg = leg_record(b, sport)
            if leg is None:
                return None
            leg["kickoff"] = b.commence_time.isoformat()
            leg["market"] = _slip_market(b)
            leg["pick"] = _describe(b)
            leg["game"] = b.game
            legs.append(leg)
        return legs

    out = []
    for group, kind, prob in ([([b], "single", b.fair_prob) for b in singles]
                              + [(list(p.legs), "parlay", p.win_prob) for p in parlays]):
        if legs := legs_of(group):
            entry = tracker.make_entry(kind, legs, prob, sport, season)
            weeks = [b.week for b in group if b.week]
            if weeks:
                entry["week"] = min(weeks)
            out.append(entry)
    return out


def save(singles, parlays, sport: str, season: int) -> list[Path]:
    stamp = datetime.now(EASTERN).strftime("%Y-%m-%d_%H%M")
    out_dir = ROOT / "bets"
    out_dir.mkdir(exist_ok=True)
    note = ("Odds are break-even prices (or consensus prices for game lines), "
            "not FanDuel's; profit is what a fair price would pay.")
    written = []
    legs = [leg for b in singles if (leg := leg_record(b, sport))]
    if legs:
        path = out_dir / f"{stamp}_{sport}_singles.json"
        path.write_text(json.dumps({"name": f"{sport.upper()} singles ({stamp})", "kind": "singles",
                                    "sport": sport, "season": season, "note": note,
                                    "legs": legs}, indent=2) + "\n")
        written.append(path)
    for i, p in enumerate(parlays, 1):
        legs = [leg_record(b, sport) for b in p.legs]
        if all(legs):
            path = out_dir / f"{stamp}_{sport}_parlay{i}.json"
            path.write_text(json.dumps({"name": f"{sport.upper()} parlay #{i} ({stamp})",
                                        "sport": sport, "season": season, "note": note,
                                        "estimated_win_prob": round(p.win_prob, 4),
                                        "legs": legs}, indent=2) + "\n")
            written.append(path)
    return written


def grade_saved(which: str) -> None:
    files = sorted((ROOT / "bets").glob("*.json")) if which == "all" else [Path(which)]
    if not files:
        print(f"No saved bets in {ROOT / 'bets'}")
    for path in files:
        print()
        try:
            print_report(path)
        except (data.DataError, KeyError, ValueError) as e:
            print(f"{path.name}: could not grade ({e})")


# ---------------------------------------------------------------- one board

ODDS_KEY_FILE = ROOT / "odds api key.txt"


def odds_api_key() -> str | None:
    """The Odds API key for FanDuel lines: ODDS_API_KEY, or the saved key file."""
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key and ODDS_KEY_FILE.exists():
        key = ODDS_KEY_FILE.read_text().strip()
    return key or None


def save_odds_api_key(key: str) -> None:
    ODDS_KEY_FILE.write_text(key.strip() + "\n")


STATE_FILE = ROOT / "fanduel state.txt"
# Win chance range in %: no limit, so the board shows the likeliest bets.
MIN_PROB, MAX_PROB = 1.0, 99.0


def fanduel_state() -> str | None:
    """The state for checking bets against FanDuel's own site (saved by the app)."""
    try:
        return STATE_FILE.read_text().strip().lower() or None
    except OSError:
        return None


def save_fanduel_state(state: str) -> None:
    STATE_FILE.write_text(state.strip().lower() + "\n")


def build(args):
    """Candidate bets, picked singles and parlays for the chosen sport and day.

    With args.fanduel_key, only bets FanDuel lists, at FanDuel's prices, ranked by
    expected value; otherwise the free board with break-even odds."""
    lo, hi = args.min_prob / 100, args.max_prob / 100
    day = None if args.date in ("week", "all") else (
        date.today() if args.date == "today" else date.fromisoformat(args.date))
    week = getattr(args, "week", None)
    key = getattr(args, "fanduel_key", None)
    board.NOTES.pop("fanduel", None)
    if key and day is None:
        from nfl_edge import fanduel

        bets, board.NOTES["fanduel"] = fanduel.fanduel_board(key, args.sport, lo, hi, week=week)
        rank = "ev"  # real prices: best chance-and-payout first
    elif args.sport == "ncaaf":
        bets, rank = cfb_board(day, lo, hi, week=week), "prob"
    else:
        # With the FanDuel check on, start from every market; the check keeps only
        # what FanDuel lists. Without it, only bets that are sure to be on FanDuel.
        bets, rank = nfl_board(day, lo, hi, games_source=args.games_file, week=week,
                               always_offered=not getattr(args, "fanduel_state", None)), "prob"
    state = getattr(args, "fanduel_state", None)
    if state and not key:
        from nfl_edge import fdfeed

        # Keep only bets found on FanDuel's own site, at FanDuel's prices.
        bets, board.NOTES["fanduel"] = fdfeed.verify(bets, state, args.sport)
        rank = "ev"
    singles = rank_singles(bets, args.singles, rank_by=rank, per_game=args.per_game,
                           prop_share=0.5 if args.sport == "nfl" else None)
    lo_legs = getattr(args, "min_legs", None) or args.legs
    hi_legs = getattr(args, "max_legs", None) or args.legs
    pays = getattr(args, "pays", None)
    if not args.parlays:
        parlays = []
    elif pays:  # parlays that pay about this much on the wager, likeliest first
        parlays = payout_parlays(bets, pays / (args.stake or 10), args.parlays,
                                 min_legs=min(lo_legs, 2), max_legs=max(hi_legs, 6),
                                 rank_by=rank)
    elif hi_legs > lo_legs:  # a spread of sizes; parlays may share games
        parlays = mixed_parlays(bets, lo_legs, hi_legs, args.parlays)
    else:
        parlays = best_parlays(bets, lo_legs, args.parlays, allow_overlap=args.allow_overlap,
                               rank_by="prob")
    return bets, singles, parlays


def week_label(singles, parlays) -> str:
    """'Week 5 · 2026 season' from the games actually shown."""
    weeks = sorted({b.week for b in singles} | {b.week for p in parlays for b in p.legs} - {None})
    season = season_for(date.today())
    if not weeks:
        return f"{season} season"
    span = f"Week {weeks[0]}" if len(weeks) == 1 else f"Weeks {weeks[0]}-{weeks[-1]}"
    return f"{span} · {season} season"


def show(args, singles, parlays, previous: dict[str, float]) -> None:
    def mark(b) -> str:
        if not previous:
            return ""
        old = previous.get(label(b))
        if old is None:
            return "  << NEW"
        if abs(old - b.fair_prob) >= 0.02:
            return f"  << chance moved from {old:.0%}"
        return ""

    print(f"\n{args.sport.upper()} | {week_label(singles, parlays)} | "
          f"updated {clock(datetime.now(EASTERN), with_date=False)} ET | "
          "likeliest first\n")
    if args.sport == "nfl" and board.NOTES.get("injuries"):
        print(f"Injuries: {board.NOTES['injuries']}\n")
    print("SINGLE BETS  (odds = break-even: bet only if FanDuel pays this or better)")
    if not singles:
        print("  None found. Try a wider chance range or a different date.")
    for i, b in enumerate(singles, 1):
        when = clock(b.commence_time.astimezone(EASTERN), with_date=False)
        extra = f", consensus price {_fmt_american(b.fd_price)}" if b.priced else ""
        print(f"{i:>2}. {b.fair_prob:.0%}  {_describe(b)}  ({break_even(b.fair_prob)}{extra})"
              f"  {b.game}, {when} ET{NOTE_TAGS.get(b.note, '')}{mark(b)}")
    if previous:
        for gone in sorted(set(previous) - {label(b) for b in singles}):
            print(f"    dropped: {gone}")

    if args.parlays:
        sizes = sorted({len(p.legs) for p in parlays}) or [args.legs]
        span = f"{sizes[0]}" if len(sizes) == 1 else f"{sizes[0]}-{sizes[-1]}"
        shared = len({b.game for p in parlays for b in p.legs}) < sum(len(p.legs) for p in parlays)
        print(f"\nPARLAYS ({span} legs" + (", may share games)" if shared else ", no shared games)"))
        if not parlays:
            print("  Not enough games for that many legs.")
        for i, p in enumerate(parlays, 1):
            print(f"\nP{i}")
            print("\n".join(format_slip(p, args.stake, break_even=True)))
        if 0 < len(parlays) < args.parlays:
            print(f"\nOnly {len(parlays)} parlay(s) fit without sharing games.")
        print("\n* Payout at break-even odds. FanDuel will pay a little less, so check its slip.")


def check_price(singles, parlays, entry: str) -> str:
    """'3 -150' or 'P1 +240' -> a one-line verdict on FanDuel's price."""
    try:
        which, odds = entry.split()
        price = int(odds.replace("+", ""))
        if which.upper().startswith("P"):
            p = parlays[int(which[1:]) - 1]
            prob, name = p.win_prob, f"Parlay {which.upper()}"
        else:
            b = singles[int(which) - 1]
            prob, name = b.fair_prob, _describe(b)
        ev = prob * american_to_decimal(price) - 1
    except (ValueError, IndexError, ZeroDivisionError):
        return "Didn't catch that. Example: 3 -150 or P1 +240"
    verdict = "worth it" if ev > 0 else "not worth it"
    return (f"{name} at {_fmt_american(price)}: {prob:.0%} to win, expected value "
            f"{ev:+.1%} per bet -> {verdict} (break-even {break_even(prob)})")


def price_checker(singles, parlays) -> None:
    print("\nCHECK FANDUEL'S PRICE: type a pick number (or P1, P2...) and FanDuel's odds,")
    print("for example  3 -150  or  P1 +240.  Press Enter when done.")
    while True:
        entry = input("> ").strip()
        if not entry:
            return
        print("  " + check_price(singles, parlays, entry))


# ---------------------------------------------------------------- main

def countdown(seconds: int) -> None:
    """Wait, showing the time left until the next update on one line."""
    end = time.monotonic() + seconds
    while (left := int(end - time.monotonic() + 0.999)) > 0:
        print(f"\rNext update in {left // 60}:{left % 60:02d} ", end="", flush=True)
        time.sleep(min(1, max(end - time.monotonic(), 0)))
    print("\rUpdating...              ")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # slips use box-drawing characters; never crash an old console on them
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    p = argparse.ArgumentParser(description="Build FanDuel single bets and parlays from free data.")
    p.add_argument("--sport", choices=["nfl", "ncaaf"])
    p.add_argument("--date", help="'week' (this week's games, default), 'today' or YYYY-MM-DD (Eastern)")
    p.add_argument("--week", type=int, help="a specific week, e.g. 6 (default: this week)")
    p.add_argument("--singles", type=int, help="how many single bets")
    p.add_argument("--legs", type=int, help="legs per parlay")
    p.add_argument("--min-legs", type=int, help="smallest parlay (with --max-legs for a mix)")
    p.add_argument("--max-legs", type=int, help="largest parlay")
    p.add_argument("--parlays", type=int, help="how many parlays (0 for none)")
    p.add_argument("--stake", type=float, help="parlay wager shown on the slips")
    p.add_argument("--pays", type=float,
                   help="build parlays that pay about this many dollars on the wager, e.g. 40")
    p.add_argument("--min-prob", type=float, default=MIN_PROB,
                   help="lowest win chance in %% (default: no limit)")
    p.add_argument("--max-prob", type=float, default=MAX_PROB,
                   help="highest win chance in %% (default: no limit)")
    p.add_argument("--per-game", type=int, default=3, help="max single bets per game (default 3)")
    p.add_argument("--allow-overlap", action="store_true", help="let parlays share games")
    p.add_argument("--injuries", action="store_true",
                   help="list this week's injured and questionable players and exit")
    p.add_argument("--save", action="store_true", help="save picks to grade later")
    p.add_argument("--grade", nargs="?", const="all", metavar="FILE", help="grade saved bets")
    p.add_argument("--watch", type=int, default=0, metavar="MINUTES",
                   help="keep running and refresh every MINUTES")
    p.add_argument("--games-file", help=argparse.SUPPRESS)  # offline testing
    args = p.parse_args()

    interactive = sys.stdin.isatty() and len(sys.argv) == 1
    if interactive:
        print("Bet Builder (free data, no API key)\n")
        if ask("1 = build bets, 2 = grade saved bets", "1") == "2":
            args.grade = "all"
    if args.injuries:
        week, players, note = board.injury_report(args.week)
        print(f"NFL injury report  ·  Week {week}\n{note}\n")
        team = None
        for p in players:
            if p["team"] != team:
                team = p["team"]
                print(f"\n{p['team_name']} ({p['opponent']}, "
                      f"{clock(p['kickoff'].astimezone(EASTERN))} ET)")
            print(f"  {p['player']:<26} {p['position']:<4} {p['status']:<17} {p['injury']}")
        return 0
    if args.grade:
        grade_saved(args.grade)
        if interactive:
            input("\nPress Enter to close.")
        return 0
    if interactive:
        args.sport = ask("Sport: nfl or ncaaf", "nfl")
        args.date = ask("Which games: week (this week), today, or a date like 2026-10-11", "week")
        args.singles = int(ask("How many single bets", "10"))
        args.legs = int(ask("Legs per parlay", "3"))
        args.parlays = int(ask("How many parlays (0 = none)", "3"))
        args.stake = float(ask("Parlay wager in dollars", "10"))
        args.watch = int(ask("Refresh automatically every how many minutes? (0 = no)", "5"))
    args.sport = args.sport or "nfl"
    args.date = args.date or "week"
    args.singles = 10 if args.singles is None else args.singles
    args.legs = args.legs or 3
    args.parlays = 3 if args.parlays is None else args.parlays
    args.stake = args.stake or 10.0
    if args.watch:
        data.FRESH_SECONDS = min(data.FRESH_SECONDS, args.watch * 60)
        board.INJURY_MAX_AGE = min(board.INJURY_MAX_AGE, args.watch * 60)

    previous: dict[str, float] = {}
    saved = False
    while True:
        print("Loading data...")
        try:
            _, singles, parlays = build(args)
        except data.DataError as e:
            print(f"Couldn't load data: {e}")
            break
        show(args, singles, parlays, previous)
        previous = {label(b): b.fair_prob for b in singles}
        if not args.watch and interactive:
            price_checker(singles, parlays)
        if not saved and (args.save or (interactive and not args.watch and
                                        ask("\nSave these to grade later? (y/n)", "n") == "y")):
            season = season_for(date.today())
            for path in save(singles, parlays, args.sport, season):
                print(f"Saved {path.relative_to(ROOT)}. Grade later with option 2 or --grade.")
            saved = True
        if not args.watch:
            break
        print("\nPress Ctrl+C to stop.")
        try:
            countdown(args.watch * 60)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        print("\n" + "=" * 70)
    if interactive:
        input("\nPress Enter to close.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
