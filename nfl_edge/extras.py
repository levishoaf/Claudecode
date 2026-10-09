"""Player props and alternate lines.

Prices come from The Odds API's per-event endpoint. Probabilities are
anchored to the market wherever possible:

- Alternate spreads, totals and team totals start from the consensus main
  line. For the NFL, the chance of each alternate line comes from real
  results of past games with a similar line, which keeps key numbers like
  3 and 7. College games use a normal curve.
- Player props start from the market's main over/under for that player with
  the margin removed. The player's game logs then decide how quickly the
  chances fall off for alternate "X+" lines. With no main line to anchor
  to, the game logs alone are used and the bet is labelled "model only".
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from . import data
from .context import name_key
from .finder import SHARP_BOOKS, TARGET_BOOK, Bet
from .odds import devig_power, implied_probability, kelly_fraction
from .stats import TEAM_ABBR, normal_cdf

STATS_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "stats_player/stats_player_week_{season}.csv")

GAME_MARKETS = ["alternate_spreads", "alternate_totals", "team_totals", "alternate_team_totals"]
# Odds API market -> (nflverse stat column(s), label, distribution)
PLAYER_MARKETS = {
    "player_pass_yds": ("passing_yards", "Passing Yds", "normal"),
    "player_pass_tds": ("passing_tds", "Passing TDs", "poisson"),
    "player_rush_yds": ("rushing_yards", "Rushing Yds", "normal"),
    "player_reception_yds": ("receiving_yards", "Receiving Yds", "normal"),
    "player_receptions": ("receptions", "Receptions", "count"),
    "player_anytime_td": ("rushing_tds+receiving_tds", "Anytime TD", "poisson"),
}
ALT = "_alternate"
DEFAULT_NFL_MARKETS = GAME_MARKETS + list(PLAYER_MARKETS) + [
    m + ALT for m in PLAYER_MARKETS if m != "player_anytime_td"]
DEFAULT_NCAAF_MARKETS = GAME_MARKETS
TEAM_SD = {"nfl": 9.1, "ncaaf": 11.0}
NORMAL_SD = {"ncaaf": {"margin": 16.5, "total": 16.0}}


def base_market(key: str) -> str:
    return key[:-len(ALT)] if key.endswith(ALT) else key


def stat_label(market: str) -> str:
    return PLAYER_MARKETS[base_market(market)][1]


# ---------------------------------------------------------------- game lines

class LineDistribution:
    """P(final margin or total > x) given the market line."""

    def __init__(self, sport: str, games: list[dict] | None = None):
        self.sport = sport
        self.history: dict[str, list[tuple[float, int]]] = {"margin": [], "total": []}
        if sport == "nfl" and games:
            for g in games:
                if (g["game_type"] == "REG" and g["home_score"] and g["spread_line"]
                        and g["total_line"] and 2015 <= int(g["season"])):
                    self.history["margin"].append((float(g["spread_line"]), int(g["result"])))
                    self.history["total"].append((float(g["total_line"]), int(g["total"])))
            for k in self.history:
                self.history[k].sort()

    def outcome(self, kind: str, line: float, x: float) -> tuple[float, float]:
        """(P(value > x), P(value == x)) for a game whose market line is `line`."""
        if self.sport != "nfl" or not self.history[kind]:
            sd = NORMAL_SD.get(self.sport, {"margin": 13.5, "total": 13.2})[kind]
            return 1 - normal_cdf((x - line) / sd), 0.0
        hist = self.history[kind]
        lines = [h[0] for h in hist]
        for width in (1.0, 1.5, 2.5, 4.0, 8.0):
            lo, hi = bisect.bisect_left(lines, line - width), bisect.bisect_right(lines, line + width)
            if hi - lo >= 150:
                break
        sample = hist[lo:hi]
        # Narrow windows keep real final scores, so key numbers like 3 and 7
        # survive. Wide windows (rare lines) are shifted onto this line.
        shift = 0.0 if width <= 1.5 else line - statistics.mean(l for l, _ in sample)
        over = sum(1 for _, v in sample if v + shift > x + 1e-9)
        push = sum(1 for _, v in sample if abs(v + shift - x) < 1e-9)
        return over / len(sample), push / len(sample)


def consensus(event: dict, market: str) -> dict[str, float]:
    """Median main-line point per outcome name across books (e.g. home spread)."""
    points: dict[str, list[float]] = defaultdict(list)
    for bk in event.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m["key"] == market:
                for o in m["outcomes"]:
                    if o.get("point") is not None:
                        points[o["name"]].append(o["point"])
    return {k: statistics.median(v) for k, v in points.items()}


# ---------------------------------------------------------------- players

@dataclass
class Profile:
    team: str
    mean: float
    sd: float
    games: int


class PlayerModel:
    """Per-player stat distributions from nflverse weekly stats."""

    ROLE = {"QB": lambda r: _n(r, "attempts") >= 15,
            "RB": lambda r: _n(r, "carries") + _n(r, "targets") >= 6,
            "WR": lambda r: _n(r, "targets") >= 3,
            "TE": lambda r: _n(r, "targets") >= 2}

    def __init__(self, season: int, games: list[dict], stats: dict[int, list[dict]]):
        self.season = season
        self.team_games: dict[tuple[int, str], int] = defaultdict(int)
        for g in games:
            if g["game_type"] == "REG" and g["home_score"] and int(g["season"]) in stats:
                for t in (g["home_team"], g["away_team"]):
                    self.team_games[(int(g["season"]), t)] += 1
        self.appearances: dict[tuple[str, int], set] = defaultdict(set)
        self.logs: dict[str, list[tuple[int, str, dict]]] = defaultdict(list)
        self.position: dict[str, str] = {}
        self.current_team: dict[str, str] = {}
        self.names: dict[str, str] = {}
        for s, rows in stats.items():
            for r in rows:
                if r.get("season_type") != "REG":
                    continue
                key = name_key(r["player_display_name"])
                self.names[key] = r["player_display_name"]
                self.appearances[(key, s)].add((r["team"], r["week"]))
                role = self.ROLE.get(r["position"])
                if role and role(r):
                    self.logs[key].append((s, r["team"], r))
                    self.position[key] = r["position"]
                if s == season:
                    self.current_team[key] = r["team"]

    def missed_most_of_a_season(self, key: str) -> bool:
        for s in (self.season - 1, self.season):
            apps = self.appearances.get((key, s))
            if not apps:
                continue
            possible = max(self.team_games[(s, t)] for t, _ in apps)
            if possible and len(apps) * 2 <= possible:
                return True
        return False

    def profile(self, player: str, stat: str) -> Profile | None:
        key = name_key(player)
        team = self.current_team.get(key)
        if team is None or self.missed_most_of_a_season(key):
            return None
        logs = [(s, r) for s, t, r in self.logs.get(key, []) if s == self.season or t == team]
        if len(logs) < 4:
            return None
        w = [2.0 if s == self.season else 1.0 for s, _ in logs]
        vals = [sum(_n(r, c) for c in stat.split("+")) for _, r in logs]
        mean = sum(a * v for a, v in zip(w, vals)) / sum(w)
        var = sum(a * (v - mean) ** 2 for a, v in zip(w, vals)) / sum(w)
        return Profile(team, mean, math.sqrt(var), len(logs))


def _n(row: dict, col: str) -> float:
    try:
        return float(row.get(col) or 0)
    except ValueError:
        return 0.0


def p_over(dist: str, mean: float, sd: float, point: float) -> float:
    """P(stat > point) for a stat with this mean."""
    if dist == "poisson":
        lam = max(mean, 1e-6)
        k = math.floor(point) + 1  # need at least k
        return 1 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k))
    sd = max(sd, 0.35 * mean, 1.0 if dist == "count" else 5.0)
    edge = math.floor(point) + 0.5 if dist == "count" else point
    return 1 - normal_cdf((edge - mean) / sd)


def anchor_mean(dist: str, sd: float, point: float, prob_over: float) -> float:
    """The mean that makes P(stat > point) equal the market's no-vig chance."""
    lo, hi = 0.0, max(point * 4, 10.0)
    for _ in range(80):
        mid = (lo + hi) / 2
        if p_over(dist, mid, sd, point) < prob_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------- pricing

def _make_bet(event, market, pick, point, price, prob, push, source) -> Bet:
    # EV with pushes: a push returns the stake.
    win, lose = prob, 1 - prob - push
    dec = 1 + (price / 100 if price > 0 else 100 / -price)
    ev = win * (dec - 1) - lose
    cond = prob / (1 - push) if push < 1 else prob
    return Bet(
        game=f"{event['away_team']} @ {event['home_team']}",
        commence_time=datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00")),
        market=market, pick=pick, point=point, fd_price=price, fair_prob=cond,
        market_prob=cond, model_prob=None, ev=ev, kelly=kelly_fraction(cond, price),
        books=[source],
    )


def _devig_pair(outcomes: list[dict]) -> float | None:
    """No-vig P(Over) from an Over/Under pair."""
    over = next((o for o in outcomes if o["name"] in ("Over", "Yes")), None)
    under = next((o for o in outcomes if o["name"] in ("Under", "No")), None)
    if not over or not under:
        return None
    return devig_power([implied_probability(over["price"]), implied_probability(under["price"])])[0]


def price_event(event: dict, extra: dict, sport: str, lines: LineDistribution,
                players: PlayerModel | None) -> list[Bet]:
    """Bets for every FanDuel outcome in the extra markets of one game."""
    home, away = event["home_team"], event["away_team"]
    spread = consensus(event, "spreads").get(home)  # home points, e.g. -3.5
    total = consensus(event, "totals").get("Over")
    margin_line = -spread if spread is not None else None  # expected home margin

    # Main player lines (any book), for anchoring: (market, player) -> [(point, p_over, sharp)]
    anchors: dict[tuple[str, str], list[tuple[float, float, bool]]] = defaultdict(list)
    for bk in extra.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m["key"] in PLAYER_MARKETS and m["key"] != "player_anytime_td":
                by_player: dict[tuple[str, float], list[dict]] = defaultdict(list)
                for o in m["outcomes"]:
                    by_player[(o.get("description", ""), o.get("point"))].append(o)
                for (player, point), outs in by_player.items():
                    q = _devig_pair(outs)
                    if q is not None and point is not None:
                        anchors[(m["key"], name_key(player))].append(
                            (point, q, bk["key"] in SHARP_BOOKS))

    bets = []
    fd = next((b for b in extra.get("bookmakers", []) if b["key"] == TARGET_BOOK), None)
    for m in (fd or {}).get("markets", []):
        key = m["key"]
        for o in m["outcomes"]:
            price, point, name = o["price"], o.get("point"), o["name"]
            if key in ("alternate_spreads",) and margin_line is not None and point is not None:
                # Team covers if its margin + point > 0.
                x = -point if name == home else point  # home margin threshold
                over, push = lines.outcome("margin", margin_line, x)
                prob = over if name == home else 1 - over - push
                bets.append(_make_bet(event, key, name, point, price, prob, push, "market line"))
            elif key == "alternate_totals" and total is not None and point is not None:
                over, push = lines.outcome("total", total, point)
                prob = over if name == "Over" else 1 - over - push
                bets.append(_make_bet(event, key, name, point, price, prob, push, "market line"))
            elif key in ("team_totals", "alternate_team_totals") and total is not None \
                    and margin_line is not None and point is not None:
                team = o.get("description", "")
                implied = total / 2 + (margin_line / 2 if team == home else -margin_line / 2)
                p = 1 - normal_cdf((point - implied) / TEAM_SD[sport])
                prob = p if name == "Over" else 1 - p
                bets.append(_make_bet(event, key, f"{team} {name}", point, price, prob, 0.0,
                                      "market line"))
            elif base_market(key) in PLAYER_MARKETS and players is not None:
                bet = _price_prop(event, key, o, players, anchors)
                if bet:
                    bets.append(bet)
    return bets


def _price_prop(event, key, o, players: PlayerModel, anchors) -> Bet | None:
    player = o.get("description", "")
    stat, _, dist = PLAYER_MARKETS[base_market(key)]
    prof = players.profile(player, stat)
    teams = {TEAM_ABBR.get(event["home_team"]), TEAM_ABBR.get(event["away_team"])}
    if prof is None or prof.team not in teams:
        return None  # unknown, missed most of a season, or on another team
    point = o.get("point")
    if base_market(key) == "player_anytime_td":
        point = 0.5
    if point is None:
        return None
    mean, source = prof.mean, "player logs only"
    found = anchors.get((base_market(key), name_key(player)))
    if found:
        # Prefer a sharp book's line, else the line nearest the middle.
        found.sort(key=lambda a: (not a[2], abs(a[1] - 0.5)))
        a_point, a_prob, _ = found[0]
        mean = anchor_mean(dist, prof.sd, a_point, a_prob)
        source = "market line"
    p = p_over(dist, mean, prof.sd, point)
    side = o["name"]
    prob = p if side in ("Over", "Yes") else 1 - p
    pick = f"{player} {side}"
    return _make_bet(event, key, pick, point, o["price"], prob, 0.0, source)


def load_player_model(season: int, games: list[dict]) -> PlayerModel:
    stats = {}
    for s in (season - 1, season):
        try:
            stats[s] = data.read_csv(STATS_URL.format(season=s), permanent=s < season)
        except data.DataError:
            stats[s] = []
    return PlayerModel(season, games, stats)
