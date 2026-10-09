#!/usr/bin/env python3
"""Build single bets and parlays from live FanDuel odds.

Double-click Bets.command (Mac) or Bets.bat (Windows), or run:

    python3 bets.py                      # asks a few questions
    python3 bets.py --sport ncaaf --date today --singles 10 --legs 3 --parlays 3

Needs a free key from https://the-odds-api.com, read from the ODDS_API_KEY
environment variable or saved once to ~/.nfl_edge/odds_api_key.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from nfl_edge import cfb, data  # noqa: E402
from nfl_edge import extras  # noqa: E402
from nfl_edge.api import OddsAPIError, fetch_event_odds, fetch_odds  # noqa: E402
from nfl_edge.cli import build_model, format_slip, _describe, _fmt_american  # noqa: E402
from nfl_edge.finder import find_bets  # noqa: E402
from nfl_edge.odds import american_to_decimal, decimal_to_american  # noqa: E402
from nfl_edge.picks import best_parlays, rank_singles  # noqa: E402
from nfl_edge.stats import TEAM_ABBR  # noqa: E402

ET = ZoneInfo("America/New_York")
KEY_FILE = Path.home() / ".nfl_edge" / "odds_api_key"


def ask(question: str, default: str) -> str:
    answer = input(f"{question} [{default}]: ").strip()
    return answer or default


def api_key(interactive: bool) -> str | None:
    key = os.environ.get("ODDS_API_KEY")
    if key:
        return key
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    if not interactive:
        return None
    print("You need a free API key from https://the-odds-api.com")
    key = getpass.getpass("Paste your key (hidden): ").strip()
    if key and ask("Save it on this computer so you aren't asked again? (y/n)", "y").lower() == "y":
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(key)
        KEY_FILE.chmod(0o600)
        print(f"Saved to {KEY_FILE}")
    return key or None


def kickoff_et(event: dict) -> datetime:
    return datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00")).astimezone(ET)


def season_for(today: date) -> int:
    return today.year if today.month >= 3 else today.year - 1


def leg_record(bet, events: list[dict], model, sport: str) -> dict | None:
    """Turn a Bet into a grader leg (bets/*.json format)."""
    event = next((e for e in events if f"{e['away_team']} @ {e['home_team']}" == bet.game), None)
    row = model.schedule_row(event) if (event and model) else None
    if row is None:
        return None
    if sport == "ncaaf":
        team = cfb.match_team(bet.pick, model.schools)
    else:
        team = TEAM_ABBR.get(bet.pick)
    leg = {"game_id": row["game_id"], "odds": bet.fd_price, "stake": 100,
           "win_prob": round(bet.fair_prob, 4), "label": f"{_describe(bet)} ({bet.game})"}
    if bet.market == "h2h":
        leg.update(type="moneyline", team=team)
    elif bet.market == "spreads" and bet.point is not None:
        leg.update(type="spread", team=team, point=bet.point)
    elif bet.market == "alternate_spreads":
        leg.update(type="spread", team=team, point=bet.point)
    elif bet.market in ("totals", "alternate_totals"):
        leg.update(type="total", side=bet.pick, line=bet.point)
    elif bet.market in ("team_totals", "alternate_team_totals"):
        name, side = bet.pick.rsplit(" ", 1)
        code = cfb.match_team(name, model.schools) if sport == "ncaaf" else TEAM_ABBR.get(name)
        leg.update(type="team_total", team=code, side=side, line=bet.point)
    elif bet.market.startswith("player_"):
        player, side = bet.pick.rsplit(" ", 1)
        stat = extras.PLAYER_MARKETS[extras.base_market(bet.market)][0]
        leg.update(type="player_stat", player=player, stat=stat)
        if side in ("Over", "Yes"):
            leg["min"] = int(bet.point) + 1
        else:
            leg["max"] = int(bet.point)
    else:
        return None
    return leg


def save(singles, parlays, events, model, sport: str, season: int, stamp: str) -> list[Path]:
    out_dir = ROOT / "bets"
    out_dir.mkdir(exist_ok=True)
    written = []
    legs = [leg for b in singles if (leg := leg_record(b, events, model, sport))]
    if legs:
        path = out_dir / f"{stamp}_{sport}_singles.json"
        path.write_text(json.dumps({"name": f"{sport.upper()} singles ({stamp})", "kind": "singles",
                                    "sport": sport, "season": season, "legs": legs}, indent=2) + "\n")
        written.append(path)
    for i, p in enumerate(parlays, 1):
        legs = [leg_record(b, events, model, sport) for b in p.legs]
        if all(legs):
            path = out_dir / f"{stamp}_{sport}_parlay{i}.json"
            path.write_text(json.dumps({"name": f"{sport.upper()} parlay #{i} ({stamp})", "sport": sport,
                                        "season": season, "odds": p.american,
                                        "estimated_win_prob": round(p.win_prob, 4), "legs": legs},
                                       indent=2) + "\n")
            written.append(path)
    return written


def extra_bets(args, events: list[dict], season: int) -> list:
    """Price player props and alternate lines for each game."""
    markets = args.extra_markets.split(",") if args.extra_markets else (
        extras.DEFAULT_NCAAF_MARKETS if args.sport == "ncaaf" else extras.DEFAULT_NFL_MARKETS)
    games = data.games() if args.sport == "nfl" else []
    lines = extras.LineDistribution(args.sport, games)
    players = extras.load_player_model(season, games) if args.sport == "nfl" else None
    saved = json.loads(args.extras_file.read_text()) if args.extras_file else None
    key = None if saved else api_key(False)
    out, remaining = [], None
    for e in events:
        if saved is not None:
            extra = saved.get(e["id"])
        else:
            try:
                extra, remaining = fetch_event_odds(key, e["id"], markets, args.sport)
            except OddsAPIError as err:
                print(f"(Props/alternate lines unavailable for {e['away_team']} @ "
                      f"{e['home_team']}: {err}. Your plan may not include these markets.)")
                continue
        if extra:
            out += extras.price_event(e, extra, args.sport, lines, players)
    if remaining is not None:
        print(f"(API credits remaining after props and alternate lines: {remaining})")
    return out


def game_label(b) -> str:
    return f"{_describe(b)} ({b.game})"


def run_once(args, interactive: bool, previous: dict[str, int]) -> tuple[dict[str, int], list, list, list, object, int] | None:
    """Fetch, model and print one board. Returns what's needed to diff and save."""
    if args.file:
        events = json.loads(args.file.read_text())
    else:
        key = api_key(interactive)
        if not key:
            print("No API key. Set ODDS_API_KEY or run without arguments to enter one.")
            return None
        try:
            events, remaining = fetch_odds(key, ["h2h", "spreads", "totals"], ["us", "eu"], args.sport)
        except OddsAPIError as e:
            print(e)
            return None
        print(f"(API credits remaining: {remaining})")
    if args.date != "all":
        day = date.today() if args.date == "today" else date.fromisoformat(args.date)
        events = [e for e in events if kickoff_et(e).date() == day]
    if not args.file:
        now = datetime.now(ET)
        events = [e for e in events if kickoff_et(e) > now]
    if not events:
        print("No upcoming games found for that date.")
        return None

    season = season_for(date.today())
    model = None
    try:
        if args.sport == "ncaaf":
            model = cfb.CfbModel(cfb.load_schedule(season, current_season=season))
        else:
            model = build_model(season, None, None, weather=True)
    except (data.DataError, ValueError) as e:
        print(f"(Stats model unavailable: {e}; using market odds only)")

    all_bets = find_bets(events, min_ev=-1.0, min_books=1, model=model, model_weight=0.1,
                         include_started=bool(args.file))
    if args.extras:
        all_bets += extra_bets(args, events, season)
    min_prob = args.min_prob / 100
    singles = rank_singles(all_bets, args.singles, min_prob)
    parlays = best_parlays(all_bets, args.legs, args.parlays,
                           allow_overlap=args.allow_overlap, min_prob=min_prob)

    def mark(b) -> str:
        if not previous:
            return ""
        old = previous.get(game_label(b))
        if old is None:
            return "  << NEW"
        if old != b.fd_price:
            return f"  << odds moved from {_fmt_american(old)}"
        return ""

    print(f"\n{args.sport.upper()}: {len({e['id'] for e in events})} games, "
          f"updated {datetime.now(ET):%a %-I:%M %p} ET. Ranked by expected value "
          f"(win chance x payout); ties go to the likelier bet.\n")
    print("SINGLE BETS")
    for i, b in enumerate(singles, 1):
        payout = 100 * (american_to_decimal(b.fd_price) - 1)
        be = decimal_to_american(1 / b.fair_prob)
        print(f"{i:>2}. {b.fair_prob:.0%}  {_describe(b)} ({b.game}) {_fmt_american(b.fd_price)}"
              f"  pays ${payout:.0f}/$100  EV {b.ev:+.1%}  worth it at {_fmt_american(be)} or better"
              f"  {b.commence_time.astimezone(ET):%a %-I:%M %p} ET"
              f"{'  (model only)' if 'player logs only' in b.books else ''}{mark(b)}")
    if previous:
        gone = set(previous) - {game_label(b) for b in singles}
        for label in sorted(gone):
            print(f"    dropped: {label}")
    if singles and all(b.ev < 0 for b in singles):
        print("\nNone of these has positive expected value at FanDuel's prices; they lose the least.")

    print(f"\n{args.legs}-LEG PARLAYS" + ("" if args.allow_overlap else " (no shared games)"))
    if not parlays:
        print("Not enough games for that many legs.")
    for i, par in enumerate(parlays, 1):
        print(f"\n#{i}")
        print("\n".join(format_slip(par, args.stake)))
    if 0 < len(parlays) < args.parlays:
        print(f"\nOnly {len(parlays)} parlay(s) fit without sharing games; "
              "use --allow-overlap for more.")
    current = {game_label(b): b.fd_price for b in singles}
    return current, singles, parlays, events, model, season


def main() -> int:
    p = argparse.ArgumentParser(description="Build single bets and parlays from live FanDuel odds.")
    p.add_argument("--sport", choices=["nfl", "ncaaf"])
    p.add_argument("--date", help="'today', 'all', or YYYY-MM-DD (Eastern time)")
    p.add_argument("--singles", type=int, help="how many single bets to show")
    p.add_argument("--legs", type=int, help="legs per parlay")
    p.add_argument("--parlays", type=int, help="how many parlays")
    p.add_argument("--stake", type=float, help="parlay wager shown on the slips")
    p.add_argument("--min-prob", type=float, default=50.0,
                   help="skip bets below this win chance in percent (default 50)")
    p.add_argument("--allow-overlap", action="store_true",
                   help="let different parlays share games")
    p.add_argument("--file", type=Path, help="read odds JSON from a file instead of the API")
    p.add_argument("--save", action="store_true", help="save the picks as paper bets for grading")
    p.add_argument("--extras", action="store_true",
                   help="also price player props and alternate lines (costs more API credits)")
    p.add_argument("--extra-markets", help="comma-separated Odds API market keys to use with --extras")
    p.add_argument("--extras-file", type=Path, help=argparse.SUPPRESS)  # offline testing
    p.add_argument("--watch", type=int, default=0, metavar="MINUTES",
                   help="keep running and refresh every MINUTES (odds, injuries, weather)")
    args = p.parse_args()

    interactive = sys.stdin.isatty() and len(sys.argv) == 1
    if interactive:
        print("FanDuel bet builder\n")
        args.sport = ask("Sport: nfl or ncaaf", "nfl")
        args.date = ask("Which games: all, today, or a date like 2026-10-11", "all")
        args.singles = int(ask("How many single bets", "10"))
        args.legs = int(ask("Legs per parlay", "3"))
        args.parlays = int(ask("How many parlays", "3"))
        args.stake = float(ask("Parlay wager in dollars", "10"))
        args.extras = ask("Include player props and alternate lines? (y/n)", "y").lower() == "y"
        args.watch = int(ask("Refresh automatically every how many minutes? (0 = no)", "0"))
    args.sport = args.sport or "nfl"
    args.date = args.date or "all"
    args.singles = 10 if args.singles is None else args.singles
    args.legs = args.legs or 3
    args.parlays = 3 if args.parlays is None else args.parlays
    args.stake = args.stake or 10.0

    if args.watch:
        # Injury reports, schedules and weather refresh at least this often too.
        data.FRESH_SECONDS = min(data.FRESH_SECONDS, args.watch * 60)

    if args.extras and not args.extras_file:
        n = len((args.extra_markets or ",".join(
            extras.DEFAULT_NCAAF_MARKETS if args.sport == "ncaaf" else extras.DEFAULT_NFL_MARKETS)).split(","))
        print(f"Note: props and alternate lines cost about {n} credits per game per update "
              f"(e.g. {n * 14} for 14 games), on top of about 6 for the main lines.")
        if interactive and ask("Continue? (y/n)", "y").lower() != "y":
            args.extras = False

    previous: dict[str, int] = {}
    saved = False
    while True:
        result = run_once(args, interactive, previous)
        if result is None:
            break
        previous, singles, parlays, events, model, season = result
        if not saved and (args.save or (interactive and not args.watch and
                                        ask("\nSave these as paper bets to grade later? (y/n)", "n") == "y")):
            stamp = datetime.now(ET).strftime("%Y-%m-%d")
            for path in save(singles, parlays, events, model, args.sport, season, stamp):
                print(f"Saved {path.relative_to(ROOT)}; grade with: "
                      f"python3 -m nfl_edge.grade {path.relative_to(ROOT)}")
            saved = True
        if not args.watch:
            break
        print(f"\nNext update in {args.watch} minutes (each update uses about 6 API credits). "
              "Press Ctrl+C to stop.")
        try:
            time.sleep(args.watch * 60)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        print("\n" + "=" * 70)
    if interactive:
        input("\nPress Enter to close.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
