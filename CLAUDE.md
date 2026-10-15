# Project notes for Claude

`nfl_edge` finds value bets on FanDuel for the NFL (`--sport nfl`) and
college football (`--sport ncaaf`), plus UFC fights (`--sport ufc`) and MLB (`--sport mlb`). See README.md for usage. Saved paper
bets live in `bets/` and are graded with `python -m nfl_edge.grade <file>`.

## The user's standing betting preference

**Bets can come from any market in FanDuel's NFL section, not just game
lines.** When picking bets, consider all of them and choose the best by the
rules below:

- **Game lines:** moneyline, spread and total.
- **Alternate lines:** alternate spreads and totals, team totals, and
  first-half or quarter lines.
- **Player props and their alternate lines:** passing, rushing and
  receiving yards, receptions, and passing touchdowns.
- **Touchdown scorer bets:** anytime TD and first TD.

Estimate each market from the data available: the game model for spreads
and totals at any line, and player game logs for props. Say plainly when a
market's FanDuel price isn't available, and give its break-even odds
instead.

Every bet the user asks for, singles and parlays alike, must aim for the
**highest win percentage with the best payout**. In practice:

- **Rank by expected value** (win probability × payout). It is the one
  measure that weighs chance of winning and payout together. Break ties
  toward the higher win probability.
- **For every pick, show** the win percentage, the odds, the payout per
  $100 (or per the user's stake), the expected value, and the worst odds
  that would still be worth taking.
- **Parlays:** use legs from different games, prefer legs with an edge, and
  rank the parlays by win probability among those with the best expected
  value. Show the result as a FanDuel-style bet slip (`format_slip` in
  `nfl_edge/cli.py`).
- **Be honest about trade-offs.** A higher win chance always means a
  smaller payout. When nothing on the board has positive expected value,
  say so plainly rather than overstating any pick.
- **Skip players who missed most of a season.** For any player-based bet
  (props, QB touchdown legs and so on), leave out any player who appeared
  in half or fewer of his team's games this season or last season. A player
  traded mid-season counts against one season's schedule, not two. Rookies
  are judged on the current season only.
- **Skip injured players.** Leave out any player listed Out, Doubtful or
  Questionable, or who missed practice before game statuses post, from
  every single and parlay leg.

## Web version

The bet builder also runs as a private web page:
https://claude.ai/artifact/QyRUWeX7NkXuVUSn4c3xsv

**Every time the application changes, update the web page too.** Rebuild it
with fresh data and republish to the same URL; carry any visible app change
into `web/template.html` first. End each reply with the link to the releases page
(https://github.com/levishoaf/Claudecode/releases/latest, where the app is
downloaded; never the Actions page) and the link to the web page.

To rebuild: read the page's placed bets first (ArtifactData `list` of the
`placed` collection on the page's URL, saved as a JSON list), then run
`python3 web/export.py <scratchpad>/bet-builder.html --placed <that file>`.
This grades placed bets and logs this week's picks in `web/history.json`
(commit and push it) for the Not placed tab. The page declares the `db`
capability, so omit `capabilities` when republishing to keep it.

The app keeps its own placed and not-placed bets in
`~/Bet Builder/my bets.json` and grades them on every refresh.

## Releases and auto-update

Each push to the branch builds the programs (`.github/workflows/build-executables.yml`)
and publishes them as GitHub Release `build-<run number>`. The windowed app is
stamped with its build number (`nfl_edge/_build.py`, written by the workflow,
not committed) and updates itself from the latest release (`nfl_edge/updater.py`,
`Updates` in `bets_gui.py`): the running app renames its own .exe or .app aside
(hidden), moves the new one into its place under the same name, starts it with a
clean PyInstaller environment (`updater.clean_env`) and quits; leftovers are deleted
on the next start. The window is resizable (`App.relayout`). Keep the release asset names (`updater.ASSETS`) and the
workflow in step.

## Discord bot

`discord_bot.py` (logic in `nfl_edge/botcore.py`) is the same bet builder as a
Discord bot that runs on the user's computer, with one shared bet history for
the whole server (`discord bets.json` next to the program). Discord is not
reachable from this workspace, so check changes with
`python3 discord_bot.py --selftest`, which runs every command's logic against
live data without connecting. The token lives in `discord token.txt` on the
user's machine; never ask for it here or commit it. The slash commands' type hints are
strings (`from __future__ import annotations`), which discord.py looks up in the module, so
`discord` and `app_commands` must stay imported at module level; the self-test builds every
command (`run_bot(None, connect=False)`) so a broken one fails the build. The Windows bot
offers to start at sign-in (`--startup on/off`, a .cmd in the Startup folder), updates
itself from `BetBuilder-Windows-DiscordBot.exe` (`updater.BOT_ASSETS`, every 6 hours), opens
its invite link when it's in no server (`invite_link`, permissions 84992), and forgets a
token Discord rejects so it can ask again.

## Making sure bets are on FanDuel

The free board (`nfl_board`, `always_offered=True` by default) only builds
bets with no line that could differ on FanDuel: moneylines and anytime TD
scorers for starters (`SURE_MARKETS`). Guessed lines (spreads, totals, team
totals, "X+" prop ladders) were shown before and the user found many weren't
on FanDuel, so they appear only when the user picks that bet type
(`always_offered=False`). Don't add guessed lines back to
the default board ("All bets"). The user asked for every bet when they choose a
bet type, so the Bet type menu's other types (spreads, totals, yardage and other
props) show every model line, marked **Confirm line** (`Bet.confirm_line`, `picks.every_line_of`). Receiving yards bets are only listed at -300 or better (`picks.TYPE_MIN_ODDS`):
each player's likeliest line within that limit. The **Money Maker** tab (`picks.money_maker_parlays`, bot `/moneymaker`) holds the
likeliest parlays (2-3 legs from different games) that pay back at least double the
user's wager if they win, judged at an estimated FanDuel payout (`picks.fanduel_estimate`:
break-even less about 4.5% per leg); parlays whose every leg has won 75%+ of the time
(`hitrate.strong_leg_check`) come first, no Weak legs, no leg in more than two. Every NFL bet shows its track record (`nfl_edge/hitrate.py`): how often that exact bet
would have won in games already played this season and last, and for moneylines the
record against this week's opponent since two seasons back, game by game. Parlay legs
show theirs, with the weakest leg named. A moneyline pick that lost most of its meetings with this opponent since then gets a warning only (`insights.h2h_warning`): tested on 2015-2025, the head-to-head record didn't predict beyond the market, so it never moves the chance. The top singles mix props and game bets (`prop_share=0.5`). The slip (add, view, save, clear, confirm, mark placed) was removed at the user's
request: the app and web page only show bets, and every bet the builder makes is graded
in Bet History. Bets placed on the page earlier stay there (the `placed` db collection).
Chances are learned from results (`nfl_edge/learn.py`): `python -m nfl_edge.learn --write`
replays past seasons to refit `nfl_edge/calibration.json` (kept per bet type only if it
beats the raw model on the newest season; bundled into the builds), and each build also
learns from the ledger's graded picks (`learn.remember`, legs keep `raw_prob`). Refit the
file each off-season.
Accuracy pieces, each tested on past seasons before use: college moneylines are 90%
betting market (ESPN scoreboard lines, `cfb.espn_lines`; history `cfb.history_lines`) and
10% model; passing-yards props adjust for the pass defense (`board.DEFENSE_WEIGHT`; other
prop tweaks tested worse and are off); tiers come from signal weights learned on the replay
(`nfl_edge/signals.py`, `python -m nfl_edge.signals --write` refits `signal_weights.json`,
bundled into the builds; each bet type may only move up or down where that direction held
up on the newest season; untested bet types keep the hand-scored tiers); parlay chances get
a correction learned from graded parlays (`learn.parlay_prob`); injury news for games within
2 hours is re-checked every 5 minutes (`board.late_statuses`); Bet History shows whether NFL
picks beat the closing line (`nfl_edge/clv.py`). Refit calibration first, then signals.
Every payout is shown for a $10 wager: the app has no Singles/Parlays/Legs/Wager boxes
(defaults 30, 10, 2-3, $10), the web page has no Wager box, and the bot's commands have no
wager option (`/place` still records what was really bet).
**UFC** (`--sport ufc`, `nfl_edge/ufc.py`, `board.ufc_board`): every fight's moneyline and
whether it goes the distance, for the next few cards (Contender Series tryouts left out).
Fight history and fighter bios come from ESPN (cached; 2014 on). Chances come from Elo
ratings plus age, reach, experience, layoff and form, fit on 2020-2023 and tested on
2024 on (`python -m nfl_edge.ufc --write` refits `ufc_weights.json`, bundled into the
builds; refit each year). No FanDuel or market UFC prices are free, so the odds are
break-even and the page says to trust FanDuel's price where it disagrees a lot. The bot
has UFC too (`sport:UFC`, the next card).
**MLB** (`--sport mlb`, `nfl_edge/mlb.py`, `board.mlb_board`): each game's moneyline and
run line (always 1.5 on FanDuel, so both are on the default board) and, when Total runs is
picked, DraftKings' total (marked Confirm line). Games, scores, probable starters and
DraftKings lines come from ESPN's scoreboard (cached per day, 2021 on). Tested on this
season's closing lines, the line (cut taken out) beat our model (team Elo + the starter's
runs allowed this season; last season's ERA added nothing, and ESPN's archived ERAs are
season-final, so never use them as of a game) and no mix helped, so a posted line's chance
is used as is and the model only fills in games with no line yet (labeled "No line yet").
`python -m nfl_edge.mlb --write` refits `mlb_weights.json` (bundled into the builds; it also
carries each team's rating at the newest season's start, so a fresh install downloads only
that season). Likewise `ufc_weights.json` carries every active fighter's record (a fight in the last
3 years or booked on a card; `ufc.snapshot`),
so only this year's cards are downloaded; refit both before releases so the snapshots stay
recent. A day or card ESPN can't send is skipped, never fatal. The
Games menu lists days (YYYYMMDD) plus "Next 7 days" (`board.MLB_ALL_DAYS`); series repeat
matchups, so each game's name carries its date. The bot's MLB uses the next 7 days.
The **Parlays** tab uses only the picked league. The **Multi-Lays** tab (`picks.multi_lays`,
`bets.league_pools`, bot `/multilays`, place with `bet:X1`) holds parlays mixing leagues:
2-3 legs from at least two of NFL, college, UFC and MLB (each league's default board:
this week, the next card, the next 7 days), different games, paying at least the Pays $
target on $10, likeliest first; it's the same whichever league is picked. The app builds
it in the background when the tab is opened (about 20 seconds) and keeps it 5 minutes.
They're graded in Bet History as sport "multi": each leg carries its own "sport" and
"season" (`tracker.regrade`), and no week. Parlay chances use their own league's learned
correction (`parlays.league_of`); mixed parlays get none.
**Real odds** (`nfl_edge/lines.py`, needs `ODDS_API_KEY`; The Odds API): every line FanDuel
offers (game lines, NFL/college/MLB player props, UFC fights) priced against every US book
and Pinnacle: the true chance is the other books' prices with their cut removed (power
devig, sharp books weighted; FanDuel left out of its own comparison; junk two-sided prices
dropped; yardage props at other numbers moved to FanDuel's with `SHIFT_SD`, counts only at
the same number; "Yes"-only anytime TDs estimated with `ONE_SIDED_CUT` and marked rough;
lines FanDuel prices far from every book marked check). `Feed` caches pulls under the data
folder and spends credits only on schedule (game lines every `ODDS_MAIN_MINUTES`, props
every `ODDS_PROP_MINUTES`, more often within 3 hours of the game) and never below
`ODDS_MIN_CREDITS`; a failed pull keeps the last good one. The sessions that build this
can't see the key (it's loaded when a session starts), so live pulls run in short helper
sessions; `odds-samples` branch and `tests/fixtures/odds` hold real responses.
**iPhone app and server** (`ios/`, `server/`, branch `ios-app` until launch): `server/app.py`
serves the boards (`web/export.build_result`), Multi-Lays, the record and `/v1/value/{league}`
(the priced lines), checking subscribers with RevenueCat; `ios/` is SwiftUI (XcodeGen
`project.yml`), built and screenshotted on a Mac by `.github/workflows/ios.yml` (screenshots
pushed to the `ios-screenshots` branch); demo data in `ios/BetBuilder/Demo`.
The app and web page are a dark-mode, multi-sport look with one generic backdrop for
every league (no football field). NBA is a visual preview only (`nba_placeholder`; no
NBA data or bets yet). Bet History is dropdowns (report card, what the model learned,
each league by team, each team by week) with a search box and League/Result/Kind/Week/Bet type
filters (`tracker.leg_type_name`) that switch to one flat list of matches. The record
(tally, win bars, report card, closing line, bot history) counts only bets on lines FanDuel
always offers plus anything placed (`tracker.model_line`, `tracker.counted`); model-line
picks show only with "Show model lines", labeled. Learning still uses every graded result.
Parlay legs (app, web, bot slips) show each leg's own model chance. A legend at the top (`App.legend`, `legendHtml` on the page) explains the chances, records, tiers, warnings and columns.
Speed: `data.read_csv` parses each cached file once per version; the app draws each tab
once per build (`App.pane`) and reopens a bet type or week built in the last 5 minutes
(`App.build(quick=True)`); keep these when changing the app.
Parlays have at most 3 legs (`picks.MAX_LEGS`, enforced inside every parlay builder):
graded 4-6 leg parlays won far less than their chance said, and FanDuel's cut comes off
every leg. A prop whose player didn't take the field is a refund ("push"), as on FanDuel,
not a loss (`grade.took_field`, nflverse snap counts; one who played and recorded nothing
loses). Logged picks on players since listed Out, Doubtful or Questionable are dropped
before kickoff (`tracker.withdraw_injured`), so the record only grades picks the board
still showed. College games with no market line are labeled No line yet (model only).
Before trusting a few weeks of live results over the replay, check sample sizes: in 2026
week 5-6 the props and college picks looked overconfident, but the 2024-2025 replay showed
them on target, and one week's results swing widely.
The automatic FanDuel check (reading FanDuel's own site data) was removed at the
user's request because it kept stopping bets from loading; don't add it back.
