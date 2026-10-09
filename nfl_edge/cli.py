"""Command-line entry point: python -m nfl_edge"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import cfb
from .api import SPORT_KEYS, OddsAPIError, fetch_odds
from .finder import Bet, find_bets
from . import data
from .context import SeasonData
from .factors import Coefficients
from .model import GameModel
from .odds import DEVIG_METHODS, decimal_to_american
from .parlays import Parlay, build_parlays, most_likely_parlay
from .stats import SeasonModel
from .weather import kickoff_forecast

SAMPLE = Path(__file__).with_name("sample_odds.json")
MARKET_LABELS = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}


def _fmt_american(price: int) -> str:
    return f"+{price}" if price > 0 else str(price)


def _describe(bet: Bet) -> str:
    if bet.market.startswith("player_"):
        from .extras import stat_label

        player, side = bet.pick.rsplit(" ", 1)
        label = stat_label(bet.market)
        if label == "Anytime TD":
            return f"{player} Anytime TD" + ("" if side == "Yes" else " (No)")
        if side == "Over" and bet.point % 1:
            return f"{player} {int(bet.point + 0.5)}+ {label}"
        return f"{player} {side} {bet.point:g} {label}"
    if bet.market in ("team_totals", "alternate_team_totals"):
        team, side = bet.pick.rsplit(" ", 1)
        return f"{team} Team Total {side} {bet.point:g}"
    if bet.point is None:
        return f"{bet.pick} ML"
    if bet.market in ("totals", "alternate_totals"):
        return f"{bet.pick} {bet.point:g}"
    return f"{bet.pick} {bet.point:+g}"


def stake(bet: Bet, bankroll: float, kelly_mult: float, max_pct: float) -> float:
    return bankroll * min(bet.kelly * kelly_mult, max_pct)


def _pct(p: float | None) -> str:
    return "  n/a" if p is None else f"{p:.1%}"


def print_ratings(model: SeasonModel) -> None:
    print(f"Power ratings ({model.num_games} games, league avg {model.league_avg:.1f} pts/team)")
    print(f"{'Team':<5} {'GP':>3} {'Off':>6} {'Def':>6} {'Net':>6}")
    for r in sorted(model.ratings.values(), key=lambda r: r.net, reverse=True):
        print(f"{r.team:<5} {r.games:>3} {r.offense:>+6.1f} {r.defense:>+6.1f} {r.net:>+6.1f}")
    print("Off = points scored vs. average, Def = points allowed vs. average "
          "(negative is good), Net = points per game better than average.\n")


def parlay_stake(p: Parlay, bankroll: float, kelly_mult: float, max_pct: float) -> float:
    return bankroll * min(p.kelly * kelly_mult, max_pct)


SLIP_WIDTH = 52
SLIP_MARKETS = {"h2h": "MONEYLINE", "spreads": "SPREAD", "totals": "TOTAL POINTS",
                "alternate_spreads": "ALT SPREAD", "alternate_totals": "ALT TOTAL POINTS",
                "team_totals": "TEAM TOTAL POINTS", "alternate_team_totals": "ALT TEAM TOTAL"}
try:
    from zoneinfo import ZoneInfo

    EASTERN = ZoneInfo("America/New_York")
except Exception:  # no tz database available
    EASTERN = None


def _slip_selection(b: Bet) -> str:
    """Selection text the way FanDuel's bet slip shows it."""
    if b.market == "h2h":
        return b.pick
    return _describe(b)


def clock(dt: datetime, with_date: bool = True) -> str:
    """'Sun Oct 11, 1:00 PM' without platform-specific strftime codes
    (%-d and %-I don't exist on Windows)."""
    hour = dt.hour % 12 or 12
    day = f"{dt:%a %b} {dt.day}, " if with_date else f"{dt:%a} "
    return f"{day}{hour}:{dt:%M %p}"


def _slip_time(b: Bet) -> str:
    if EASTERN is None:
        return f"{clock(b.commence_time)} UTC"
    return f"{clock(b.commence_time.astimezone(EASTERN))} ET"


def _slip_market(b: Bet) -> str:
    if b.market.startswith("player_"):
        from .extras import stat_label

        return ("ALT " if b.market.endswith("_alternate") else "") + stat_label(b.market).upper()
    return SLIP_MARKETS.get(b.market, b.market.upper())


def format_slip(p: Parlay, wager: float) -> list[str]:
    """A parlay laid out like a FanDuel bet slip."""
    inner = SLIP_WIDTH - 4

    def line(left: str = "", right: str = "") -> str:
        left = left[: inner - len(right) - 1] if right else left[:inner]
        return f"│ {left}{right.rjust(inner - len(left))} │"

    rule = "├" + "─" * (SLIP_WIDTH - 2) + "┤"
    payout = round(wager * p.decimal, 2)
    out = ["┌" + "─" * (SLIP_WIDTH - 2) + "┐",
           line(f"{len(p.legs)} Leg Parlay", _fmt_american(p.american)),
           rule]
    for i, b in enumerate(p.legs):
        if i:
            out.append(line())
        out += [line(f"● {_slip_selection(b)}", _fmt_american(b.fd_price)),
                line(f"  {_slip_market(b)}"),
                line(f"  {b.game}"),
                line(f"  {_slip_time(b)}")]
    out += [rule,
            line(f"Wager ${wager:,.2f}", f"To Win ${payout - wager:,.2f}"),
            line("Total Payout", f"${payout:,.2f}"),
            rule,
            line(f"Our estimate: {p.win_prob:.1%} to win, EV {p.ev:+.1%}"),
            "└" + "─" * (SLIP_WIDTH - 2) + "┘"]
    return out


def print_parlays(parlays: list[Parlay], bankroll: float, kelly_mult: float,
                  max_pct: float, wager: float | None = None) -> None:
    print("\nParlays (legs from different games, ranked by chance of winning)")
    if not parlays:
        print("  None: there aren't enough +EV legs in separate games right now.")
        return
    for i, p in enumerate(parlays, 1):
        print(f"\n#{i}")
        amount = wager if wager is not None else parlay_stake(p, bankroll, kelly_mult, max_pct)
        print("\n".join(format_slip(p, amount)))
    print("\nA parlay only wins if every leg wins. Odds and payout assume FanDuel's standard "
          "parlay\npricing (the product of the legs); confirm on the bet slip before placing it.")


def print_most_likely(events: list[dict], args: argparse.Namespace, model, max_pct: float) -> int:
    every_leg = find_bets(
        events, min_ev=-1.0, min_books=args.min_books, devig=args.devig,
        sharp_only=not args.all_books, include_started=args.demo, model=model,
        model_weight=args.model_weight,
    )
    p = most_likely_parlay(every_leg, args.parlay_legs)
    if p is None:
        games = len({b.game for b in every_leg})
        print(f"Only {games} games have FanDuel odds; a {args.parlay_legs}-leg parlay needs "
              f"{args.parlay_legs} different games.")
        return 1
    wager = args.parlay_wager if args.parlay_wager is not None else 10.0
    if args.json:
        print(json.dumps(_parlay_json(p, args.bankroll, args.kelly, max_pct), indent=2))
        return 0
    print(f"Most likely {len(p.legs)}-leg parlay (one leg per game, most likely outcome in each)\n")
    print("\n".join(format_slip(p, wager)))
    fair_decimal = 1 / p.win_prob
    print(f"\nFair odds for this parlay: {_fmt_american(decimal_to_american(fair_decimal))}. "
          f"FanDuel pays {_fmt_american(p.american)}.")
    print(f"Expected value: {p.ev:+.1%}, about ${-p.ev * wager:,.2f} lost per ${wager:,.0f} "
          f"bet on average." if p.ev < 0 else f"Expected value: {p.ev:+.1%}.")
    if p.ev < 0:
        print("Legs were picked for likelihood, not value, so FanDuel's margin on each one "
              "compounds.\nThe suggested stake for a negative-EV bet is $0.")
    return 0


def _parlay_json(p: Parlay, bankroll: float, kelly_mult: float, max_pct: float) -> dict:
    return {
        "legs": [{"game": b.game, "bet": _describe(b), "fanduel_odds": b.fd_price,
                  "win_prob": round(b.fair_prob, 4)} for b in p.legs],
        "win_prob": round(p.win_prob, 4),
        "fanduel_odds": p.american,
        "ev": round(p.ev, 4),
        "stake": round(parlay_stake(p, bankroll, kelly_mult, max_pct), 2),
    }


def build_model(season: int, games_source: str | None, data_dir: str | None,
                weather: bool) -> GameModel:
    all_games = data.games(games_source)
    rows = [g for g in all_games if g["season"] == str(season)]
    if not any(g["home_score"] for g in rows):
        raise data.DataError(f"No completed games found for the {season} season")
    season_data = SeasonData(
        rows,
        data.release("injuries", season, season, data_dir),
        data.release("snap_counts", season, season, data_dir),
        history=all_games,
    )
    return GameModel(rows, season_data, Coefficients.load(),
                     kickoff_forecast if weather else None)


def print_breakdown(model: GameModel, events: list[dict], bets: list[Bet]) -> None:
    """Explain the projection for every game that has a recommended bet."""
    seen = set()
    for b in bets:
        event = next(e for e in events if f"{e['away_team']} @ {e['home_team']}" == b.game)
        if b.game in seen:
            continue
        seen.add(b.game)
        p = model.predict(event)
        if p is None:
            continue
        print(f"\n{p.away} @ {p.home}: projected {p.away} {p.away_pts:.1f} - {p.home} {p.home_pts:.1f}"
              f" (total {p.home_pts + p.away_pts:.1f})")
        print(f"  team ratings alone: {p.away} {p.base_away:.1f} - {p.home} {p.base_home:.1f}")
        for label, dm, dt in sorted(p.adjustments, key=lambda a: -abs(a[1]) - abs(a[2])):
            parts = []
            if abs(dm) >= 0.05:
                parts.append(f"{p.home if dm > 0 else p.away} {abs(dm):+.1f} margin")
            if abs(dt) >= 0.05:
                parts.append(f"total {dt:+.1f}")
            print(f"  {label:<20} {', '.join(parts)}")
        for note in p.notes:
            print(f"  - {note}")


STATS_LEGEND = {
    "nfl": "Stats% = team ratings adjusted for injuries, starting QB,\ntravel, divisional game and weather.",
    "ncaaf": "Stats% = college team ratings built on preseason and current Elo.",
}


def print_table(bets: list[Bet], bankroll: float, kelly_mult: float, max_pct: float,
                sport: str = "nfl") -> None:
    if not bets:
        print("No +EV bets on FanDuel right now. Passing is a winning decision too.")
        return
    header = f"{'Kickoff (UTC)':<13} {'Game':<44} {'Mkt':<9} {'Bet':<30} {'FD':>6} {'Fair':>6} {'Mkt%':>6} {'Stats%':>6} {'Win%':>6} {'EV':>6} {'Stake':>8}"
    print(header)
    print("-" * len(header))
    for b in bets:
        print(
            f"{b.commence_time:%a %m/%d %H:%M} "
            f"{b.game[:44]:<44} "
            f"{MARKET_LABELS.get(b.market, b.market):<9} "
            f"{_describe(b)[:30]:<30} "
            f"{_fmt_american(b.fd_price):>6} "
            f"{_fmt_american(b.fair_american):>6} "
            f"{b.market_prob:>6.1%} "
            f"{_pct(b.model_prob):>6} "
            f"{b.fair_prob:>6.1%} "
            f"{b.ev:>6.2%} "
            f"${stake(b, bankroll, kelly_mult, max_pct):>7.2f}"
        )
    print(
        f"\nMkt% = sharp-book no-vig odds. {STATS_LEGEND[sport]}\n"
        "Win% = blend used for EV and stakes."
    )
    print(
        f"{len(bets)} bet(s). Stakes = {kelly_mult:g}x Kelly on a ${bankroll:,.0f} "
        f"bankroll, capped at {max_pct:.0%} per bet."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="nfl_edge",
        description="Find +EV NFL bets on FanDuel by comparing against no-vig sharp-book odds.",
    )
    p.add_argument("--api-key", default=os.environ.get("ODDS_API_KEY"),
                   help="The Odds API key (or set ODDS_API_KEY)")
    p.add_argument("--sport", choices=sorted(SPORT_KEYS), default="nfl",
                   help="nfl (default) or ncaaf for college football")
    p.add_argument("--demo", action="store_true",
                   help="use bundled NFL sample data instead of the API")
    p.add_argument("--file", type=Path, help="read odds JSON from a file instead of the API")
    p.add_argument("--markets", default="h2h,spreads,totals")
    p.add_argument("--regions", default="us,eu",
                   help="'eu' includes Pinnacle, the sharpest reference book")
    p.add_argument("--min-ev", type=float, default=1.0, help="minimum EV in percent (default 1.0)")
    p.add_argument("--min-books", type=int, default=2,
                   help="minimum reference books offering the same line")
    p.add_argument("--devig", choices=sorted(DEVIG_METHODS), default="power")
    p.add_argument("--all-books", action="store_true",
                   help="average every book instead of preferring sharp ones")
    p.add_argument("--bankroll", type=float, default=1000)
    p.add_argument("--kelly", type=float, default=0.25, help="Kelly multiplier (default quarter Kelly)")
    p.add_argument("--max-bet", type=float, default=2.0, help="max stake as percent of bankroll")
    p.add_argument("--json", action="store_true", help="output JSON")

    par = p.add_argument_group("parlays")
    par.add_argument("--parlays", action="store_true",
                     help="also suggest parlays with the highest chance of winning")
    par.add_argument("--max-legs", type=int, default=3, help="max legs per parlay (default 3)")
    par.add_argument("--parlay-count", type=int, default=5, help="parlays to show (default 5)")
    par.add_argument("--parlay-legs", type=int,
                     help="build the single most likely parlay with exactly this many legs "
                          "(legs may have negative EV)")
    par.add_argument("--parlay-wager", type=float,
                     help="show slips for this wager instead of the suggested stake")
    par.add_argument("--parlay-min-ev", type=float, default=1.0,
                     help="minimum parlay EV in percent (default 1.0)")

    stats = p.add_argument_group("season stats model")
    stats.add_argument("--no-stats", action="store_true", help="use market odds only")
    stats.add_argument("--season", type=int,
                       help="NFL season (default: current, based on today's date)")
    stats.add_argument("--stats-file",
                       help="nflverse games.csv path or URL (default: download latest)")
    stats.add_argument("--data-dir",
                       help="folder with injuries_YYYY.csv / snap_counts_YYYY.csv "
                            "(default: download latest)")
    stats.add_argument("--no-weather", action="store_true", help="skip weather forecasts")
    stats.add_argument("--explain", action="store_true",
                       help="show what drives each game's projection "
                            "(injuries, QB, travel, weather)")
    stats.add_argument("--model-weight", type=float, default=0.1,
                       help="max weight of the stats model in the blend, 0-1 "
                            "(default 0.1; see python -m nfl_edge.backtest)")
    stats.add_argument("--require-agreement", action="store_true",
                       help="only show bets the stats model also favors over the market")
    stats.add_argument("--ratings", action="store_true", help="print team power ratings")
    args = p.parse_args(argv)

    if args.demo and args.sport != "nfl":
        p.error("--demo only has NFL sample data; use --file for college odds")
    if args.demo or args.file:
        events = json.loads((args.file or SAMPLE).read_text())
    else:
        if not args.api_key:
            p.error("an API key is required (get a free one at https://the-odds-api.com), "
                    "or run with --demo")
        try:
            events, remaining = fetch_odds(
                args.api_key, args.markets.split(","), args.regions.split(","), args.sport
            )
        except OddsAPIError as e:
            print(e, file=sys.stderr)
            return 1
        if remaining is not None:
            print(f"(API requests remaining: {remaining})", file=sys.stderr)

    model = None
    if not args.no_stats:
        today = date.today()
        season = args.season or (today.year if today.month >= 3 else today.year - 1)
        try:
            if args.sport == "ncaaf":
                model = cfb.CfbModel(cfb.load_schedule(season, args.stats_file, season))
            else:
                model = build_model(season, args.stats_file, args.data_dir, not args.no_weather)
        except (data.DataError, ValueError) as e:
            print(f"Warning: {e}; continuing with market odds only.", file=sys.stderr)
        else:
            print(f"(Season stats: {season}, {model.num_games} completed games)",
                  file=sys.stderr)
            if args.ratings and not args.json:
                print_ratings(model.ratings)

    bets = find_bets(
        events,
        min_ev=args.min_ev / 100,
        min_books=args.min_books,
        devig=args.devig,
        sharp_only=not args.all_books,
        include_started=args.demo,
        model=model,
        model_weight=args.model_weight,
        require_agreement=args.require_agreement,
    )
    max_pct = args.max_bet / 100

    if args.parlay_legs:
        return print_most_likely(events, args, model, max_pct)

    parlays = None
    if args.parlays:
        # Any leg with an edge can help a parlay, even one too small to bet alone.
        legs = find_bets(
            events,
            min_ev=0.0,
            min_books=args.min_books,
            devig=args.devig,
            sharp_only=not args.all_books,
            include_started=args.demo,
            model=model,
            model_weight=args.model_weight,
            require_agreement=args.require_agreement,
        )
        parlays = build_parlays(legs, max_legs=args.max_legs, min_ev=args.parlay_min_ev / 100,
                                count=args.parlay_count)

    if args.json:
        singles = [
            {
                "game": b.game,
                "kickoff": b.commence_time.isoformat(),
                "market": b.market,
                "bet": _describe(b),
                "fanduel_odds": b.fd_price,
                "fair_odds": b.fair_american,
                "win_prob": round(b.fair_prob, 4),
                "market_prob": round(b.market_prob, 4),
                "stats_prob": None if b.model_prob is None else round(b.model_prob, 4),
                "ev": round(b.ev, 4),
                "stake": round(stake(b, args.bankroll, args.kelly, max_pct), 2),
                "reference_books": b.books,
            }
            for b in bets
        ]
        if parlays is None:
            print(json.dumps(singles, indent=2))
        else:
            print(json.dumps({
                "bets": singles,
                "parlays": [_parlay_json(p, args.bankroll, args.kelly, max_pct) for p in parlays],
            }, indent=2))
    else:
        print_table(bets, args.bankroll, args.kelly, max_pct, args.sport)
        if parlays is not None:
            print_parlays(parlays, args.bankroll, args.kelly, max_pct, args.parlay_wager)
        if args.explain and model is not None:
            print_breakdown(model, events, bets)
    return 0
