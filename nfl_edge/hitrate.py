"""How often a bet would have won in games already played ("hit rate").

For a player prop, each of the player's games this season and last: would this
exact bet (e.g. 25+ receiving yards) have won? For a team bet, each of the
team's games: a moneyline is a win, a spread is the margin plus the line, a
total is the points scored. This is plain history, not a prediction: it ignores
opponents and how the player is used now, and small samples swing a lot.
"""

from __future__ import annotations

from dataclasses import dataclass

from .context import name_key
from .stats import TEAM_ABBR

# The game-log column(s) each prop is settled on.
PROP_STATS = {
    "player_pass_yds": "passing_yards", "player_pass_tds": "passing_tds",
    "player_rush_yds": "rushing_yards", "player_reception_yds": "receiving_yards",
    "player_receptions": "receptions", "player_anytime_td": "rushing_tds+receiving_tds",
}


@dataclass
class HitRate:
    hits: int
    games: int
    results: tuple[str, ...] = ()  # e.g. ("2025 W 45-21",), newest first, for matchups

    @property
    def rate(self) -> float | None:
        return self.hits / self.games if self.games else None

    def text(self) -> str:
        if not self.games:
            return "no games"
        return f"{self.hits} of {self.games}  ({self.rate:.0%})"


def _num(row: dict, cols: str) -> float:
    total = 0.0
    for c in cols.split("+"):
        try:
            total += float(row.get(c) or 0)
        except ValueError:
            pass
    return total


def _stat_for(market: str) -> str | None:
    return next((col for prefix, col in PROP_STATS.items() if market.startswith(prefix)), None)


def prop_rates(b, players, season: int) -> dict[int, HitRate] | None:
    """{season: HitRate} for a player prop, this season and last."""
    col = _stat_for(b.market)
    if col is None:
        return None
    key = name_key(b.pick.rsplit(" ", 1)[0])
    need = (b.point or 0.5) if b.market != "player_anytime_td" else 0.5
    out = {}
    for s in (season, season - 1):
        vals = [_num(r, col) for yr, r in players.games_played.get(key, []) if yr == s]
        out[s] = HitRate(sum(v > need for v in vals), len(vals))
    return out


def _team_games(games: list[dict], abbr: str, s: int):
    for g in games:
        if g["season"] != str(s) or g["game_type"] != "REG" or g["home_score"] == "":
            continue
        if abbr in (g["home_team"], g["away_team"]):
            home = g["home_team"] == abbr
            pts, opp = int(g["home_score"]), int(g["away_score"])
            yield (pts, opp) if home else (opp, pts), g


def team_rates(b, games: list[dict], season: int) -> dict[int, HitRate] | None:
    """{season: HitRate} for a moneyline, spread, total or team total."""
    teams = [TEAM_ABBR.get(t.strip()) for t in b.game.split(" @ ")]
    if None in teams:
        return None
    out = {}
    for s in (season, season - 1):
        if b.market in ("totals", "alternate_totals"):
            seen, hits, n = set(), 0, 0
            for abbr in teams:  # every game either team played
                for (pts, opp), g in _team_games(games, abbr, s):
                    if g["game_id"] in seen:
                        continue
                    seen.add(g["game_id"])
                    total = pts + opp
                    n += 1
                    hits += total > b.point if b.pick == "Over" else total < b.point
            out[s] = HitRate(hits, n)
            continue
        team_name = b.pick.rsplit(" ", 1)[0] if "team_totals" in b.market else b.pick
        abbr = TEAM_ABBR.get(team_name)
        if abbr is None:
            return None
        hits = n = 0
        for (pts, opp), _ in _team_games(games, abbr, s):
            n += 1
            if b.market == "h2h":
                hits += pts > opp
            elif b.market in ("spreads", "alternate_spreads"):
                hits += pts - opp + b.point > 0
            elif "team_totals" in b.market:
                hits += pts > b.point if b.pick.endswith("Over") else pts < b.point
            else:
                return None
        out[s] = HitRate(hits, n)
    return out


H2H_SEASONS = 3  # this season and the two before it


def head_to_head(b, games: list[dict], season: int) -> HitRate | None:
    """A moneyline pick's record against this opponent (playoffs included) since
    the season two years back, e.g. 2024-2026."""
    teams = [TEAM_ABBR.get(t.strip()) for t in b.game.split(" @ ")]
    me = TEAM_ABBR.get(b.pick)
    if None in teams or me not in teams:
        return None
    opp = teams[0] if teams[1] == me else teams[1]
    first = season - H2H_SEASONS + 1
    hits = n = 0
    results = []
    for g in games:
        if g["home_score"] == "" or not first <= int(g["season"]) <= season:
            continue
        if {g["home_team"], g["away_team"]} != {me, opp}:
            continue
        mine, theirs = ((int(g["home_score"]), int(g["away_score"])) if g["home_team"] == me
                        else (int(g["away_score"]), int(g["home_score"])))
        n += 1
        hits += mine > theirs
        word = "W" if mine > theirs else "L" if mine < theirs else "T"
        results.append((int(g["season"]), int(g.get("week") or 0), f"{g['season']} {word} {mine}-{theirs}"))
    return HitRate(hits, n, tuple(r for *_, r in sorted(results, reverse=True)))


def rates(b, players, games: list[dict], season: int) -> dict | None:
    """This season's and last season's hit rate for any NFL bet we can settle, plus
    ("h2h") a moneyline pick's record against its opponent."""
    if b.market.startswith("player_"):
        return prop_rates(b, players, season)
    out = team_rates(b, games, season)
    if out is not None and b.market == "h2h":
        out["h2h"] = head_to_head(b, games, season)
    return out


STRONG_RATE = 0.75  # a leg "keeps winning" if it hit at least this often


def record_of(r: dict | None, season: int) -> HitRate | None:
    """The record to judge a bet by: this season's once it has 3 games, else last
    season's with 6 or more."""
    now, last = (r or {}).get(season), (r or {}).get(season - 1)
    return now if now and now.games >= 3 else last if last and last.games >= 6 else None


def strong_leg_check(season: int, min_rate: float = STRONG_RATE):
    """f(bet) -> True if the bet has won at least `min_rate` of the time (NFL), for the
    Money Maker; None if the records can't be loaded."""
    try:
        from .insights import context

        ctx = context(season)
    except Exception:
        return None
    seen: dict = {}

    def strong(b) -> bool:
        key = (b.market, b.pick, b.point)
        if key not in seen:
            try:
                rec = record_of(rates(b, ctx.players, ctx.games, season), season)
            except Exception:
                rec = None
            seen[key] = bool(rec and rec.rate >= min_rate)
        return seen[key]
    return strong
