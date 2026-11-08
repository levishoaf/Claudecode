"""Extra statistics on each NFL bet, and a confidence tier built from them.

For every bet, from free nflverse data (weekly player stats, snap counts, the
schedule) plus the injury report and the kickoff forecast:

- matchup: how much of the stat this week's opponent allows to the player's
  position, ranked among the teams (props)
- usage: share of targets or carries and snap share, last 3 games vs the season
- teammates: injured teammates whose targets or carries go elsewhere
- floor: the player's lowest output in his last 8 games, and how often he cleared
  the line comfortably
- rest and travel: short weeks, byes, time zones crossed
- home and away records, and whether the usual starting QB is playing
- weather: wind, rain or cold at an outdoor kickoff
- small-sample and streaky-stat warnings

These combine into a tier: green (the signals agree), yellow (mixed) or red (the
signals argue against it). It's a filter for skipping weaker bets, not a promise.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import data, hitrate
from .context import name_key
from .stats import TEAM_ABBR
from .venues import STADIUMS

GREEN, YELLOW, RED = "green", "yellow", "red"
TIER_WORDS = {GREEN: "Strong", YELLOW: "Mixed", RED: "Weak"}
POSITION_GROUP = {"WR": "WR", "TE": "TE", "RB": "RB", "FB": "RB", "QB": "QB"}
OUT = ("Out", "Doubtful")


@dataclass
class Insight:
    tier: str = YELLOW
    score: int = 0
    reasons: list[str] = field(default_factory=list)   # why the tier (short)
    notes: list[str] = field(default_factory=list)     # matchup, usage, floor, splits
    flags: list[str] = field(default_factory=list)     # warnings
    small_sample: bool = False
    shift: float = 0.0  # how much the tested signals moved the chance


def _num(row: dict, cols: str) -> float:
    total = 0.0
    for c in cols.split("+"):
        try:
            total += float(row.get(c) or 0)
        except ValueError:
            pass
    return total


def _stat_for(market: str) -> str | None:
    return next((col for prefix, col in hitrate.PROP_STATS.items() if market.startswith(prefix)),
                None)


class Context:
    """Everything the insights need, loaded once per build."""

    def __init__(self, season: int, games: list[dict], players, injuries=None, snaps=None,
                 forecast=None):
        self.season, self.games, self.players = season, games, players
        self.injuries = injuries or {}  # (team, name key) -> status
        self.forecast = forecast        # (stadium id, kickoff) -> {wind, precip, temp} | None
        self._weather: dict = {}
        self.abbr_name = {v: k for k, v in TEAM_ABBR.items()}
        # Snap share by (name key, week), this season.
        self.snaps: dict[tuple[str, int], float] = {}
        for r in snaps or []:
            if r.get("season") == str(season) and r.get("game_type", "REG") == "REG":
                try:
                    self.snaps[(name_key(r["player"]), int(r["week"]))] = float(r["offense_pct"])
                except (KeyError, ValueError):
                    pass
        # This season's rows by player, and team carries per week.
        self.rows: dict[str, list[dict]] = defaultdict(list)
        self.team_carries: dict[tuple[str, int], float] = defaultdict(float)
        allowed: dict[tuple[str, str, str, int], float] = defaultdict(float)
        self.opp_weeks: dict[str, set] = defaultdict(set)
        for key, logs in players.games_played.items():
            for s, r in logs:
                if s != season:
                    continue
                self.rows[key].append(r)
                week = int(r["week"])
                self.team_carries[(r["team"], week)] += _num(r, "carries")
                opp, pos = r.get("opponent_team"), POSITION_GROUP.get(r.get("position", ""))
                if opp and pos:
                    self.opp_weeks[opp].add(week)
                    for col in set(hitrate.PROP_STATS.values()):
                        allowed[(opp, pos, col, week)] += _num(r, col)
        for key in self.rows:
            self.rows[key].sort(key=lambda r: int(r["week"]))
        # Per game allowed by each defense to each position: (pos, col) -> {team: avg}
        self.allowed: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
        sums: dict[tuple[str, str, str], float] = defaultdict(float)
        for (opp, pos, col, _week), v in allowed.items():
            sums[(opp, pos, col)] += v
        for (opp, pos, col), v in sums.items():
            self.allowed[(pos, col)][opp] = v / max(len(self.opp_weeks[opp]), 1)
        # Schedule pieces: each team's home stadium, results, usual QB.
        self.home_stadium: dict[str, str] = {}
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.qb_starts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for g in games:
            if g.get("season") not in (str(season), str(season - 1)):
                continue
            if g.get("location", "Home") == "Home" and g.get("stadium_id"):
                counts[g["home_team"]][g["stadium_id"]] += 1
            if g["season"] == str(season) and g["home_score"] != "":
                for side in ("home", "away"):
                    if g.get(f"{side}_qb_name"):
                        self.qb_starts[g[f"{side}_team"]][g[f"{side}_qb_name"]] += 1
        for team, c in counts.items():
            self.home_stadium[team] = max(c, key=c.get)

    # ---------------------------------------------------------------- lookups
    def game_row(self, b) -> dict | None:
        gid = getattr(b, "game_id", None)
        return next((g for g in self.games if g["game_id"] == gid), None) if gid else None

    def team_of(self, b) -> str | None:
        """The team code a bet is on (the player's team for props); None for game totals."""
        if b.market.startswith("player_"):
            return self.players.current_team.get(name_key(b.pick.rsplit(" ", 1)[0]))
        if b.market in ("totals", "alternate_totals"):
            return None
        name = b.pick.rsplit(" ", 1)[0] if "team_totals" in b.market else b.pick
        return TEAM_ABBR.get(name)

    def weather(self, g: dict) -> dict | None:
        if not g or g.get("roof") in ("dome", "closed") or not self.forecast:
            return None
        key = g["game_id"]
        if key not in self._weather:
            try:
                kick = datetime.fromisoformat(f"{g['gameday']}T{g['gametime'] or '13:00'}:00")
                from .board import EASTERN

                kick = kick.replace(tzinfo=EASTERN).astimezone(timezone.utc)
                self._weather[key] = self.forecast(g["stadium_id"], kick)
            except Exception:
                self._weather[key] = None
        return self._weather[key]

    def signal_features(self, b, key: str, team: str, g: dict) -> dict:
        """The tested signals for a prop bet (nfl_edge/signals.py), from live data."""
        from . import signals
        from .extras import defense_table

        if not hasattr(self, "_defense"):
            self._defense = defense_table(self.players.stats.get(self.season, []))
        usual = signals.usual_qb(self.games, team, self.season)
        hurt = bool(usual and self.injuries.get((team, name_key(usual))) in OUT + ("Questionable",))
        gf = signals.game_features(g, team, self.home_stadium, self.games, self.season, hurt)
        opp = g["away_team"] if g["home_team"] == team else g["home_team"]
        w = self.weather(g) or {}
        stat = _stat_for(b.market)
        return signals.prop_features(self.players, key, team, stat, (b.point or 0) + 0.5,
                                     getattr(b, "raw_prob", None) or b.fair_prob,
                                     self._defense, opp, gf, w.get("wind"), w.get("temp"))

    # ---------------------------------------------------------------- signals
    def matchup(self, b, key: str, team: str, g: dict):
        """(text, +1 good / -1 tough / 0) for a prop: what the opponent allows."""
        col, pos = _stat_for(b.market), POSITION_GROUP.get(self.players.position.get(key, ""))
        if not col or not pos or not g or b.market == "player_anytime_td":
            return None
        opp = g["away_team"] if g["home_team"] == team else g["home_team"]
        table = self.allowed.get((pos, col), {})
        if opp not in table or len(table) < 16:
            return None
        ranked = sorted(table, key=table.get, reverse=True)
        rank = ranked.index(opp) + 1
        what = col.replace("_", " ").replace("rushing tds+receiving tds", "TDs")
        text = (f"{self.abbr_name.get(opp, opp)} allow {table[opp]:.0f} {what}/game to "
                f"{pos}s ({_ordinal(rank)} most of {len(table)})")
        return text, (1 if rank <= 10 else -1 if rank > len(table) - 10 else 0)

    def usage(self, b, key: str, team: str):
        """(text, +1 rising / -1 falling / 0) from target, carry and snap shares."""
        rows = self.rows.get(key, [])
        if len(rows) < 4:
            return None
        col = _stat_for(b.market) or ""
        if "rushing" in col:
            shares = [_num(r, "carries") / max(self.team_carries[(r["team"], int(r["week"]))], 1)
                      for r in rows]
            what = "carries"
        elif col in ("receiving_yards", "receptions"):
            shares = [_num(r, "target_share") for r in rows]
            what = "targets"
        else:
            shares = []
            what = ""
        snaps = [self.snaps.get((key, int(r["week"]))) for r in rows]
        snaps = [s for s in snaps if s is not None]
        parts, trend = [], 0
        if shares:
            last, season = sum(shares[-3:]) / 3, sum(shares) / len(shares)
            arrow = "↑" if last - season >= 0.04 else "↓" if season - last >= 0.04 else "→"
            trend = 1 if arrow == "↑" else -1 if arrow == "↓" else 0
            parts.append(f"{last:.0%} of team {what} last 3 (season {season:.0%}) {arrow}")
        if len(snaps) >= 4:
            last, season = sum(snaps[-3:]) / 3, sum(snaps) / len(snaps)
            parts.append(f"snaps {last:.0%} last 3 (season {season:.0%})")
            if not shares:
                trend = 1 if last - season >= 0.08 else -1 if season - last >= 0.08 else 0
        return ("Usage: " + ", ".join(parts), trend) if parts else None

    def teammates(self, key: str, team: str, b) -> list[str]:
        """Injured teammates whose targets or carries are up for grabs."""
        col = _stat_for(b.market) or ""
        out = []
        for other, rows in self.rows.items():
            if other == key or self.players.current_team.get(other) != team or len(rows) < 2:
                continue
            status = self.injuries.get((team, other))
            if status not in OUT:
                continue
            tgt = sum(_num(r, "target_share") for r in rows) / len(rows)
            car = sum(_num(r, "carries") / max(self.team_carries[(r["team"], int(r["week"]))], 1)
                      for r in rows) / len(rows)
            name = self.players.names.get(other, other)
            if "rushing" in col and car >= 0.3:
                out.append(f"{name} ({car:.0%} of carries) is {status}")
            elif col in ("receiving_yards", "receptions", "rushing_tds+receiving_tds") and tgt >= 0.15:
                out.append(f"{name} ({tgt:.0%} of targets) is {status}")
        return out

    def floor(self, b, key: str):
        """(text, +1 / -1 / 0): lowest output in the last 8 games and comfortable clears."""
        col = _stat_for(b.market)
        if not col or b.market == "player_anytime_td":
            return None
        logs = [r for s, r in self.players.games_played.get(key, []) if s in (self.season,
                                                                              self.season - 1)]
        logs = sorted(logs, key=lambda r: (int(r["season"]), int(r["week"])))[-8:]
        if len(logs) < 4:
            return None
        vals = [_num(r, col) for r in logs]
        line = b.point or 0
        margin = max(line * 1.2, line + 1)
        comfy = sum(v >= margin for v in vals)
        unit = col.replace("_", " ")
        text = (f"Floor: lowest {min(vals):.0f} {unit} in last {len(vals)}; cleared the line "
                f"comfortably in {comfy} of {len(vals)}")
        return text, (1 if min(vals) > line else -1 if comfy <= len(vals) // 3 else 0)

    def rest_travel(self, team: str, g: dict) -> list[str]:
        side = "home" if g["home_team"] == team else "away"
        notes = []
        try:
            rest = int(g.get(f"{side}_rest") or 7)
        except ValueError:
            rest = 7
        if rest <= 5:
            notes.append(f"short week ({rest} days' rest)")
        elif rest >= 13:
            notes.append("coming off a bye")
        home = self.home_stadium.get(team)
        if side == "away" and home in STADIUMS and g.get("stadium_id") in STADIUMS:
            zones = round(abs(STADIUMS[home][1] - STADIUMS[g["stadium_id"]][1]) / 15)
            if zones >= 2:
                notes.append(f"travels {zones} time zones")
        return notes

    def splits(self, team: str, g: dict) -> str | None:
        """The team's record at this week's venue (home or away), this season and last."""
        side = "home" if g["home_team"] == team else "away"
        w = n = 0
        for x in self.games:
            if x["home_score"] == "" or x["game_type"] != "REG":
                continue
            if x["season"] not in (str(self.season), str(self.season - 1)):
                continue
            if x[f"{side}_team"] != team:
                continue
            mine, theirs = int(x[f"{side}_score"]), int(x["away_score" if side == "home" else "home_score"])
            n += 1
            w += mine > theirs
        if n < 3:
            return None
        return f"{'Home' if side == 'home' else 'Away'} {w}-{n - w} the last two seasons"

    def qb(self, team: str, g: dict) -> str | None:
        """A warning when the usual starting QB isn't the one listed or is hurt."""
        starts = self.qb_starts.get(team)
        if not starts:
            return None
        usual = max(starts, key=starts.get)
        listed = g.get("home_qb_name" if g["home_team"] == team else "away_qb_name")
        status = self.injuries.get((team, name_key(usual)))
        if status in OUT + ("Questionable",):
            return f"{self.abbr_name.get(team, team)} QB {usual} is {status}"
        if listed and listed != usual:
            return f"{listed} expected to start at QB for {self.abbr_name.get(team, team)} (not {usual})"
        return None

    def weather_flag(self, b, g: dict) -> str | None:
        w = self.weather(g)
        if not w:
            return None
        bits = []
        if (w.get("wind") or 0) >= 15:
            bits.append(f"wind {w['wind']:.0f} mph")
        if (w.get("precip") or 0) >= 0.1:
            bits.append("rain/snow")
        if (w.get("temp") or 60) <= 25:
            bits.append(f"{w['temp']:.0f}°F")
        return ("Weather: " + ", ".join(bits)) if bits else None


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# ---------------------------------------------------------------- the tier

def h2h_warning(b, h2h, season: int) -> str | None:
    """A heads-up when a moneyline pick has lost most of its recent meetings with this
    opponent. It's a warning only: on 2015-2025 games, teams with a losing record
    against their opponent won as often as the betting market said, so it doesn't move
    the chance."""
    if b.market != "h2h" or not h2h or h2h.games < 2:
        return None
    losses = sum(" L " in r for r in h2h.results)
    if losses <= h2h.hits:
        return None
    opp = next((t.strip() for t in b.game.split(" @ ") if t.strip() != b.pick), "opponent")
    return (f"Head-to-head: lost {losses} of {h2h.games} meetings with the {opp} since "
            f"{season - hitrate.H2H_SEASONS + 1} (a heads-up only; past seasons show it "
            "doesn't change the chance)")


def analyze(b, ctx: Context, rates: dict | None = None) -> Insight:
    """All the signals for one NFL bet, and its tier."""
    ins = Insight()
    g = ctx.game_row(b)
    team = ctx.team_of(b)
    is_prop = b.market.startswith("player_")
    key = name_key(b.pick.rsplit(" ", 1)[0]) if is_prop else None
    score = 0

    # Track record (this season, or last season while this one is short).
    rates = rates if rates is not None else hitrate.rates(b, ctx.players, ctx.games, ctx.season)
    warning = h2h_warning(b, (rates or {}).get("h2h"), ctx.season)
    if warning:
        ins.flags.append(warning)
    now, last = (rates or {}).get(ctx.season), (rates or {}).get(ctx.season - 1)
    base = now if now and now.games >= 3 else last if last and last.games >= 6 else None
    if base:  # judged against the bet's own chance: a 55% bet that hit 50% is fine
        if base.rate >= max(0.6, b.fair_prob - 0.05):
            score += 1
            ins.reasons.append("strong record")
        elif base.rate < b.fair_prob - 0.15:
            score -= 1
            ins.reasons.append("weak record")
    if not (now and now.games >= 4) and not (last and last.games >= 8):
        ins.small_sample = True
        ins.flags.append("Small sample: only a few games to go on")
    if b.market == "player_anytime_td":
        ins.flags.append("Touchdowns are streaky: records swing a lot")

    if g and is_prop and team:
        m = ctx.matchup(b, key, team, g)
        if m:
            ins.notes.append(m[0])
            score += m[1]
            if m[1]:
                ins.reasons.append("good matchup" if m[1] > 0 else "tough matchup")
        u = ctx.usage(b, key, team)
        if u:
            ins.notes.append(u[0])
            score += u[1]
            if u[1]:
                ins.reasons.append("usage rising" if u[1] > 0 else "usage falling")
        f = ctx.floor(b, key)
        if f:
            ins.notes.append(f[0])
            score += f[1]
            if f[1] > 0:
                ins.reasons.append("high floor")
        mates = ctx.teammates(key, team, b)
        if mates:
            ins.notes.append("More volume likely: " + "; ".join(mates))
            score += 1
            ins.reasons.append("teammate out")
    if g and team and not is_prop:
        split = ctx.splits(team, g)
        if split:
            ins.notes.append(split)
    if g:
        teams = [team] if team else [g["home_team"], g["away_team"]]
        for t in teams:
            rt = ctx.rest_travel(t, g)
            if rt:
                ins.flags.append(f"{ctx.abbr_name.get(t, t)}: " + ", ".join(rt))
                if any("short week" in x for x in rt) and any("time zones" in x for x in rt):
                    score -= 1
            q = ctx.qb(t, g)
            if q:
                ins.flags.append(q)
                if is_prop or b.market in ("h2h", "spreads", "alternate_spreads") or \
                        "team_totals" in b.market or b.pick == "Over":
                    score -= 1
                    ins.reasons.append("QB change")
        wf = ctx.weather_flag(b, g)
        if wf:
            ins.flags.append(wf)
            passing = _stat_for(b.market) in ("passing_yards", "passing_tds", "receiving_yards",
                                              "receptions") or b.pick == "Over"
            if passing:
                score -= 1
                ins.reasons.append("bad weather")

    ins.score = score
    from . import learn, signals

    group = learn.group_of_bet(b, "nfl")
    if signals.tested(group):
        # Tiers from the weights learned on past seasons, not hand-set points: Strong or
        # Weak only when tested signals move the chance by 2 points or more.
        ins.reasons = []
        # A bet at FanDuel's real price keeps the market's chance: the signals were tested
        # on the model's chances, not the market's (which already prices them in), and
        # moving a market chance would make edges that aren't there.
        if signals.saved().get(group) and is_prop and team and g and \
                not getattr(b, "on_fanduel", False):
            base = getattr(b, "signal_base", None) or b.fair_prob  # once per bet
            b.signal_base = base
            feats = ctx.signal_features(b, key, team, g)
            p, weights = signals.move(group, base, feats)
            learn.reprice(b, p)
            ins.shift = p - base
            if abs(ins.shift) >= 0.005:
                ins.reasons = signals.reasons(weights, feats)
        moved = getattr(ins, "shift", 0.0)
        if moved >= 0.02 and not ins.small_sample:
            ins.tier = GREEN
        elif moved <= -0.02:
            ins.tier = RED
        else:
            ins.tier = YELLOW
        return ins
    if score >= 2 and not ins.small_sample and not any("QB" in r for r in ins.reasons):
        ins.tier = GREEN
    elif score <= -1:
        ins.tier = RED
    else:
        ins.tier = YELLOW
    return ins


# ---------------------------------------------------------------- building it

_CACHE: dict = {}
CACHE_SECONDS = 30 * 60


def context(season: int) -> Context | None:
    """The shared context for this season, rebuilt every 30 minutes."""
    hit = _CACHE.get(season)
    # Close to kickoff, injuries and the weather forecast are re-read every 5 minutes.
    from .board import LATE_MAX_AGE, starting_soon, upcoming_nfl

    try:
        late = bool(starting_soon(upcoming_nfl()))
    except Exception:
        late = False
    if hit and time.monotonic() - hit[0] < (LATE_MAX_AGE if late else CACHE_SECONDS):
        return hit[1]
    from .board import espn_official
    from .extras import load_player_model
    from .weather import kickoff_forecast

    games = data.games(None)
    players = load_player_model(season, games)
    try:
        injuries = {k: v["status"] for k, v in espn_official().items()}
    except Exception:
        injuries = {}
    snaps = data.release("snap_counts", season, season)
    ctx = Context(season, games, players, injuries, snaps, kickoff_forecast)
    _CACHE[season] = (time.monotonic(), ctx)
    return ctx


def annotate(bets: list, sport: str, season: int) -> Context | None:
    """Attach `insight` (and `tier`) to each NFL bet; returns the context used."""
    if sport != "nfl" or not bets:
        return None
    try:
        ctx = context(season)
    except Exception:
        return None
    for b in bets:
        try:
            b.insight = analyze(b, ctx)
        except Exception:
            b.insight = Insight()
        b.tier = b.insight.tier
    return ctx


def parlay_pool(bets: list, needed: int = 12) -> tuple[list, str]:
    """(bets to build parlays from, note): green legs only when there are enough of
    them across games, else green and yellow; red legs never."""
    green = [b for b in bets if getattr(b, "tier", YELLOW) == GREEN]
    if len({b.game for b in green}) >= 4 and len(green) >= needed:
        return green, "Parlays use only Strong (green) legs."
    usable = [b for b in bets if getattr(b, "tier", YELLOW) != RED]
    return usable, ("Not enough Strong (green) legs this week, so parlays also use Mixed "
                    "(yellow) ones; Weak (red) legs are left out.")


TYPE_NAMES = {"moneyline": "Moneylines", "spread": "Spreads", "total": "Game totals",
              "team_total": "Team totals"}


def bet_type_name(leg: dict) -> str:
    """A settled leg's bet type for the report card, e.g. 'Receiving yards'."""
    if leg.get("type") == "player_stat":
        return str(leg.get("stat", "Player props")).replace("_", " ").capitalize()
    return TYPE_NAMES.get(leg.get("type"), "Other")


def report_card(entries: list[dict]) -> dict[str, list[tuple]]:
    """How the model did on settled bets: predicted vs actual.

    {"type": rows, "tier": rows, "parlays": rows}, each row (name, won, settled,
    expected win share, actual win share), biggest groups first. Pushes and pending
    bets are left out; tiers count only bets saved since tiers began."""
    groups: dict[str, dict[str, list]] = {"type": {}, "tier": {}, "parlays": {}}
    for e in entries:
        if e.get("status") not in ("won", "lost") or not e.get("legs"):
            continue
        hit, prob = e["status"] == "won", e.get("win_prob") or 0.0
        if e.get("kind") == "single":
            leg = e["legs"][0]
            groups["type"].setdefault(bet_type_name(leg), []).append((hit, prob))
            if leg.get("tier") in TIER_WORDS:
                groups["tier"].setdefault(leg["tier"], []).append((hit, prob))
        else:
            tiers = [leg.get("tier") for leg in e["legs"]]
            name = (f"All {TIER_WORDS[GREEN]} legs" if tiers and all(t == GREEN for t in tiers)
                    else "Parlays")
            groups["parlays"].setdefault(name, []).append((hit, prob))
    out = {}
    for key, by in groups.items():
        rows = []
        for name, results in by.items():
            n = len(results)
            rows.append((TIER_WORDS.get(name, name) if key == "tier" else name,
                         sum(h for h, _ in results), n,
                         sum(p for _, p in results) / n, sum(h for h, _ in results) / n))
        if key == "tier":
            order = [TIER_WORDS[t] for t in (GREEN, YELLOW, RED)]
            rows.sort(key=lambda r: order.index(r[0]))
        else:
            rows.sort(key=lambda r: -r[2])
        out[key] = rows
    return out
