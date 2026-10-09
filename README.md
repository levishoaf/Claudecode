# nfl_edge: find +EV NFL bets on FanDuel

No program can make you win every bet. What *can* give you a long-run edge
is betting only when **FanDuel pays more than the true odds**. This tool:

1. Pulls live NFL moneyline, spread and total prices from FanDuel and other
   books via [The Odds API](https://the-odds-api.com).
2. Builds a **fair (no-vig) probability** for each line from sharp books
   (Pinnacle, Circa, BetOnline, LowVig) by removing their margin.
3. Compares each FanDuel price to that fair probability and lists the bets
   with **positive expected value**, sorted by edge.
4. Blends in a **season-stats model** built from this season's completed
   games (see below).
5. Suggests a stake with **fractional Kelly** sizing (quarter Kelly, capped
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
| `--model-weight` | `0.1` | Max weight of the stats model in the blend (0 = market only) |
| `--require-agreement` | off | Only show bets the stats model also favors |
| `--ratings` | off | Print this season's team power ratings |
| `--no-stats` | off | Skip the stats model |
| `--season` | current | Season to pull stats from |
| `--stats-file` | nflverse | Local `games.csv` path or URL |

Each live run costs `markets × regions` API credits (6 with the defaults).
Keep `eu` in `--regions`, since that is where Pinnacle comes from.

## Season stats model

Each run downloads this season's completed game results from
[nflverse](https://github.com/nflverse/nfldata) and fits offense and defense
ratings for every team (ridge regression on points scored and allowed, with
home field). Those ratings give a projected margin and total for each game,
which become win/cover/over probabilities.

The final probability is `(1 - w) * market + w * stats`. `w` is
`--model-weight` scaled down until both teams have played 8 games, so early
in the season the stats barely move the estimate. The table shows all three
numbers: `Mkt%`, `Stats%`, and the blended `Win%` used for EV and stakes.

**Be realistic about what stats add.** On 2015–2025, predicting each week
from earlier weeks only, blending the stats model in made predictions
slightly *worse* than the closing market at every weight. On its own it
picked the right side of the closing spread 48.9% of the time (52.4% is
break-even). The betting market already prices in last week's box score.
That's why the default weight is small. Check it yourself:

```bash
python -m nfl_edge.backtest              # downloads nflverse data
python -m nfl_edge.backtest --start 2020 --end 2025
```

`--require-agreement` uses the stats as a filter: it drops +EV bets the
stats model disagrees with. You'll get fewer bets, but by the backtest
above, the ones you keep are no more likely to win.

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
