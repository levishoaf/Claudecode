# nfl_edge: find +EV NFL bets on FanDuel

No program can make you win every bet. What *can* give you a long-run edge
is betting only when **FanDuel pays more than the true odds**. This tool:

1. Pulls live NFL moneyline, spread and total prices from FanDuel and other
   books via [The Odds API](https://the-odds-api.com).
2. Builds a **fair (no-vig) probability** for each line from sharp books
   (Pinnacle, Circa, BetOnline, LowVig) by removing their margin.
3. Compares each FanDuel price to that fair probability and lists the bets
   with **positive expected value**, sorted by edge.
4. Blends in a **game model**: this season's team ratings adjusted for
   injuries, starting QB, rest, travel, divisional games and weather (see below).
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
| `--explain` | off | Show what drives each recommended game's projection |
| `--no-weather` | off | Skip weather forecasts |
| `--data-dir` | download | Folder with `injuries_YYYY.csv` / `snap_counts_YYYY.csv` |
| `--no-stats` | off | Skip the stats model |
| `--season` | current | Season to pull stats from |
| `--stats-file` | nflverse | Local `games.csv` path or URL |

Each live run costs `markets × regions` API credits (6 with the defaults).
Keep `eu` in `--regions`, since that is where Pinnacle comes from.

## Game model

All data is free and needs no extra keys. It's cached in `~/.cache/nfl_edge`
(override with `NFL_EDGE_CACHE`).

| Factor | Source | How it's used |
|---|---|---|
| Team strength | nflverse results, this season | Offense/defense ratings (ridge regression on points scored and allowed) |
| Starting QB | nflverse injury report + starts | Usual starter Out/Doubtful/Questionable |
| Other injuries | Injury report × snap counts | Each Out/Doubtful/Questionable player weighted by how much he plays |
| Rest | Schedule | Rest-day difference (byes, short weeks) |
| Travel | Stadium coordinates | Extra miles the away team travels |
| Home field | Schedule | Removed for neutral and international games |
| Divisional game | Schedule | Familiar opponents play closer games |
| Weather | Open-Meteo forecast at kickoff | Wind over 10 mph and cold below 45°F, outdoor games only |
| Dome | Schedule | Indoor scoring environment |

How much each factor is worth isn't guessed. `python -m nfl_edge.backtest
--write` fits it on 2015–2025 and stores it in `nfl_edge/factors.json`. Every
game is rebuilt using only information available before kickoff. Each
estimate is then shrunk toward zero according to its uncertainty, so noisy
factors barely count. Current fitted values:

- **Starting QB out:** about 3.1 points of margin and 2.8 fewer total points.
- **Wind:** about 0.33 fewer points per mph above 10 mph.
- **Injuries:** a full-time defensive starter out adds about 0.7 to the
  opponent's margin.
- **Rest, travel, divisional:** small.

`--explain` prints the breakdown for each game you're told to bet (illustrative output):

```
IND @ PIT: projected IND 22.4 - PIT 24.0 (total 46.4)
  team ratings alone: IND 22.6 - PIT 23.8
  home field           PIT +0.4 margin
  rest                 PIT +0.1 margin
  - rest PIT 10d vs IND 7d
  - forecast 48F, wind 14 mph
```

The final probability is `(1 - w) * market + w * model`. `w` is
`--model-weight` (default 0.1), scaled down until both teams have played
8 games.

### What the backtest says

Out-of-sample results: fit on 2015–2020, tested on 1,196 games in 2021–2025.

| Prediction | Win log loss | Margin error | Total error |
|---|---|---|---|
| Closing market line | **0.608** | **9.85** | **10.26** |
| Team ratings only | 0.647 | 10.46 | 10.76 |
| Ratings + all factors | 0.640 | 10.39 | 10.58 |
| Market + 10% model | 0.609 | 9.86 | 10.26 |

The factors are real: they improve the model. But the closing market
already prices them in, and nothing beats it. The model picked the right
side of the closing spread 48.1% of the time (52.4% is break-even). The
backtest also asks whether the market under-reacts to any factor; none
showed a significant gap.

So where does the edge come from? From **FanDuel lagging the sharp books**,
which the finder catches directly. The model is a second opinion and a
sanity check. Use `--require-agreement` to drop bets it disagrees with, or
`--model-weight 0` for market only.

Rerun the backtest yourself (takes about 15 seconds):

```bash
python -m nfl_edge.backtest
```

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
python -m unittest discover -t . -s tests
```
