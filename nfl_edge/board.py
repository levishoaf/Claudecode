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

import math

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

# position -> [(stat column, market, distribution, thresholds)]. The steps follow
# FanDuel's "X+" ladders: yards in 25s, receptions from 3+, passing TDs 1+ to 3+.
PROP_LADDERS = {
    "QB": [("passing_yards", "player_pass_yds_alternate", "normal", range(175, 351, 25)),
           ("passing_tds", "player_pass_tds_alternate", "poisson", range(1, 4))],
    "RB": [("rushing_yards", "player_rush_yds_alternate", "normal", range(25, 151, 25)),
           ("receptions", "player_receptions_alternate", "count", range(3, 7)),
           ("rushing_tds+receiving_tds", "player_anytime_td", "poisson", [1])],
    "WR": [("receptions", "player_receptions_alternate", "count", range(3, 11)),
           ("receiving_yards", "player_reception_yds_alternate", "normal", range(25, 151, 25)),
           ("rushing_tds+receiving_tds", "player_anytime_td", "poisson", [1])],
}
# FanDuel only lists props for players with a real role: a stat's ladder is
# offered only if the player averages at least this much of it.
PROP_MIN_AVERAGE = {"passing_yards": 150, "passing_tds": 0.8, "rushing_yards": 25,
                    "receptions": 2.5, "receiving_yards": 25, "rushing_tds+receiving_tds": 0.15}
# Markets the free board can be sure FanDuel lists: no line that could differ.
SURE_MARKETS = {"h2h", "player_anytime_td"}
# FanDuel posts "X+" prop ladders for players with a clear starting role.
# With always_offered, a ladder is used only if the player averages this much.
FEATURED_MIN_AVERAGE = {"passing_yards": 190, "passing_tds": 1.0, "rushing_yards": 45,
                        "receptions": 3.5, "receiving_yards": 40,
                        "rushing_tds+receiving_tds": 0.15}
# How far from the main line FanDuel's alternate lines usually go.
ALT_SPREAD_RANGE = 10
ALT_TOTAL_RANGE = 10
TEAM_TOTAL_RANGE = 4
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


def _lines(prob_at, lines, lo: float, hi: float, every: bool) -> list[tuple[float, float]]:
    """The best line in [lo, hi], or with `every` all of them (for looking a bet up)."""
    if every:
        return [(line, p) for line in lines if lo <= (p := prob_at(line)) <= hi]
    best = _pick_line(prob_at, lines, lo, hi)
    return [best] if best else []


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
              games_source: str | None = None, week: int | None = None,
              every_line: bool = False, always_offered: bool = True) -> list[Bet]:
    """Candidate bets for the week. Alternate lines are the best one per side, or
    with `every_line` every line in [lo, hi] (for looking up a specific bet).

    With `always_offered` (the default), only bets that have no line to get
    wrong and that FanDuel posts for every game: moneylines and anytime
    touchdown scorers (starters only). Spreads, totals, team totals and "X+"
    props need FanDuel's own line, so they come only from the FanDuel check
    (`always_offered=False`, then nfl_edge.fdfeed.verify)."""
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
    for g in ([] if always_offered else rows):
        home, away = ABBR_NAME[g["home_team"]], ABBR_NAME[g["away_team"]]
        game, kick, gid = f"{away} @ {home}", kickoff_of(g), g["game_id"]
        sp, tot = float(g["spread_line"]), float(g["total_line"])

        def home_by_more(x):  # P(home margin > x)
            return dist.outcome("margin", sp, x)[0]

        for team, sign in ((home, 1), (away, -1)):
            # team +L (or -L): covers if its margin + L > 0
            def cover(L, sign=sign):
                return home_by_more(-L) if sign == 1 else 1 - home_by_more(L) - dist.outcome("margin", sp, L)[1]
            main = -sp if sign == 1 else sp  # the team's main spread
            near = [L for L in HALF_POINTS + [-L for L in HALF_POINTS]
                    if abs(L - main) <= ALT_SPREAD_RANGE]
            for lines in ([L for L in near if L > 0], [L for L in near if L < 0]):
                for line, p in _lines(cover, lines, lo, hi, every_line):
                    bets.append(make_bet(game, kick, "alternate_spreads", team, line, p,
                                         gid, "market line"))
        over = lambda L: dist.outcome("total", tot, L)[0]  # noqa: E731
        for side, prob_at in (("Over", over), ("Under", lambda L: 1 - over(L))):
            near = [L + 20 for L in HALF_POINTS if abs(L + 20 - tot) <= ALT_TOTAL_RANGE]
            for line, p in _lines(prob_at, near, lo, hi, every_line):
                bets.append(make_bet(game, kick, "alternate_totals", side, line, p,
                                     gid, "market line"))
        for team, pts in ((home, tot / 2 + sp / 2), (away, tot / 2 - sp / 2)):
            near = [L for L in HALF_POINTS if abs(L - pts) <= TEAM_TOTAL_RANGE]
            for line, p in _lines(lambda L, pts=pts: 1 - normal_cdf((L - pts) / 9.1),
                                  near, lo, hi, every_line):
                bets.append(make_bet(game, kick, "team_totals", f"{team} Over", line, p,
                                     gid, "market line"))

    # 3) Player props.
    bets += _props(rows, all_games, season, lo, hi, every_line,
                   only={"player_anytime_td"} if always_offered else None, featured=always_offered)
    weeks = {g["game_id"]: int(g["week"]) for g in rows}
    for b in bets:
        b.week = weeks.get(b.game_id)
    if always_offered:  # only bets with no line that could differ on FanDuel
        bets = [b for b in bets if b.market in SURE_MARKETS]
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


def _props(rows, all_games, season, lo, hi, every_line: bool = False,
           only: set[str] | None = None, featured: bool = False) -> list[Bet]:
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

    # This week's injury report: anyone listed (Out, Doubtful or Questionable), or who
    # missed practice before statuses post, is left out so no bet rides on him.
    week = int(rows[0]["week"])
    status, missed_practice = {}, set()
    for (team_code, player_key), report in espn_official().items():
        status[(team_code, player_key)] = report["status"]
    espn_teams = {t for t, _ in status}
    for i in data.release("injuries", season, season, max_age=INJURY_MAX_AGE):
        if i.get("game_type") != "REG" or i.get("week") != str(week):
            continue
        if i["team"] in espn_teams:
            pass  # ESPN's official report is newer for this team
        elif i.get("report_status"):
            status[(i["team"], name_key(i["full_name"]))] = i["report_status"]
        elif (i.get("practice_status") or "").startswith("Did Not"):
            missed_practice.add((i["team"], name_key(i["full_name"])))
    playing = set(implied)
    reported = ({t for t, _ in status} | espn_teams) & playing
    current = min(int(g["week"]) for g in upcoming_nfl()) if upcoming_nfl() else week
    if week > current and not reported:
        NOTES["injuries"] = (f"Week {week} injury reports come out the week of the games; "
                             "these picks will update then")
    elif reported == playing:
        NOTES["injuries"] = f"Week {week} injury reports included for all {len(playing)} teams"
    elif reported:
        NOTES["injuries"] = (f"Week {week} injury reports in for {len(reported)} of {len(playing)} "
                             "teams; refresh after they post (usually Friday afternoon)")
    elif missed_practice:
        NOTES["injuries"] = (f"Week {week} game statuses aren't out yet (usually Friday "
                             "afternoon); players who missed practice are left out")
    else:
        NOTES["injuries"] = (f"Week {week} injury reports not posted yet "
                             "(usually Wednesday to Friday); refresh later")

    out = []
    for key, team in players.current_team.items():
        if team not in implied or team not in pts:
            continue
        if status.get((team, key)) or (team, key) in missed_practice:
            continue  # injured or questionable: he may not play
        apps = players.appearances.get((key, season), set())
        if (team, str(latest.get(team, 0))) not in apps:
            continue  # didn't play his team's latest game
        factor = implied[team] / (pts[team][0] / pts[team][1])
        name = players.names[key]
        found = []  # (chance, market, threshold)
        for stat, market, dist, ladder in PROP_LADDERS.get(players.position.get(key, ""), []):
            if only is not None and market not in only:
                continue
            prof = players.profile(name, stat)
            minimum = (FEATURED_MIN_AVERAGE if featured else PROP_MIN_AVERAGE).get(stat, 0)
            if prof is None or prof.mean < minimum:
                continue  # FanDuel wouldn't list this prop for him
            mean = prof.mean * factor ** SCALING[dist]
            for k in ladder:
                p = p_over(dist, mean, prof.sd, k - 0.5)
                p = (p * prof.games + 1) / (prof.games + 2)  # small-sample shrink
                if lo <= p <= hi:
                    found.append((p, market, k))
        if not every_line:  # the player's likeliest line of each prop type
            best = {}
            for f in sorted(found, key=lambda f: -f[0]):
                best.setdefault(f[1], f)
            found = list(best.values())
        for p, market, k in found:
            game, kick, gid = info[team]
            pick = f"{name} Yes" if market == "player_anytime_td" else f"{name} Over"
            bet = make_bet(game, kick, market, pick, k - 0.5, p, gid, "player logs")
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


# ---------------------------------------------------------------- injury report

SEVERITY = ["Out", "Doubtful", "Questionable", "Did not practice", "Limited"]
GAME_STATUSES = ("Out", "Doubtful", "Questionable")
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
# Same report on ESPN's other API host, tried if the first can't be reached.
ESPN_INJURIES_ALT = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
INJURY_MAX_AGE = 30 * 60  # injury news moves fast; refresh every 30 minutes


def espn_official(payload: object | None = None) -> dict[tuple[str, str], dict]:
    """Official game statuses from ESPN's live NFL injury report, which posts
    within minutes of the NFL's Friday release. {(team, player key): info}.
    Returns {} if ESPN can't be reached or its format isn't recognised."""
    if payload is None:
        for url in (ESPN_INJURIES, ESPN_INJURIES_ALT):
            try:
                payload = data.read_json(url, "espn_nfl_injuries.json", INJURY_MAX_AGE)
                break
            except (data.DataError, ValueError):
                continue
        else:
            return {}
    out = {}
    try:
        for team in payload.get("injuries", []):
            abbr = TEAM_ABBR.get(team.get("displayName", ""))
            if not abbr:
                continue
            for item in team.get("injuries", []):
                status = (item.get("status") or "").strip().title()
                if status not in GAME_STATUSES:
                    continue  # skips Injured Reserve, PUP, etc. (not on the game report)
                athlete = item.get("athlete") or {}
                name = athlete.get("displayName") or ""
                details = item.get("details") or {}
                out[(abbr, name_key(name))] = {
                    "player": name,
                    "position": (athlete.get("position") or {}).get("abbreviation", ""),
                    "status": status,
                    "injury": short_injury(details.get("type") or details.get("detail") or ""),
                }
    except AttributeError:  # unexpected shape
        return {}
    return out


def short_injury(text: str) -> str:
    """'Not injury related - resting player' -> 'Rest', 'Left Knee' -> 'Knee'."""
    text = (text or "").strip()
    if not text:
        return "Undisclosed"
    low = text.lower()
    if low.startswith("not injury related"):
        tail = low.split("-", 1)[-1]
        return "Rest" if "rest" in tail else "Personal" if "personal" in tail else "Non-injury"
    first = text.split(",")[0].split("/")[0]
    words = [w for w in first.split() if w.lower() not in ("left", "right")]
    return " ".join(words[:2]).title() or "Undisclosed"


def injury_report(week: int | None = None, now: datetime | None = None,
                  games_source: str | None = None) -> tuple[int | None, list[dict], str]:
    """(week, players, note) for every team playing that week.

    Official game designations (Out/Doubtful/Questionable) come first; until
    they post, players who missed or were limited in practice are listed.
    """
    upcoming = upcoming_nfl(now, games_source)
    if not upcoming:
        return None, [], "No upcoming games."
    current = min(int(g["week"]) for g in upcoming)
    week = week or current
    games = [g for g in upcoming if int(g["week"]) == week]
    matchup = {}
    for g in games:
        when = kickoff_of(g)
        matchup[g["home_team"]] = (f"vs {g['away_team']}", when)
        matchup[g["away_team"]] = (f"@ {g['home_team']}", when)
    if week > current:
        return week, [], (f"Week {week} injury reports come out the week of the games "
                          "(practice reports from Wednesday, game statuses on Friday).")

    season = season_for((now or datetime.now(EASTERN)).date())
    players, official = [], set()
    espn = espn_official() if week == current else {}
    espn_teams = {t for t, _ in espn} & set(matchup)
    seen = set()
    for r in data.release("injuries", season, season, max_age=INJURY_MAX_AGE):
        if r.get("game_type") != "REG" or r.get("week") != str(week) or r["team"] not in matchup:
            continue
        key = (r["team"], name_key(r["full_name"]))
        seen.add(key)
        status = r.get("report_status", "")
        injury = short_injury(r.get("report_primary_injury") or r.get("practice_primary_injury"))
        if r["team"] in espn_teams:
            # ESPN already has this team's official report: it replaces both
            # nflverse's statuses and the practice list.
            info = espn.get(key)
            status = info["status"] if info else ""
            if info and info["injury"] != "Undisclosed":
                injury = info["injury"]
        elif not status:
            practice = r.get("practice_status", "")
            status = ("Did not practice" if practice.startswith("Did Not") else
                      "Limited" if practice.startswith("Limited") else "")
        if status in GAME_STATUSES:
            official.add(r["team"])
        if status not in SEVERITY:
            continue
        opp, when = matchup[r["team"]]
        players.append({
            "team": r["team"], "team_name": ABBR_NAME.get(r["team"], r["team"]),
            "opponent": opp, "kickoff": when, "player": r["full_name"],
            "position": r.get("position", ""), "status": status,
            "official": status in GAME_STATUSES, "injury": injury,
        })
    # Players ESPN lists who weren't on the practice report yet.
    for (team, key), info in espn.items():
        if team in matchup and (team, key) not in seen:
            opp, when = matchup[team]
            official.add(team)
            players.append({"team": team, "team_name": ABBR_NAME.get(team, team),
                            "opponent": opp, "kickoff": when, "player": info["player"],
                            "position": info["position"], "status": info["status"],
                            "official": True, "injury": info["injury"]})
    players.sort(key=lambda p: (p["kickoff"], p["team"], SEVERITY.index(p["status"]), p["player"]))
    teams = set(matchup)
    if official >= teams:
        note = f"Official Week {week} game statuses for all {len(teams)} teams."
    elif official:
        note = (f"Official game statuses in for {len(official)} of {len(teams)} teams; "
                "the rest show practice participation until Friday's report.")
    else:
        note = ("Official game statuses aren't out yet (usually Friday afternoon); "
                "showing who missed or was limited in practice.")
    return week, players, note
