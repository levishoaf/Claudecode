# Project notes for Claude

`nfl_edge` finds value bets on FanDuel for the NFL (`--sport nfl`) and
college football (`--sport ncaaf`). See README.md for usage. Saved paper
bets live in `bets/` and are graded with `python -m nfl_edge.grade <file>`.

## The user's standing betting preference

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
