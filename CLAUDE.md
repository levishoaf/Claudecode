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

## Web version

The bet builder also runs as a private web page:
https://claude.ai/artifact/QyRUWeX7NkXuVUSn4c3xsv

**Every time the application changes, update the web page too.** Rebuild it
with fresh data and republish to the same URL; carry any visible app change
into `web/template.html` first. End each reply with the link to the web page.

To rebuild: read the page's placed bets first (ArtifactData `list` of the
`placed` collection on the page's URL, saved as a JSON list), then run
`python3 web/export.py <scratchpad>/bet-builder.html --placed <that file>`.
This grades placed bets and logs this week's picks in `web/history.json`
(commit and push it) for the Not placed tab. The page declares the `db`
capability, so omit `capabilities` when republishing to keep it.

The app keeps its own placed and not-placed bets in
`~/Bet Builder/my bets.json` and grades them on every refresh.

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
markets FanDuel posts for every game: moneylines, main spreads and totals,
main team totals and anytime TDs, plus "X+" prop ladders in FanDuel's steps
for players with a clear starting role (`FEATURED_MIN_AVERAGE`). The top
singles are a mix: props fill at most half (`prop_share=0.5`) while game bets
are available. In the app and on the web page each slip
bet must be confirmed **On FanDuel** before it can be marked placed
(`ledger["checks"]` in the app, the `checks` db collection on the page).
`nfl_edge/fdfeed.py` optionally checks bets against FanDuel's own site data
(unofficial, no key; app "FanDuel check", bot `/fanduel-check`); it is
blocked from this workspace, so it is tested only against sample feeds.
