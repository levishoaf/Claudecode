"""The free board: candidate bets built from public data, no API key.

- NFL game lines use nflverse's consensus moneylines, spreads and totals.
- Alternate spreads, alternate totals and team totals are priced from
  those lines and real NFL results (see extras.LineDistribution).
- Player props come from game logs, scaled to how many points the market
  expects each team to score, using FanDuel-style "X+" ladders.
- College uses the college model's moneylines.

None of these are FanDuel's prices. Each bet carries its win chance and
break-even odds, so FanDuel's price can be checked against it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import cfb, data
from .context import name_key
from .extras import LineDistribution, load_player_model, p_over
from .finder import Bet, find_bets
from .odds import american_to_decimal, decimal_to_american, kelly_fraction
from .stats import TEAM_ABBR, normal_cdf

try:
    from zoneinfo import ZoneInfo

    EASTERN = ZoneInfo("America/New_York")
except Exception:  # no time-zone database
    EASTERN = timezone(timedelta(hours=-4), "ET")

ABBR_NAME = {v: k for k, v in TEAM_ABBR.items()}
# Status lines from the last board built (e.g. whether injury reports are in).
NOTES: dict[str, str] = {}
HALF_POINTS = [k + 0.5 for k in range(0, 60)]

# position -> [(stat column, market, distribution, thresholds)]
PROP_LADDERS = {
    "QB": [("passing_yards", "player_pass_yds_alternate", "normal", range(150, 351, 25)),
           ("passing_tds", "player_pass_tds_alternate", "poisson", range(1, 4))],
    "RB": [("rushing_yards", "player_rush_yds_alternate", "normal", range(20, 131, 10)),
           ("receptions", "player_receptions_alternate", "count", range(2, 7)),
           ("rushing_tds+receiving_tds", "player_anytime_td", "poisson", [1])],
    "WR": [("receptions", "player_receptions_alternate", "count", range(2, 11)),
           ("receiving_yards", "player_reception_yds_alternate", "normal", range(20, 151, 10)),
           ("rushing_tds+receiving_tds", "player_anytime_td", "poisson", [1])],
}
PROP_LADDERS["TE"] = PROP_LADDERS["WR"]
# How much a team's expected points move each stat (yards and catches less than TDs).
SCALING = {"normal": 0.5, "count": 0.3, "poisson": 1.0}


def make_bet(game: str, kickoff: datetime, market: str, pick: str, point: float | None,
             prob: float, game_id: str, source: str, price: int | None = None,
             week: int | None = None) -> Bet:
    """A bet priced at `price`, or at its break-even odds when there's no price."""
    priced = price is not None
    if price is None:
        price = decimal_to_american(1 / min(max(prob, 0.01), 0.99))
    ev = prob * american_to_decimal(price) - 1
    return Bet(game, kickoff, market, pick, point, price, prob, prob, None, ev,
               kelly_fraction(prob, price), [source], game_id, priced, week)


def kickoff_of(row: dict) -> datetime:
    when = f"{row['gameday']}T{row['gametime'] or '13:00'}"
    return datetime.fromisoformat(when).replace(tzinfo=EASTERN)


def season_for(day: date) -> int:
    return day.year if day.month >= 3 else day.year - 1


def _pick_line(prob_at, lines, lo: float, hi: float) -> tuple[float, float] | None:
    """The line whose chance is highest without going over `hi` (and >= lo)."""
    best = None
    for line in lines:
        p = prob_at(line)
        if lo <= p <= hi and (best is None or p > best[1]):
            best = (line, p)
    return best


def has_lines(g: dict) -> bool:
    return bool(g["spread_line"] and g["total_line"] and g["home_moneyline"] and g["away_moneyline"])


def upcoming_nfl(now: datetime | None = None, games_source: str | None = None) -> list[dict]:
    now = now or datetime.now(EASTERN)
    season = season_for(now.date())
    return [g for g in data.games(games_source) if g["season"] == str(season)
            and g["game_type"] == "REG" and g["home_score"] == "" and kickoff_of(g) > now]


def nfl_weeks(now: datetime | None = None, games_source: str | None = None) -> list[tuple[int, bool]]:
    """[(week, lines posted?)] for this week and every later week on the schedule."""
    weeks: dict[int, bool] = {}
    for g in upcoming_nfl(now, games_source):
        w = int(g["week"])
        weeks[w] = weeks.get(w, True) and has_lines(g)
    return sorted(weeks.items())


def cfb_weeks(now: datetime | None = None, ahead: int = 4) -> list[int]:
    now = now or datetime.now(EASTERN)
    season = season_for(now.date())
    weeks = sorted({int(r["week"]) for r in cfb.load_schedule(season, current_season=season)
                    if r["completed"] != "TRUE" and r["season_type"] == "regular"
                    and datetime.fromisoformat(r["start_date"].replace("Z", "+00:00")) > now})
    return weeks[:ahead]


def nfl_board(day: date | None, lo: float, hi: float, now: datetime | None = None,
              games_source: str | None = None, week: int | None = None) -> list[Bet]:
    from .cli import build_model  # heavy import, only when needed

    now = now or datetime.now(EASTERN)
    all_games = data.games(games_source)
    season = season_for(now.date())
    upcoming = [g for g in upcoming_nfl(now, games_source)
                if day is None or kickoff_of(g).date() == day]
    if week is None and day is None and upcoming:  # default: this week's games only
        week = min(int(g["week"]) for g in upcoming)
    if week is not None:
        upcoming = [g for g in upcoming if int(g["week"]) == week]
    rows = [g for g in upcoming if has_lines(g)]
    if upcoming and not rows:  # schedule known, lines not out yet
        return _model_only_week(upcoming, season, lo, hi, games_source)
    if not rows:
        return []

    # 1) Game lines at consensus prices, blended 90/10 with the stats model.
    events = []
    for g in rows:
        home, away = ABBR_NAME[g["home_team"]], ABBR_NAME[g["away_team"]]
        sp = float(g["spread_line"])  # positive = home favored
        markets = [
            {"key": "h2h", "outcomes": [{"name": away, "price": int(g["away_moneyline"])},
                                        {"name": home, "price": int(g["home_moneyline"])}]},
            {"key": "spreads", "outcomes": [
                {"name": away, "price": int(g["away_spread_odds"] or -110), "point": sp},
                {"name": home, "price": int(g["home_spread_odds"] or -110), "point": -sp}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": int(g["over_odds"] or -110), "point": float(g["total_line"])},
                {"name": "Under", "price": int(g["under_odds"] or -110), "point": float(g["total_line"])}]},
        ]
        events.append({"id": g["game_id"], "home_team": home, "away_team": away,
                       "commence_time": kickoff_of(g).astimezone(timezone.utc).isoformat(),
                       "bookmakers": [{"key": "consensus", "markets": markets},
                                      {"key": "consensus_ref", "markets": markets}]})
    try:
        model = build_model(season, games_source, None, weather=True)
    except (data.DataError, ValueError):
        model = None
    bets = find_bets(events, min_ev=-1.0, min_books=1, model=model, model_weight=0.1,
                     now=now.astimezone(timezone.utc), target_book="consensus")
    ids = {f"{e['away_team']} @ {e['home_team']}": e["id"] for e in events}
    for b in bets:
        b.game_id, b.books = ids[b.game], ["consensus line"]

    # 2) Alternate spreads, totals and team totals from the lines.
    dist = LineDistribution("nfl", all_games)
    for g in rows:
        home, away = ABBR_NAME[g["home_team"]], ABBR_NAME[g["away_team"]]
        game, kick, gid = f"{away} @ {home}", kickoff_of(g), g["game_id"]
        sp, tot = float(g["spread_line"]), float(g["total_line"])

        def home_by_more(x):  # P(home margin > x)
            return dist.outcome("margin", sp, x)[0]

        for team, sign in ((home, 1), (away, -1)):
            # team +L (or -L): covers if its margin + L > 0
            def cover(L, sign=sign):
                return home_by_more(-L) if sign == 1 else 1 - home_by_more(L) - dist.outcome("margin", sp, L)[1]
            for lines in ([L for L in HALF_POINTS], [-L for L in HALF_POINTS]):
                best = _pick_line(cover, lines, lo, hi)
                if best:
                    bets.append(make_bet(game, kick, "alternate_spreads", team, best[0], best[1],
                                         gid, "market line"))
        over = lambda L: dist.outcome("total", tot, L)[0]  # noqa: E731
        for side, prob_at in (("Over", over), ("Under", lambda L: 1 - over(L))):
            best = _pick_line(prob_at, [L + 20 for L in HALF_POINTS], lo, hi)
            if best:
                bets.append(make_bet(game, kick, "alternate_totals", side, best[0], best[1],
                                     gid, "market line"))
        for team, pts in ((home, tot / 2 + sp / 2), (away, tot / 2 - sp / 2)):
            best = _pick_line(lambda L, pts=pts: 1 - normal_cdf((L - pts) / 9.1),
                              HALF_POINTS, lo, hi)
            if best:
                bets.append(make_bet(game, kick, "team_totals", f"{team} Over", best[0], best[1],
                                     gid, "market line"))

    # 3) Player props.
    bets += _props(rows, all_games, season, lo, hi)
    weeks = {g["game_id"]: int(g["week"]) for g in rows}
    for b in bets:
        b.week = weeks.get(b.game_id)
    return [b for b in bets if lo <= b.fair_prob <= hi]


def _model_only_week(rows, season, lo, hi, games_source) -> list[Bet]:
    """Moneylines from the stats model for a week whose lines aren't posted."""
    from .cli import build_model
    from .stats import probabilities_from_points

    week = int(rows[0]["week"])
    NOTES["injuries"] = (f"Week {week} lines aren't posted yet: moneylines from the stats model "
                         "only. Props, alternate lines and injury reports come later.")
    model = build_model(season, games_source, None, weather=False)
    out = []
    for g in rows:
        home, away = ABBR_NAME[g["home_team"]], ABBR_NAME[g["away_team"]]
        event = {"id": g["game_id"], "home_team": home, "away_team": away,
                 "commence_time": kickoff_of(g).astimezone(timezone.utc).isoformat()}
        p = model.predict(event)
        if p is None:
            continue
        probs = probabilities_from_points(home, p.home_pts, p.away_pts, "h2h",
                                          [{"name": home}, {"name": away}])
        for team, prob in probs.items():
            if lo <= prob <= hi:
                out.append(make_bet(f"{away} @ {home}", kickoff_of(g), "h2h", team, None, prob,
                                    g["game_id"], "stats model (no lines yet)", week=week))
    return out


def _props(rows, all_games, season, lo, hi) -> list[Bet]:
    players = load_player_model(season, all_games)
    implied, info = {}, {}
    for g in rows:
        tot, sp = float(g["total_line"]), float(g["spread_line"])
        implied[g["home_team"]], implied[g["away_team"]] = tot / 2 + sp / 2, tot / 2 - sp / 2
        game = f"{ABBR_NAME[g['away_team']]} @ {ABBR_NAME[g['home_team']]}"
        info[g["home_team"]] = info[g["away_team"]] = (game, kickoff_of(g), g["game_id"])

    # Team scoring average (this season counts double) and each team's latest game.
    pts, latest = {}, {}
    for g in all_games:
        if g["game_type"] != "REG" or not g["home_score"] or int(g["season"]) < season - 1:
            continue
        w = 2.0 if g["season"] == str(season) else 1.0
        for side in ("home", "away"):
            t = g[f"{side}_team"]
            tot_w = pts.setdefault(t, [0.0, 0.0])
            tot_w[0] += w * int(g[f"{side}_score"])
            tot_w[1] += w
            if g["season"] == str(season):
                latest[t] = max(latest.get(t, 0), int(g["week"]))

    # This week's injury report: Out/Doubtful players are dropped, Questionable flagged.
    week = int(rows[0]["week"])
    status = {}
    for i in data.release("injuries", season, season):
        if i.get("game_type") == "REG" and i.get("week") == str(week) and i.get("report_status"):
            status[(i["team"], name_key(i["full_name"]))] = i["report_status"]
    playing = set(implied)
    reported = {t for t, _ in status} & playing
    current = min(int(g["week"]) for g in upcoming_nfl()) if upcoming_nfl() else week
    if week > current and not reported:
        NOTES["injuries"] = (f"Week {week} injury reports come out the week of the games; "
                             "these picks will update then")
    elif reported == playing:
        NOTES["injuries"] = f"Week {week} injury reports included for all {len(playing)} teams"
    elif reported:
        NOTES["injuries"] = (f"Week {week} injury reports in for {len(reported)} of {len(playing)} "
                             "teams; refresh after they post (usually Friday afternoon)")
    else:
        NOTES["injuries"] = (f"Week {week} injury reports not posted yet "
                             "(usually Wednesday to Friday); refresh later")

    out = []
    for key, team in players.current_team.items():
        if team not in implied or team not in pts:
            continue
        injury = status.get((team, key), "")
        if injury in ("Out", "Doubtful"):
            continue
        apps = players.appearances.get((key, season), set())
        if (team, str(latest.get(team, 0))) not in apps:
            continue  # didn't play his team's latest game
        factor = implied[team] / (pts[team][0] / pts[team][1])
        name = players.names[key]
        best = None
        for stat, market, dist, ladder in PROP_LADDERS.get(players.position.get(key, ""), []):
            prof = players.profile(name, stat)
            if prof is None:
                continue
            mean = prof.mean * factor ** SCALING[dist]
            for k in ladder:
                p = p_over(dist, mean, prof.sd, k - 0.5)
                p = (p * prof.games + 1) / (prof.games + 2)  # small-sample shrink
                if lo <= p <= hi and (best is None or p > best[0]):
                    best = (p, market, k)
        if best:
            p, market, k = best
            game, kick, gid = info[team]
            pick = f"{name} Yes" if market == "player_anytime_td" else f"{name} Over"
            bet = make_bet(game, kick, market, pick, k - 0.5, p, gid, "player logs")
            bet.note = "Questionable" if injury == "Questionable" else ""
            out.append(bet)
    return out


def cfb_board(day: date | None, lo: float, hi: float, now: datetime | None = None,
              week: int | None = None) -> list[Bet]:
    now = now or datetime.now(EASTERN)
    season = season_for(now.date())
    rows = cfb.load_schedule(season, current_season=season)
    model = cfb.CfbModel(rows)

    def kick_of(r):
        return datetime.fromisoformat(r["start_date"].replace("Z", "+00:00")).astimezone(EASTERN)

    upcoming = [r for r in rows if r["completed"] != "TRUE" and kick_of(r) > now
                and (day is None or kick_of(r).date() == day)]
    if week is None and day is None and upcoming:  # default: this week's games only
        week = min(int(r["week"]) for r in upcoming)
    if week is not None:
        upcoming = [r for r in upcoming if int(r["week"]) == week]
    out = []
    for r in upcoming:
        kick = kick_of(r)
        hp, ap, _ = model.predict_row(r)
        p_home = normal_cdf((hp - ap) / cfb.MARGIN_SD)
        game = f"{r['away_team']} @ {r['home_team']}"
        for team, p in ((r["home_team"], p_home), (r["away_team"], 1 - p_home)):
            if lo <= p <= hi:
                out.append(make_bet(game, kick, "h2h", team, None, p, r["game_id"], "college model",
                                    week=int(r["week"])))
    return out
