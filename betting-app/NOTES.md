# Working notes

## 2026-10-10 round 3 (second book, snapshots, visual QA)
- Probed 11 keyless hosts (`probe_sources.py`, results in data/source_probe.json). Only ESPN site hosts reachable; they carry DraftKings only. No second book found. Blocked hosts recorded, not circumvented. Live run still PASS.
- Added `app/snapshots.py`, `snapshot.py`, snapshot append in `refresh.py`, own-snapshot CLV in `grade.py` (ESPN closing odds as fallback). 44 tests pass.
- UI: forced theme via `?theme=dark|light`, server ignores query strings. Verified 390px and 1280px, light and dark (headless_shell at /opt/pw-browsers/chromium_headless_shell-1194 honors small window sizes). No layout/contrast bugs found.
- Open: need a second reachable book for real edges; FanDuel parser still unverified against real data.

## 2026-10-10 item 1: parser hardening
- Per-event try/except in the ESPN, FanDuel and ratings parsers; top-level shape checks; `valid_event` gate in `merge_events`; outcome validation in the engine; providers that raise are recorded as failed sources instead of crashing `engine.run`; corrupt cache/ledger/snapshot lines are skipped.
- `tests/test_robustness.py` (junk inputs for every parser, 400 random mutations each of valid ESPN and FanDuel payloads, raising/garbage providers, garbled files, pipeline fallback). A 40-seed x 150 fuzz sweep found 0 failures. 55 tests pass.

## 2026-10-10 item 2: more markets
- Inspected every key in ESPN scoreboard odds, summary pickcenter and header odds for NFL and NCAAF: only moneyline, spread, total (one book, DraftKings). No alt/1H/team-total/prop data exists in reachable sources, so nothing added (no synthesis).
- Found ESPN "Matchup Predictor" (FPI-style win %) in summary for upcoming games; added as display-only `fpi_prob` (`--no-fpi` to skip) and stored in ledger entries. 57 tests pass.

## 2026-10-10 item 3: model improvements (walk-forward validated)
- Added optional rest-day adjustment and unseen-team start rating to the Elo (both default off). `experiments.py` ran 24 single-parameter variants with a strict accept rule (log loss better on both seasons, blend-vs-market Brier not worse): all rejected. Baseline unchanged. Injuries not testable (no history). 60 tests pass.

## 2026-10-10 item 4: parlay quality
- `enumerate_parlays` does exact DFS enumeration (one leg per game, <=2 same-direction totals legs, min combined prob) and keeps the top 400 by EV; `build_parlays` uses qualifying legs first and tops up with labelled watchlist parlays. Payload has `parlay_search` stats. Parlays are BET only if EV>0 and all legs qualify.
- Tests: exact combination counts, same-game exclusion, totals cap, vig compounding vs singles, 0.5% stake cap, qualifying-first behaviour. 64 tests pass. Not modelled: conference/weather correlation (no keyless data).

## 2026-10-10 item 5: UX
- Added: per-pick detail drawer (click/Enter on a row; sources, prices seen, market/ratings/ESPN-predictor/blend probabilities, Kelly, flags), "How to read this page" panel, sortable/filterable parlay cards (sort, legs, bets-only), tighter singles table that fits 1280px without sideways scroll (scrolls inside its box on narrow screens).
- Found and fixed a real CSS bug: a stray `}` after the forced-dark block was dropping the following `*{box-sizing:border-box}` rule (drawer overflowed the viewport at 390px). Server now reads index.html per request. QA done at 390px and 1280px, light and dark, with `?theme=` and `?open=N` hooks (headless_shell at /opt/pw-browsers/chromium_headless_shell-1194 honors small windows; take screenshots with --virtual-time-budget=8000). 64 tests pass.

## 2026-10-10 item 6: docs
- README: run list (experiments, snapshot, probe), flags, using the page, step-by-step FanDuel debug-dump instructions, layout. All six queue items done; 64 tests pass; live refresh = PASS (one book), sample refresh and static build OK.
