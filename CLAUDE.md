# Project notes for Claude

`nfl_edge` finds value bets on FanDuel for the NFL (`--sport nfl`) and
college football (`--sport ncaaf`). See README.md for usage. Saved paper
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
user's machine; never ask for it here or commit it.

## Making sure bets are on FanDuel

The free board (`nfl_board`, `always_offered=True` by default) only builds
bets with no line that could differ on FanDuel: moneylines and anytime TD
scorers for starters (`SURE_MARKETS`). Guessed lines (spreads, totals, team
totals, "X+" prop ladders) were shown before and the user found many weren't
on FanDuel, so they appear only through the FanDuel check
(`always_offered=False`, then `fdfeed.verify`). Don't add guessed lines back to
the default board ("All bets"). The user asked for every bet when they choose a
bet type, so the Bet type menu's other types (spreads, totals, yardage and other
props) show every model line, FanDuel-confirmed ones at FanDuel's odds and the
rest marked **Confirm line** (`Bet.confirm_line`, `picks.every_line_of`). Receiving yards bets are only listed at -300 or better (`picks.TYPE_MIN_ODDS`):
each player's likeliest line within that limit. The **Money Maker** tab (`picks.money_maker_parlays`, bot `/moneymaker`) holds riskier
parlays the user asked for: 2-3 strong legs plus one coin-flip "make or break" leg
the regular parlays don't use, about 20% to win for about 5x the wager. The top singles mix props and game bets (`prop_share=0.5`). In the app and on the web page each slip
bet must be confirmed **On FanDuel** before it can be marked placed
(`ledger["checks"]` in the app, the `checks` db collection on the page).
`nfl_edge/fdfeed.py` checks bets against FanDuel's own site data
(unofficial, no key; app "FanDuel check", bot `/fanduel-check`). It is on by
default for Indiana (`bets.DEFAULT_STATE`; "off" in the state file turns it
off), and falls back to the sure board with a note when FanDuel can't be read. It is
blocked from this workspace, so it is tested only against sample feeds.
