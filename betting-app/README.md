# Weekly NFL + NCAAF Picks (keyless, informational only)

**Disclaimer:** For information only. No pick is guaranteed and no profit is promised; you can lose everything you stake.
All win probabilities, edges, EVs and stakes are *estimates* from market prices. Gamble responsibly. 21+ (or legal age) and
legal jurisdictions only. Problem gambling help: **1-800-GAMBLER**. The app never places bets or touches any account.

## What it does
- Top 30 single bets (moneyline / spread / total / props where a source provides them) ranked by EV per $1: best price, est. win %,
  implied %, edge, EV, fractional-Kelly stake (default 0.25 Kelly, capped at 2% of bankroll). Min-probability filter and sort by EV, edge, win % or payout.
- 5 parlays (3-5 legs) chosen to maximise EV: one leg per game (so no same-game correlation), no duplicate legs, combined payout / win % / EV.
  Parlay vig compounds, so parlays are usually worse value than their legs as singles; the UI says so. Parlay stake cap 0.5%.
- Every row shows how many sources fed its probability and a confidence label (`none`, `low`, `medium`, `high`). A "Data sources" table lists which sources worked or failed.

## Run
Python 3.10+; **standard library only, no API keys, no pip installs.**
```bash
cd betting-app
python3 refresh.py             # fetch (cached 6h) + rank -> data/picks.json
python3 server.py              # http://127.0.0.1:8000  (refreshes automatically when the cache is older than the TTL)
python3 server.py --sample     # force labeled SAMPLE data
python3 refresh.py --static site/   # or build a static site (index.html + picks.json)
python3 -m unittest discover -s tests -v
```
Weekly refresh (cron, Tuesday 9:00): `0 9 * * 2 cd /path/betting-app && python3 refresh.py --static site/`

## Data sources (keyless, read-only)
| Source | Notes |
|---|---|
| FanDuel public sportsbook JSON | Unauthenticated GET of the public endpoint the FanDuel web site uses. **Could not be reached from the build sandbox (see below).** The parser is written from the publicly visible response layout and is only unit-tested on a synthetic fixture, not live data. |
| ESPN public scoreboard JSON | Works. Provides **one book only (DraftKings)**: moneyline, spread, total. |
| SAMPLE DATA | Fictional teams/players/odds generated per ISO week for offline demos; always labeled. Used when no live source returns events, or with `--sample`. |

Providers are pluggable (`app/providers.py`: subclass `Provider`, return normalised events, add it to `app/pipeline.py`).
Politeness: one request per league per run, 1.5 s spacing, on-disk cache (`data/cache/`, 6 h TTL, stale cache used on error). No logins, credentials,
API keys, bet placement, CAPTCHA/anti-bot circumvention. A 403 / geo-block / bot-wall is recorded and **not** worked around.

### What happened when live access was tried (build sandbox)
- `sbapi.nj.sportsbook.fanduel.com` and `sportsbook.fanduel.com`: the sandbox egress proxy refused the tunnel (`CONNECT ... 403 Forbidden`, "Tunnel connection failed: 403 Forbidden"). No FanDuel data was obtained. This was not worked around.
- `site.api.espn.com` scoreboard (NFL, and NCAAF FBS group 80): HTTP 200, 60 upcoming events with DraftKings lines.
- Run your own machine to see whether FanDuel is reachable for you: `python3 refresh.py` prints per-source status.
- **Terms/geo:** automated access to a sportsbook's endpoints may conflict with its terms of service and the sites are generally geo-restricted to legal states. Check before enabling it (`--no-fanduel` disables it). Use at your own risk; the FanDuel state subdomain is set with `--state`.

## How probabilities and EV work, and the honest limits
1. Each book's two-way prices are converted to implied probabilities and the vig removed (power method; proportional also available in code).
2. Books are combined by weighted average (Pinnacle/other sharps weight 5/3/2, everyone else 1) **only on identical lines**.
3. A bet is "+EV" when the best available price beats that consensus: EV per $1 = p x decimal - 1. Kelly = (p x dec - 1)/(dec - 1), scaled by the fraction, capped.

**Limits you must understand**
- With keyless sources there is no sharp book (Pinnacle etc.), so the consensus is weaker than the brief's ideal. Confidence is `low`/`medium` and never `high` live.
- **With only one book available (what ESPN alone gives), a book's own no-vig probability can never show an edge against that same book.** Every EV is then just the negative margin; the UI says "No edge can be measured" and suggests no stake. A real edge needs at least a second independent book (FanDuel + DraftKings both working, for instance). That is the current live situation here.
- Edges above 8% are flagged "verify line" (often stale prices); above 25% are dropped as likely data errors.
- Parlay win probability assumes independent legs from different games; real parlays must be placed at one book, so shown payouts (best price per leg) are optimistic.
- Player props are only evaluated if a provider supplies them; neither live source currently does (sample data includes a few fictional ones).
- Lines move; always check the live price before acting. Past model output is not evidence of future results.

## Layout
`app/odds_math.py` math | `app/engine.py` ranking + parlays | `app/providers.py` ESPN/FanDuel/cache/merge | `app/sample_data.py` | `refresh.py` | `server.py` | `web/index.html` | `tests/`
