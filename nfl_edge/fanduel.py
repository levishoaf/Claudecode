"""FanDuel's real board: only bets FanDuel lists, at FanDuel's prices.

Needs a key from The Odds API (https://the-odds-api.com). One check of an NFL
week costs about 6 credits for the main lines plus roughly 15 per game for
alternate lines and player props (about 200 for a full Sunday slate), so
results are kept for CHECK_MINUTES and auto-refresh reuses them.

Each bet's chance comes from the same models as the free board: the no-vig
price at sharp books (Pinnacle) for game lines, past results near the line
for alternate lines, and game logs anchored to the market for props.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from . import board, cfb, data
from .api import SPORT_KEYS, OddsAPIError, fetch_event_odds, fetch_odds
from .context import name_key
from .extras import (DEFAULT_NCAAF_MARKETS, DEFAULT_NFL_MARKETS, LineDistribution,
                     load_player_model, price_event)
from .finder import Bet, find_bets
from .stats import TEAM_ABBR

MAIN_MARKETS = ["h2h", "spreads", "totals"]
CHECK_MINUTES = 30
_cache: dict[tuple, tuple[float, list[Bet], str]] = {}

__all__ = ["OddsAPIError", "fanduel_board", "CHECK_MINUTES", "SPORT_KEYS"]


def _week_games(sport: str, week: int | None, now: datetime, games_source=None):
    """(week, schedule rows for that week's games still to be played)."""
    if sport == "ncaaf":
        season = board.season_for(now.date())
        rows = [r for r in cfb.load_schedule(season, current_season=season)
                if r["completed"] != "TRUE"
                and datetime.fromisoformat(r["start_date"].replace("Z", "+00:00")) > now]
        if week is None and rows:
            week = min(int(r["week"]) for r in rows)
        rows = [r for r in rows if int(r["week"]) == week]
        return week, rows
    rows = board.upcoming_nfl(now, games_source)
    if week is None and rows:
        week = min(int(g["week"]) for g in rows)
    return week, [g for g in rows if int(g["week"]) == week]


def _match(sport: str, event: dict, rows: list[dict]) -> dict | None:
    """The schedule row for an Odds API event, or None if it's another week's game."""
    if sport == "ncaaf":
        schools = sorted({r["home_team"] for r in rows} | {r["away_team"] for r in rows})
        home = cfb.match_team(event["home_team"], schools)
        away = cfb.match_team(event["away_team"], schools)
        return next((r for r in rows if r["home_team"] == home and r["away_team"] == away), None)
    home, away = TEAM_ABBR.get(event["home_team"]), TEAM_ABBR.get(event["away_team"])
    return next((g for g in rows if g["home_team"] == home and g["away_team"] == away), None)


def injury_statuses(week: int) -> dict[str, str]:
    """{player name key: Out/Doubtful/Questionable} for this week (ESPN, else nflverse)."""
    out = {key: info["status"] for (_, key), info in board.espn_official().items()}
    season = board.season_for(datetime.now(board.EASTERN).date())
    for r in data.release("injuries", season, season, max_age=board.INJURY_MAX_AGE):
        if r.get("week") == str(week) and r.get("report_status") in board.GAME_STATUSES:
            out.setdefault(name_key(r["full_name"]), r["report_status"])
    return out


def fanduel_board(api_key: str, sport: str = "nfl", lo: float = 0.0, hi: float = 1.0,
                  week: int | None = None, props: bool = True, now: datetime | None = None,
                  games_source: str | None = None, fetch=fetch_odds,
                  fetch_event=fetch_event_odds) -> tuple[list[Bet], str]:
    """(FanDuel bets with a lo-hi chance, a note on credits and freshness).

    Raises OddsAPIError if the API can't be reached or the key is wrong."""
    now = now or datetime.now(board.EASTERN)
    key = (sport, week, props, round(lo, 3), round(hi, 3))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CHECK_MINUTES * 60:
        return hit[1], hit[2]

    week, rows = _week_games(sport, week, now, games_source)
    events, remaining = fetch(api_key, MAIN_MARKETS, ["us", "eu"], sport)
    matched = []
    for ev in events:
        row = _match(sport, ev, rows)
        if row is not None:
            matched.append((ev, row))
    if not matched:
        return [], f"FanDuel has no lines yet for week {week}."

    season = board.season_for(now.date())
    if sport == "ncaaf":
        model = cfb.CfbModel(cfb.load_schedule(season, current_season=season))
    else:
        from .cli import build_model  # heavy import, only when needed
        try:
            model = build_model(season, games_source, None, weather=True)
        except (data.DataError, ValueError):
            model = None
    bets = find_bets([ev for ev, _ in matched], min_ev=-1.0, min_books=1, model=model,
                     model_weight=0.1, now=now.astimezone(timezone.utc), target_book="fanduel")
    ids = {f"{ev['away_team']} @ {ev['home_team']}": (row["game_id"], int(row["week"]))
           for ev, row in matched}

    if props:
        games = data.games(games_source) if sport == "nfl" else None
        lines = LineDistribution(sport, games)
        players = load_player_model(season, games) if sport == "nfl" else None
        markets = DEFAULT_NFL_MARKETS if sport == "nfl" else DEFAULT_NCAAF_MARKETS
        status = injury_statuses(week) if sport == "nfl" else {}
        for ev, _ in matched:
            if datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) <= now:
                continue  # already started
            extra, left = fetch_event(api_key, ev["id"], markets, sport)
            remaining = left or remaining
            for b in price_event(ev, extra, sport, lines, players):
                if b.market.startswith("player_"):
                    injury = status.get(name_key(b.pick.rsplit(" ", 1)[0]), "")
                    if injury in ("Out", "Doubtful"):
                        continue
                    b.note = "Questionable" if injury == "Questionable" else ""
                bets.append(b)

    for b in bets:
        b.game_id, b.week = ids.get(b.game, (None, None))
        b.priced = True
    bets = [b for b in bets if b.game_id and lo <= b.fair_prob <= hi]
    stamp = datetime.now(board.EASTERN)
    note = (f"FanDuel lines checked {stamp.hour % 12 or 12}:{stamp:%M %p} ET"
            + (f" · {remaining} API credits left" if remaining else ""))
    _cache[key] = (time.monotonic(), bets, note)
    return bets, note
