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

from . import cfb, data, learn
from .context import name_key
from .extras import LineDistribution, defense_table, load_player_model, p_over
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
    props need FanDuel's own line, so they come only when a bet type is chosen
    (`always_offered=False`), marked to confirm on FanDuel."""
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
        if b.market == "spreads":
            b.main_line = b.point

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
                    bets[-1].main_line = main  # for the closing-line check
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
    learn.adjust(bets, "nfl")  # chances corrected by how past bets like these did
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
            out.append(make_bet(f"{away} @ {home}", kickoff_of(g), "h2h", team, None, prob,
                                g["game_id"], "stats model (no lines yet)", week=week))
    learn.adjust(out, "nfl")
    return [b for b in out if lo <= b.fair_prob <= hi]


# How much a defense (its yards and catches allowed to the position, vs average) and
# the expected game script (the team's spread) move a player's projection. Kept only
# where they predicted 2025 better on the replay (`python -m nfl_edge.learn --props`).
# Tested: recent-game weighting, a blend with his actual hit rate, game script and
# defense for the other stats all predicted 2025 slightly worse, so they're off.
DEFENSE_WEIGHT = {"passing_yards": 0.5}  # stat -> weight (1.0 = the full ratio)
SCRIPT_RUSH = 0.0  # rushing up this much per point a team is favored by
SCRIPT_PASS = 0.0  # passing and receiving down this much per point favored by
RUSH_STATS = {"rushing_yards"}
PASS_STATS = {"passing_yards", "passing_tds", "receiving_yards", "receptions"}


def prop_chances(players, key: str, factor: float, only: set[str] | None = None,
                 featured: bool = False, opponent: str | None = None, defense=None,
                 favored_by: float = 0.0) -> list[tuple[float, str, int, str]]:
    """[(chance, market, threshold, stat)] for every rung of a player's prop ladders,
    his averages scaled by `factor` (his team's expected points vs its average), the
    `opponent`'s defense (a `defense_table`) and the game script (`favored_by`)."""
    from .extras import EMPIRICAL_PRIOR

    name = players.names[key]
    position = players.position.get(key, "")
    out = []
    for stat, market, dist, ladder in PROP_LADDERS.get(position, []):
        if only is not None and market not in only:
            continue
        prof = players.profile(name, stat)
        minimum = (FEATURED_MIN_AVERAGE if featured else PROP_MIN_AVERAGE).get(stat, 0)
        if prof is None or prof.mean < minimum:
            continue  # FanDuel wouldn't list this prop for him
        mean = prof.mean * factor ** SCALING[dist]
        if defense is not None and opponent and DEFENSE_WEIGHT.get(stat):
            mean *= defense(opponent, position, stat) ** DEFENSE_WEIGHT[stat]
        if stat in RUSH_STATS:
            mean *= max(1 + SCRIPT_RUSH * favored_by, 0.5)
        elif stat in PASS_STATS:
            mean *= max(1 - SCRIPT_PASS * favored_by, 0.5)
        for k in ladder:
            p = p_over(dist, mean, prof.sd, k - 0.5)
            if EMPIRICAL_PRIOR and prof.values:  # how often he really cleared it
                hit = sum(w for w, v in prof.values if v >= k)
                total = sum(w for w, _ in prof.values)
                p = (hit + EMPIRICAL_PRIOR * p) / (total + EMPIRICAL_PRIOR)
            out.append(((p * prof.games + 1) / (prof.games + 2), market, k, stat))  # small-sample shrink
    return out


def _props(rows, all_games, season, lo, hi, every_line: bool = False,
           only: set[str] | None = None, featured: bool = False) -> list[Bet]:
    players = load_player_model(season, all_games)
    implied, info, opponent, favored = {}, {}, {}, {}
    defense = defense_table(players.stats.get(season, []))
    for g in rows:
        tot, sp = float(g["total_line"]), float(g["spread_line"])
        implied[g["home_team"]], implied[g["away_team"]] = tot / 2 + sp / 2, tot / 2 - sp / 2
        opponent[g["home_team"]], opponent[g["away_team"]] = g["away_team"], g["home_team"]
        favored[g["home_team"]], favored[g["away_team"]] = sp, -sp
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
    soon = starting_soon(rows)
    if soon:  # final check: each game's own page, re-read every five minutes
        status.update(late_statuses(rows))
        NOTES["late"] = (f"Final check: injury news for the {len(soon)} game"
                         f"{'s' * (len(soon) > 1)} starting within 2 hours is re-checked "
                         "every 5 minutes")
    else:
        NOTES.pop("late", None)
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
        found = [f for f in prop_chances(players, key, factor, only, featured,
                                         opponent.get(team), defense, favored.get(team, 0.0))
                 if lo <= f[0] <= hi]  # (chance, market, threshold)
        if not every_line:  # the player's likeliest line of each prop type
            best = {}
            for f in sorted(found, key=lambda f: -f[0]):
                best.setdefault(f[1], f)
            found = list(best.values())
        for p, market, k, _ in found:
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
    lines: dict[str, float] = {}
    for w in sorted({int(r["week"]) for r in upcoming}):
        lines.update(cfb.espn_lines(season, w))
    out = []
    for r in upcoming:
        kick = kick_of(r)
        hp, ap, _ = model.predict_row(r)
        p_home = normal_cdf((hp - ap) / cfb.MARGIN_SD)
        source = "college model"
        market = lines.get(str(r["game_id"]))
        if market is not None:  # the betting market, nudged by the model
            p_home = cfb.MARKET_WEIGHT * market + (1 - cfb.MARKET_WEIGHT) * p_home
            source = "market line + college model"
        game = f"{r['away_team']} @ {r['home_team']}"
        for team, p in ((r["home_team"], p_home), (r["away_team"], 1 - p_home)):
            b = make_bet(game, kick, "h2h", team, None, p, r["game_id"], source,
                         week=int(r["week"]))
            if market is None and not b.note:  # the model alone: no betting line to lean on
                b.note = NO_LINE
            out.append(b)
    learn.adjust(out, "ncaaf")
    return [b for b in out if lo <= b.fair_prob <= hi]


# ---------------------------------------------------------------- UFC

UFC_CARDS_AHEAD = 4


def ufc_cards(now: datetime | None = None) -> list[tuple[str, str, datetime]]:
    """[(event id, card name, start)] for the next few UFC cards."""
    from . import ufc

    return [(eid, name, datetime.fromisoformat(bs[0]["date"].replace("Z", "+00:00")))
            for eid, name, bs in ufc.upcoming_cards(now)[:UFC_CARDS_AHEAD]]


def ufc_board(lo: float, hi: float, card: str | None = None, day: date | None = None,
              now: datetime | None = None) -> list[Bet]:
    """Each fight's moneylines (both fighters) and whether it goes the distance, from the
    UFC model, for one card (default: the next one) or one day's fights."""
    from . import ufc

    now = now or datetime.now(timezone.utc)
    cards = ufc.upcoming_cards(now)
    if day is not None:
        bouts = [b for _, _, bs in cards for b in bs
                 if datetime.fromisoformat(b["date"].replace("Z", "+00:00"))
                 .astimezone(EASTERN).date() == day]
        name = "Today's fights"
    else:
        chosen = next((c for c in cards if c[0] == card), cards[0] if cards else None)
        bouts, name = (chosen[2], chosen[1]) if chosen else ([], "No UFC card scheduled")
    NOTES["ufc_card"] = name
    test = (ufc.weights().get("test") or {}).get("win") or {}
    if test:
        NOTES["ufc_model"] = (
            f"UFC chances come from the fight model: on {test['n']:,} recent fights it never "
            f"saw, its favorite won {test['likelier_won']:.0%}. It knows only UFC fights, so "
            "fighters new to the UFC lean on age, reach and few results; where FanDuel's "
            "price disagrees a lot, trust the price.")
    if not bouts:
        return []
    ratings = ufc.current_ratings()
    numbers = ufc.card_numbers(now.year)
    out = []
    for b in bouts:
        if not b["a_name"] or not b["b_name"]:
            continue
        kick = datetime.fromisoformat(b["date"].replace("Z", "+00:00")).astimezone(EASTERN)
        p_a, p_distance = ufc.chances(b, ratings)
        game = f"{b['a_name']} vs {b['b_name']}"
        week = numbers.get(b["id"])
        for fighter, p in ((b["a_name"], p_a), (b["b_name"], 1 - p_a)):
            out.append(make_bet(game, kick, "h2h", fighter, None, p, b["id"], "UFC model",
                                week=week))
        fight = f"{ufc.last_name(b['a_name'])} vs {ufc.last_name(b['b_name'])}"
        for pick, p in ((f"{fight} goes the distance", p_distance),
                        (f"{fight} doesn't go the distance", 1 - p_distance)):
            out.append(make_bet(game, kick, "fight_distance", pick, None, p, b["id"],
                                "UFC model", week=week))
    learn.adjust(out, "ufc")  # graded UFC picks have won less often than the model said
    for b in out:
        if b.market == "h2h" and not b.note:  # the model alone (real odds replace it)
            b.note = NO_LINE
    return [b for b in out if lo <= b.fair_prob <= hi]


# ---------------------------------------------------------------- MLB

def mlb_days(now: datetime | None = None) -> list[tuple[date, bool]]:
    """[(day, lines posted?)] for the next days with MLB games."""
    from . import mlb

    return [(d, any(g.get("lines") for g in mlb.day(d) if g["state"] == "pre"))
            for d in mlb.upcoming_days(now)]


NO_LINE = "No line yet"
MLB_ALL_DAYS = 1  # the Games menu's "Next 7 days" (other values are YYYYMMDD days)


def mlb_board(lo: float, hi: float, day: date | None = None, now: datetime | None = None,
              every_line: bool = False, all_days: bool = False) -> list[Bet]:
    """One day's games (default: the next day with games): each team's moneyline and run
    line (FanDuel always offers both, the run line at 1.5) and, with `every_line`, the
    game's total runs at DraftKings' line (marked to confirm on FanDuel)."""
    from . import mlb

    now = now or datetime.now(timezone.utc)
    days = mlb.upcoming_days(now) if all_days or day is None else [day]
    if not all_days:
        days = days[:1]
    if not days:
        NOTES["mlb_day"] = "No MLB games scheduled"
        return []
    NOTES["mlb_day"] = ("Next 7 days" if all_days else f"{days[0]:%A, %b} {days[0].day}")
    games = [g for d in days for g in mlb.day(d) if g["state"] == "pre"]
    if not games:
        return []
    ratings = mlb.current_ratings()
    out, starters = [], []
    for g in games:
        kick = datetime.fromisoformat(g["date"].replace("Z", "+00:00")).astimezone(EASTERN)
        c = mlb.chances(g, ratings)
        on = mlb.us_date(g)  # series repeat a matchup: the date tells the games apart
        game = f"{g['away_name']} @ {g['home_name']}, {on:%b} {on.day}"
        week = mlb.season_week(on)
        hs, aws = (g.get("home_sp") or {}).get("name"), (g.get("away_sp") or {}).get("name")
        if hs or aws:
            starters.append(f"{g['away']} {aws or 'TBD'} vs {g['home']} {hs or 'TBD'}")
        mine = []
        for team, p in ((g["home_name"], c["home"]), (g["away_name"], 1 - c["home"])):
            mine.append(make_bet(game, kick, "h2h", team, None, p, g["id"], c["source"], week=week))
        line = c["rl_line"]
        for team, point, p in ((g["home_name"], line, c["home_rl"]),
                               (g["away_name"], -line, c["away_rl"])):
            mine.append(make_bet(game, kick, "run_line", team, point, p, g["id"], c["source"],
                                 week=week))
        if c["source"] != "DraftKings line":
            for b in mine:  # the model alone: weaker than a posted line
                b.note = NO_LINE
        out += mine
        if every_line and c["over"] is not None:
            for side, p in (("Over", c["over"]), ("Under", 1 - c["over"])):
                b = make_bet(game, kick, "totals_runs", side, float(c["total"]), p, g["id"],
                             "DraftKings line", week=week)
                b.confirm_line = True  # FanDuel's total can sit half a run away
                out.append(b)
    NOTES["mlb_starters"] = " · ".join(starters)
    test = (mlb.weights().get("test") or {})
    NOTES["mlb_model"] = (
        "MLB chances are DraftKings' lines with the sportsbook's cut taken out: on "
        f"{test.get('games', 0):,} games this season the line's favorite won "
        f"{(test.get('market') or {}).get('likelier_won', 0):.0%}, better than our own model "
        f"({(test.get('model') or {}).get('likelier_won', 0):.0%}), so the model only fills in "
        "games with no line yet. Baseball favorites lose often: chances rarely top 70%."
        if test else "")
    return [b for b in out if lo <= b.fair_prob <= hi]


# ---------------------------------------------------------------- NBA

def nba_days(now: datetime | None = None) -> list[tuple[date, bool]]:
    """[(day, lines posted?)] for the next days with NBA games."""
    from . import nba

    return [(d, any(g.get("lines") for g in nba.day(d) if g["state"] == "pre"))
            for d in nba.upcoming_days(now)]


def nba_board(lo: float, hi: float, day: date | None = None, now: datetime | None = None,
              every_line: bool = False, all_days: bool = False) -> list[Bet]:
    """One day's games (default: the next day with games): each team's moneyline (FanDuel
    always offers it) and, with `every_line`, each side of DraftKings' spread and total
    (marked to confirm on FanDuel, whose line can sit half a point away)."""
    from . import nba

    now = now or datetime.now(timezone.utc)
    days = nba.upcoming_days(now) if all_days or day is None else [day]
    if not all_days:
        days = days[:1]
    if not days:
        NOTES["nba_day"] = "No NBA games scheduled"
        return []
    NOTES["nba_day"] = ("Next 7 days" if all_days else f"{days[0]:%A, %b} {days[0].day}")
    games = sorted((g for d in days for g in nba.day(d)
                    if g["state"] == "pre" and g["home"] in nba.TEAMS and g["away"] in nba.TEAMS),
                   key=lambda g: g["date"])
    if not games:
        return []
    ratings = nba.current_ratings()
    out = []
    for g in games:
        kick = datetime.fromisoformat(g["date"].replace("Z", "+00:00")).astimezone(EASTERN)
        c = nba.chances(g, ratings)
        ratings.played(g)  # a game later in the week: rest counts this one
        on = nba.us_date(g)
        game = f"{g['away_name']} @ {g['home_name']}, {on:%b} {on.day}"
        week = nba.season_week(on)
        mine = [make_bet(game, kick, "h2h", team, None, p, g["id"], c["source"], week=week)
                for team, p in ((g["home_name"], c["home"]), (g["away_name"], 1 - c["home"]))]
        if every_line:
            line = c["spread"]
            for team, point, p in ((g["home_name"], line, c["home_cover"]),
                                   (g["away_name"], -line, 1 - c["home_cover"])):
                mine.append(make_bet(game, kick, "spreads", team, point, p, g["id"], c["source"],
                                     week=week))
            if c["over"] is not None:
                for side, p in (("Over", c["over"]), ("Under", 1 - c["over"])):
                    mine.append(make_bet(game, kick, "totals", side, float(c["total"]), p,
                                         g["id"], c["source"], week=week))
        for b in mine:
            if b.market != "h2h":
                b.confirm_line = True  # FanDuel's line can sit half a point away
            if c["source"] != "NBA DraftKings line":
                b.note = NO_LINE  # the model alone: weaker than a posted line
        out += mine
    test = (nba.weights().get("test") or {})
    NOTES["nba_model"] = (
        "NBA chances are DraftKings' lines with the sportsbook's cut taken out. Games with "
        "no line yet use our model (team ratings, home court, back-to-backs): on "
        f"{test['games']:,} games last season ({test['season']}) its favorite won "
        f"{test['model']['likelier_won']:.0%}, the line's {test['market']['likelier_won']:.0%}."
        if test.get("model") else "")
    return [b for b in out if lo <= b.fair_prob <= hi]


# ---------------------------------------------------------------- injury report

SEVERITY = ["Out", "Doubtful", "Questionable", "Did not practice", "Limited"]
GAME_STATUSES = ("Out", "Doubtful", "Questionable")
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
# Same report on ESPN's other API host, tried if the first can't be reached.
ESPN_INJURIES_ALT = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
INJURY_MAX_AGE = 30 * 60  # injury news moves fast; refresh every 30 minutes


LATE_WINDOW = 2 * 3600  # seconds before kickoff when news is re-checked every few minutes
LATE_MAX_AGE = 5 * 60
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={id}"


def starting_soon(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """The games among `rows` that kick off within LATE_WINDOW."""
    now = now or datetime.now(EASTERN)
    return [g for g in rows if 0 <= (kickoff_of(g) - now).total_seconds() <= LATE_WINDOW]


def late_statuses(rows: list[dict], now: datetime | None = None) -> dict[tuple[str, str], str]:
    """{(team, player key): status} from ESPN's page for each game starting within two
    hours, re-read every five minutes, so late scratches and downgrades drop out.
    {} when no game is that close or ESPN can't be reached."""
    soon = starting_soon(rows, now)
    if not soon:
        return {}
    wanted = {frozenset((g["home_team"], g["away_team"])) for g in soon}
    out: dict[tuple[str, str], str] = {}
    try:
        board = data.read_json(ESPN_SCOREBOARD, "espn_nfl_scoreboard.json", LATE_MAX_AGE)
    except Exception:
        return {}
    for e in board.get("events", []):
        comps = (e.get("competitions") or [{}])[0].get("competitors", [])
        teams = frozenset(TEAM_ABBR.get((c.get("team") or {}).get("displayName", ""), "")
                          for c in comps)
        if teams not in wanted:
            continue
        try:
            summary = data.read_json(ESPN_SUMMARY.format(id=e["id"]),
                                     f"espn_nfl_summary_{e['id']}.json", LATE_MAX_AGE)
        except Exception:
            continue
        for team in summary.get("injuries", []):
            abbr = TEAM_ABBR.get((team.get("team") or {}).get("displayName", ""))
            for item in team.get("injuries", []) if abbr else []:
                status = (item.get("status") or "").strip().title()
                name = (item.get("athlete") or {}).get("displayName")
                if status in GAME_STATUSES and name:
                    out[(abbr, name_key(name))] = status
    return out


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
