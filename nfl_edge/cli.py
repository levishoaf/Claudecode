"""Command-line entry point: python -m nfl_edge"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from .api import OddsAPIError, fetch_nfl_odds
from .finder import Bet, find_bets
from .odds import DEVIG_METHODS
from .stats import SeasonModel, StatsError, load_games

SAMPLE = Path(__file__).with_name("sample_odds.json")
MARKET_LABELS = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}


def _fmt_american(price: int) -> str:
    return f"+{price}" if price > 0 else str(price)


def _describe(bet: Bet) -> str:
    if bet.point is None:
        return f"{bet.pick} ML"
    if bet.market == "totals":
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


def print_table(bets: list[Bet], bankroll: float, kelly_mult: float, max_pct: float) -> None:
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
        "\nMkt% = sharp-book no-vig odds, Stats% = season-stats model, "
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
    p.add_argument("--demo", action="store_true", help="use bundled sample data instead of the API")
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

    stats = p.add_argument_group("season stats model")
    stats.add_argument("--no-stats", action="store_true", help="use market odds only")
    stats.add_argument("--season", type=int,
                       help="NFL season (default: current, based on today's date)")
    stats.add_argument("--stats-file",
                       help="nflverse games.csv path or URL (default: download latest)")
    stats.add_argument("--model-weight", type=float, default=0.1,
                       help="max weight of the stats model in the blend, 0-1 "
                            "(default 0.1; see python -m nfl_edge.backtest)")
    stats.add_argument("--require-agreement", action="store_true",
                       help="only show bets the stats model also favors over the market")
    stats.add_argument("--ratings", action="store_true", help="print team power ratings")
    args = p.parse_args(argv)

    if args.demo or args.file:
        events = json.loads((args.file or SAMPLE).read_text())
    else:
        if not args.api_key:
            p.error("an API key is required (get a free one at https://the-odds-api.com), "
                    "or run with --demo")
        try:
            events, remaining = fetch_nfl_odds(
                args.api_key, args.markets.split(","), args.regions.split(",")
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
            model = SeasonModel(load_games(season, args.stats_file))
        except StatsError as e:
            print(f"Warning: {e}; continuing with market odds only.", file=sys.stderr)
        else:
            print(f"(Season stats: {season}, {model.num_games} completed games)",
                  file=sys.stderr)
            if args.ratings and not args.json:
                print_ratings(model)

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

    if args.json:
        print(json.dumps([
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
        ], indent=2))
    else:
        print_table(bets, args.bankroll, args.kelly, max_pct)
    return 0
