# nfl_edge: find +EV NFL bets on FanDuel

No program can make you win every bet. What *can* give you a long-run edge
is betting only when **FanDuel pays more than the true odds**. This tool:

1. Pulls live NFL moneyline, spread and total prices from FanDuel and other
   books via [The Odds API](https://the-odds-api.com).
2. Builds a **fair (no-vig) probability** for each line from sharp books
   (Pinnacle, Circa, BetOnline, LowVig) by removing their margin.
3. Compares each FanDuel price to that fair probability and lists the bets
   with **positive expected value**, sorted by edge.
4. Suggests a stake with **fractional Kelly** sizing (quarter Kelly, capped
   at 2% of bankroll by default).

Sharp-book closing lines are the best public predictor of NFL outcomes.
When FanDuel lags behind a sharp move or shades a side, that gap is your edge.

## Setup

Requires Python 3.10+ and no third-party packages.

1. Get a free API key at https://the-odds-api.com (500 credits/month).
2. `export ODDS_API_KEY=your_key`

## Usage

```bash
python -m nfl_edge --demo            # try it on bundled sample data
python -m nfl_edge                   # live odds
python -m nfl_edge --bankroll 500 --min-ev 2
python -m nfl_edge --markets h2h --regions us,eu   # cheaper: 2 credits
python -m nfl_edge --json
```

| Option | Default | Meaning |
|---|---|---|
| `--min-ev` | `1.0` | Minimum edge in % to show a bet |
| `--min-books` | `2` | Reference books that must offer the exact same line |
| `--devig` | `power` | `power` or `multiplicative` vig removal |
| `--all-books` | off | Average all books instead of preferring sharp ones |
| `--bankroll` | `1000` | Bankroll for stake sizing |
| `--kelly` | `0.25` | Kelly multiplier |
| `--max-bet` | `2.0` | Stake cap as % of bankroll |

Each live run costs `markets × regions` API credits (6 with the defaults).
Keep `eu` in `--regions`, since that is where Pinnacle comes from.

## Using it well

- **Act fast.** Edges close within minutes. Run it close to when you bet,
  and check that FanDuel's price hasn't moved.
- **Edges are small.** Expect 1–5%. Profit comes from volume and
  discipline, not from any single bet. Losing streaks of 10+ bets are normal.
- **Track closing line value (CLV).** If your bets regularly beat the
  closing line, you're doing it right, even during a losing stretch.
- **Expect limits.** FanDuel restricts accounts that consistently beat its
  lines.
- Only bet where it's legal for you, and only what you can afford to lose.
  Problem gambling help: 1-800-GAMBLER.

## Tests

```bash
python -m unittest discover tests
```
