# Weekly NFL + NCAAF Picks (keyless, informational only, paper-trading ledger)

**Disclaimer:** For information only. No pick is guaranteed and no profit is promised; you can lose everything you stake.
All win probabilities, edges, EVs and stakes are *estimates*. Gamble responsibly. 21+ (or legal age) and legal jurisdictions only.
Problem gambling help: **1-800-GAMBLER**. The app never places bets or touches any account; the ledger is paper trading.

## Read this first: what the app honestly does this week
The page opens with a **"Bottom line this week"** box. A pick is a **BET** only if it clears the discipline rules
(estimated edge >= 3%, EV > 0, confidence >= "low"). Everything else is **watchlist** (shown faintly, no stake). If nothing clears
the bar the box says **PASS**; with only one live sportsbook (the current situation) that is the expected result, because a
book's own no-vig line cannot show an edge against itself. Passing is a legitimate, often profitable decision.

## Run (Python 3.10+, standard library only, no API keys, no pip installs)
```bash
cd betting-app
python3 refresh.py                  # fetch (cached) + rank + append paper picks to data/ledger.jsonl -> data/picks.json
python3 grade.py                    # settle past paper picks from ESPN final scores -> data/track.json
python3 backtest.py                 # (slow first run, ~6 min polite requests) -> data/backtest.json; decides whether the model is blended
python3 snapshot.py                 # fresh odds snapshot only (for closing-line value); run near kickoff
python3 probe_sources.py            # which keyless hosts are reachable from this machine
python3 server.py [--sample]        # http://127.0.0.1:8000  (add ?theme=dark or ?theme=light to force a theme)
python3 refresh.py --static site/   # static site (index.html, picks.json, track.json, backtest.json)
python3 refresh.py --sample         # labeled fictional data (writes data/picks_sample.json, never the ledger)
python3 refresh.py --debug-dump dbg/  # save raw FanDuel responses/errors for parser fixing (see below)
python3 experiments.py              # walk-forward test of model tweaks (needs backtest.py cache) -> data/experiments.json
python3 -m unittest discover -s tests -v
```
Flags: `--no-fanduel`, `--no-model`, `--no-fpi`, `--state nj`, `--ttl SECONDS`. `data/` holds the cache, ledger, snapshots and outputs; delete `data/cache/` to force fresh fetches.

### Using the page
Click a column header to sort, click a row (or press Enter on it) for the detail drawer (sources, every price seen, market vs ratings vs ESPN-predictor vs used probability, Kelly, flags).
Parlay cards can be sorted and filtered. The "How to read this page" panel defines every column. Bankroll is remembered in your browser only.

### FanDuel debug-dump steps (run on a machine where FanDuel is reachable and legal for you)
1. `python3 refresh.py --debug-dump dbg/` (one request per league; nothing is cached or committed).
2. Look in `dbg/`: `fanduel_<sport>.json` is the raw response; `fanduel_<sport>.error.json` holds the exact HTTP status and first 4 KB of an error body (a 403/bot-wall means stop; do not try to get around it).
3. If a JSON file exists but `refresh.py` reports "no parseable events", the response shape differs from what `parse_fanduel_page` expects: share the file (strip anything personal; it should contain none) so the parser can be fixed against real data.

## Data sources (keyless, read-only, polite: one request per page, 1.5 s spacing, disk cache, stale-cache on error)
| Source | Status from the build sandbox |
|---|---|
| FanDuel public sportsbook JSON | **Blocked**: the sandbox proxy refuses the tunnel (`403 Forbidden`). Not worked around. Parser has a strict path and a tolerant fallback but has never seen a real response; use `--debug-dump` locally. |
| ESPN public scoreboard / summary JSON | Works. Odds are **one book only (DraftKings)**; also used for results, ratings and (recent games only) closing lines. |
| SAMPLE DATA | Fictional teams/odds, always labeled; automatic fallback if no live source returns events. Never written to the ledger. |

**Second-book search (2026-10-10):** `python3 probe_sources.py` makes one GET per candidate host and records the result in `data/source_probe.json`.
Reachable: `site.api.espn.com`, `site.web.api.espn.com` (both list **DraftKings only**, in the scoreboard, summary `pickcenter` and header odds blocks).
Blocked by the sandbox proxy (`403` on CONNECT, recorded and left alone): `sports.core.api.espn.com`, `cdn.espn.com`, FanDuel, DraftKings direct,
Action Network, VegasInsider, Covers, OddsShark, Sportsbook Review. **So only one book is reachable here and the weekly answer stays PASS.** The engine already
does multi-book consensus (spread/total comparisons only on identical lines), so a second provider added later is used automatically.

Automated access to a sportsbook may conflict with its terms of service and is generally geo-restricted to legal states; check before enabling it (`--no-fanduel` disables it).
`--debug-dump DIR` makes one fresh request per league and saves the raw JSON (or the exact HTTP status and first 4 KB of the error body) to `DIR/fanduel_<sport>[.error].json`. Send me a dump and the parser can be fixed against real data.

## Probabilities
1. **Market**: each book's two-way prices -> implied probs -> vig removed (power method) -> weighted average across books on identical lines (Pinnacle/other sharps weighted most when present). With one book this is just that book's no-vig line.
2. **Ratings model (independent, simple)**: margin-based Elo in points from public ESPN results (NFL and NCAAF separate; home-field 2.0 / 2.5 pts; margin capped; ratings regress 30% / 35% to the mean at each new season and learn faster early in a season; unknown opponents such as FCS teams move established teams less). Rating gap -> spread -> win probability via a normal CDF. Covers moneyline and spreads only (not totals/props).
3. **Blend**: `p = w*market + (1-w)*model`, w = 0.80. **It is OFF by default** and turns on only if `data/backtest.json` shows the blend beating the market closing line (lower Brier and log loss) in every league with at least 40 games. Otherwise picks are market-only and the model is a reference column ("Ratings %"), flagged "model disagrees" at >= 8 points (large gaps usually mean the model is wrong).
4. EV per $1 = p x decimal - 1; Kelly fraction 0.25, per-bet cap 2%.

## Backtest results (run 2026-10-10; reproduce with `python3 backtest.py`)
Ratings parameters were tuned on the **2025** season only (grid over k and sigma), so the 2026 comparison is out of sample. Market = DraftKings closing moneyline, no-vig, from ESPN summary data (only recent games still expose it).

| | NFL | NCAAF |
|---|---|---|
| 2025 walk-forward, n | 208 | 566 |
| Brier / log loss (coin flip = 0.250 / 0.693) | 0.225 / 0.638 | 0.187 / 0.551 |
| 2026 games with closing lines, n | 33 | 76 |
| Brier: model / market / 80-20 blend | 0.219 / 0.249 / 0.240 | 0.200 / **0.174** / 0.175 |
| Margin MAE: model vs market spread | 8.8 vs 8.0 | 13.3 vs 12.5 |

Reading it plainly: **in NCAAF the market clearly beats the model and the blend does not improve on the market alone. In NFL the n=33 sample is far too small to conclude anything** (the market's Brier of 0.249 there is just a run of upsets; its margin error is still lower than the model's). The model has real skill versus a coin flip, but it does **not** demonstrably beat or complement the market, so the app does not use it for EV. Treat any "model edge" as unproven. Rerun the backtest as the season (and the sample) grows.

### Model-change experiments (`python3 experiments.py`, results in `data/experiments.json`)
Tried one change at a time (rest days, home-field size, season regression, margin cap, FCS/unseen-team start rating), keeping a change only if log loss improved out of sample
on **both** the 2025 and 2026 walk-forwards and the blend-vs-market Brier did not get worse. **Result (2026-10-10): none were kept**, the baseline parameters stand
(positive deltas in the log are worse). Rest-day and unseen-team options remain in the code, off by default. Opponent adjustment is already inherent in the Elo update.
**Injuries** were not tried: ESPN does not expose historical injury reports, so they cannot be validated by backtest. ESPN's own "Matchup Predictor" win probability (FPI-style, upcoming games only)
is shown as a reference (`fpi_prob`, in the pick detail and the ledger) and is not used in EV; it cannot be backtested because ESPN drops it after the game, so the ledger collects it for a future check.
**More markets:** the only keyless sources reachable (ESPN scoreboard, summary `pickcenter`, header) return only moneyline, spread and total from one book (no alt lines, halves, team totals or props), so nothing was added.

## Discipline rules (all configurable in `app/engine.py` DEFAULTS)
- Minimum edge 3%, EV > 0, minimum confidence "low" -> BET; else watchlist with zero stake.
- Max combined stake per game 2% (bets on the same game are correlated and must not stack).
- Max total weekly exposure 10% of bankroll across singles + parlays (stakes scale down proportionally; the page shows exposure and whether it was scaled).
- Parlays: 3-5 legs, **exact enumeration** of every combination from the pool (up to ~34 legs, a few hundred thousand combos, no sampling), one leg per game, no duplicates, at most 2 legs on the same totals direction (a crude proxy for shared scoring environment/weather; real correlation such as conference or weather is **not** modelled), stake cap 0.5%. When at least 3 games have legs that clear the single-bet bar, BET parlays are built only from those legs; the remaining slots are filled with clearly labelled watchlist parlays. A parlay is staked only if EV > 0 and every leg qualifies. Parlay vig compounds; best prices may sit at different books so real payouts are lower than shown.
- Edges above 8% are flagged "verify line" (often stale); above 25% are dropped as likely data errors.
- Confidence: `none` (one source, market only), `low`, `medium` (>= 4 books), `high` (sharp book + >= 3 books). Live keyless data can never reach `high`.

## Line snapshots (own closing lines)
Every live refresh appends a compact timestamped snapshot of all h2h/spread/total prices to `data/snapshots.jsonl` (gitignored). `python3 snapshot.py` captures a fresh one
(no ranking, no ledger); run it hourly on game days. `grade.py` computes closing-line value from the **last snapshot taken before kickoff, same book, same line**, and falls back to ESPN's closing odds
(recent games only). A pick whose line moved has no same-line CLV (shown as n/a). A weekly-only refresh gives one snapshot, i.e. not a true close; snapshot more often for meaningful CLV.

## Results ledger and track record (paper trading)
Each live refresh appends the published top-30 singles and 5 parlays (odds, line, stake, tier, timestamp) to `data/ledger.jsonl`, de-duplicated so re-running a week does not double count. `python3 grade.py` settles finished games from ESPN final scores (pushes handled; a parlay with a pushed leg pays on the remaining legs) into `data/grades.json` and writes `data/track.json`: record, units won, ROI (flat 1 unit and Kelly-staked), average closing-line value where ESPN still exposes closing odds (same line only), split by singles/parlays, bet/watchlist tier and confidence. 1 unit = 1% of a notional bankroll. The UI shows it, or "No settled picks yet".

## Weekly automation
`../.github/workflows/weekly.yml` (repo root): Tuesday cron + manual dispatch; runs tests, `grade.py`, `refresh.py --static site/`, and uploads the site as a workflow **artifact** (no Pages, no commits). The ledger is carried between runs with `actions/cache`, which is best-effort (caches can be evicted). **FanDuel may be blocked or geo-restricted from GitHub runners too**; the app records that and falls back to ESPN, then to sample data.

## Limits and decisions
- One live book means no market-vs-market edge detection; the honest weekly answer is usually PASS. A second independent book (FanDuel from a machine where it is reachable, via the debug-dump workflow) is the main upgrade.
- ESPN odds are DraftKings lines relayed by ESPN, possibly slightly delayed; confirm the live price before acting.
- Props and totals are market-only; no live source supplies props.
- Backtest samples are small and the market comparison uses a single book's closing line. Past performance, including paper results, does not predict future results.
- Decisions made without asking: power devig, 80/20 blend weight (not tuned), 3% min edge, 10% weekly cap, 2% per-game cap, ratings tuned on one prior season, ledger logs watchlist picks too (zero Kelly units) so the bar's usefulness can be measured.

## Layout
`app/odds_math.py` | `app/engine.py` ranking, discipline, exact parlay enumeration | `app/ratings.py` Elo model | `app/backtest.py` backtest + experiments | `app/ledger.py` ledger/grading | `app/snapshots.py` | `app/providers.py` ESPN/FanDuel/cache | `app/sample_data.py` | `refresh.py` `grade.py` `backtest.py` `experiments.py` `snapshot.py` `probe_sources.py` `server.py` | `web/index.html` | `tests/` | `NOTES.md` dated work log
