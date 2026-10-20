"""Teach the model from results, so its chances get more accurate over time.

    python -m nfl_edge.learn            # replay past seasons and report
    python -m nfl_edge.learn --write    # also save calibration.json

Two steps, each a correction to the model's win chance for one kind of bet
(NFL moneylines, receiving yards, anytime touchdowns, college moneylines, ...):

1. **Past seasons.** Every week of recent seasons is replayed with only the
   data known before kickoff: the same prop, line and college models price
   the bets, and the real results grade them. A curve fit to thousands of
   those bets says how far off each kind of bet's chances run (say anytime
   TDs at "40%" really hitting 33%). It is fit on the older seasons and
   checked on the newest before it's trusted, then saved to calibration.json.
2. **This season's graded bets.** Every pick the builder shows is graded
   after the games (Bet History). Those results nudge the curve again on
   each build, a little at first and more as results pile up, so the model
   keeps learning from how its own picks actually do.

A correction only moves a chance as far as the evidence supports: a few
results barely move it, a few hundred can.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

from . import data
from .odds import american_to_decimal, decimal_to_american, devig_power, implied_probability, kelly_fraction

CALIBRATION_FILE = Path(__file__).with_name("calibration.json")
PRIOR = 30.0   # bets' worth of "the model was right" each correction starts from
LIVE_PRIOR = 60.0  # this season's picks overlap (same players, same games): trust them slower
PARLAY_PRIOR = 100.0  # parlays share legs with each other: trust their results slowest
CLAMP = (0.01, 0.99)

GROUP_NAMES = {"moneyline": "Moneylines", "spread": "Spreads", "total": "Game totals",
               "team_total": "Team totals", "prop:passing_yards": "Passing yards",
               "prop:passing_tds": "Passing TDs", "prop:rushing_yards": "Rushing yards",
               "prop:receiving_yards": "Receiving yards", "prop:receptions": "Receptions",
               "prop:rushing_tds+receiving_tds": "Anytime TD"}


# ---------------------------------------------------------------- the curve

# A curve is a correction at each of these chances, straight lines in between:
# {"knots": KNOTS, "shift": [how much to add at each knot]}.
KNOTS = [round(0.025 + 0.05 * i, 3) for i in range(20)]
WIDTH = 0.075  # each knot learns from bets within this much of its chance


def fit(points: list[tuple[float, bool, float]], prior: float = PRIOR) -> dict:
    """The correction that best matches the results: at each knot, how much more (or
    less) often the bets near that chance won than the model said.

    points: (model chance, won, weight). Each knot's shift is pulled toward 0 by
    `prior` bets' worth of "the model was right", so little evidence means little
    change. The corrected chances never go down as the model's go up."""
    shift, weight = [], []
    for k in KNOTS:
        num = den = 0.0
        for p, won, w in points:
            d = abs(p - k)
            if d < WIDTH:
                kw = w * (1 - d / WIDTH)
                num += kw * ((1.0 if won else 0.0) - p)
                den += kw
        shift.append(num / (den + prior))
        weight.append(den + prior)
    values = _rising([k + d for k, d in zip(KNOTS, shift)], weight)
    return {"knots": KNOTS, "shift": [round(v - k, 4) for k, v in zip(KNOTS, values)]}


def _rising(values: list[float], weights: list[float]) -> list[float]:
    """The closest never-falling values (weighted pool-adjacent-violators)."""
    blocks: list[list[float]] = []  # [value, weight, count]
    for v, w in zip(values, weights):
        blocks.append([v, w, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    return [v for v, _, c in blocks for _ in range(c)]


def apply(curve: dict | None, p: float) -> float:
    if not curve:
        return p
    ks, ds = curve["knots"], curve["shift"]
    if p <= ks[0]:
        d = ds[0] * p / ks[0]  # fades to no change at 0% and 100%
    elif p >= ks[-1]:
        d = ds[-1] * (1 - p) / (1 - ks[-1])
    else:
        i = next(j for j in range(1, len(ks)) if p <= ks[j])
        t = (p - ks[i - 1]) / (ks[i] - ks[i - 1])
        d = ds[i - 1] + t * (ds[i] - ds[i - 1])
    return min(max(p + d, CLAMP[0]), CLAMP[1])


def logloss(points, curve=None) -> float:
    total = sum(w for _, _, w in points)
    return -sum(w * math.log(apply(curve, p) if won else 1 - apply(curve, p))
                for p, won, w in points) / total


# ---------------------------------------------------------------- kinds of bets

def group_of_bet(b, sport: str) -> str:
    m = b.market
    if sport == "ufc":  # named as graded UFC legs are (tracker: fight, distance)
        return f"ufc:{'fight' if m == 'h2h' else 'distance' if m == 'fight_distance' else m}"
    if m == "h2h":
        g = "moneyline"
    elif "spreads" in m:
        g = "spread"
    elif "team_totals" in m:
        g = "team_total"
    elif "totals" in m:
        g = "total"
    elif m.startswith("player_"):
        from .extras import PLAYER_MARKETS, base_market

        g = "prop:" + PLAYER_MARKETS[base_market(m)][0]
    else:
        g = m
    return f"{sport}:{g}"


def group_of_leg(leg: dict, sport: str) -> str:
    kind = leg.get("type", "")
    return f"{sport}:" + (f"prop:{leg.get('stat')}" if kind == "player_stat" else kind)


def group_name(group: str) -> str:
    if group.startswith("parlay:"):
        return "Parlays" + (" (college)" if group.endswith("ncaaf") else "")
    sport, _, kind = group.partition(":")
    return ("College " if sport == "ncaaf" else "") + GROUP_NAMES.get(kind, kind)


# ---------------------------------------------------------------- using it

_saved: dict | None = None
_live: dict[str, dict] = {}
_live_counts: dict[str, int] = {}


def saved() -> dict:
    """calibration.json's curves from the past-season replay ({} if there's none)."""
    global _saved
    if _saved is None:
        try:
            _saved = json.loads(CALIBRATION_FILE.read_text())
        except (OSError, ValueError):
            _saved = {}
    return _saved


def base_curve(group: str) -> dict | None:
    return saved().get("groups", {}).get(group)


def graded_legs(entries: list[dict]) -> dict[str, list[tuple[float, bool, float]]]:
    """{group: [(model chance before learning, won, weight)]} for every settled leg,
    each counted once however many picks it was part of."""
    seen, out = set(), defaultdict(list)
    for e in entries:
        for leg in e.get("legs", []):
            if leg.get("status") not in ("won", "lost") or leg.get("id") in seen:
                continue
            p = leg.get("raw_prob") or leg.get("win_prob")
            if not p:
                continue
            seen.add(leg.get("id"))
            out[group_of_leg(leg, e.get("sport", "nfl"))].append(
                (float(p), leg["status"] == "won", 1.0))
    return out


def remember(entries: list[dict]) -> None:
    """Learn from these graded picks (a ledger's entries) on top of the replay.

    Called before each build by the app, the web export and the Discord bot."""
    _live.clear()
    _live_counts.clear()
    for group, points in graded_legs(entries).items():
        base = base_curve(group)
        # Score each result against the replay-corrected chance, then fit how far
        # this season's results say that is still off.
        shifted = [(apply(base, p), won, w) for p, won, w in points]
        _live[group] = fit(shifted, LIVE_PRIOR)
        _live_counts[group] = len(points)
    for group, points in parlay_points(entries).items():  # after the legs' corrections
        _live[group] = fit(points, PARLAY_PRIOR)
        _live_counts[group] = len(points)


def parlay_points(entries: list[dict]) -> dict[str, list[tuple[float, bool, float]]]:
    """{"parlay:<sport>": [(chance now, won, weight)]} for every settled parlay: its
    chance worked out again from its legs' original chances with today's corrections,
    so old parlays are scored the way new ones are priced."""
    seen, out = set(), defaultdict(list)
    for e in entries:
        if e.get("kind") != "parlay" or e.get("status") not in ("won", "lost"):
            continue
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        sport = e.get("sport", "nfl")
        p = 1.0
        for leg in e["legs"]:
            raw = leg.get("raw_prob") or leg.get("win_prob") or 0.5
            p *= calibrated(float(raw), group_of_leg(leg, sport))
        out[f"parlay:{sport}"].append((p, e["status"] == "won", 1.0))
    return out


def parlay_prob(p: float, sport: str = "nfl") -> float:
    """A parlay's chance (its legs' chances multiplied), corrected by how this season's
    graded parlays did."""
    return apply(_live.get(f"parlay:{sport}"), p)


def calibrated(p: float, group: str) -> float:
    if group.startswith("parlay:"):
        return parlay_prob(p, group.split(":", 1)[1])
    return apply(_live.get(group), apply(base_curve(group), p))


def adjust(bets: list, sport: str) -> None:
    """Replace each bet's chance with the learned one (the original is kept as
    b.raw_prob), repricing break-even odds and expected value to match."""
    for b in bets:
        raw = getattr(b, "raw_prob", None) or b.fair_prob
        p = calibrated(raw, group_of_bet(b, sport))
        b.raw_prob = raw
        if abs(p - b.fair_prob) >= 1e-9:
            reprice(b, p)


def reprice(b, p: float) -> None:
    """Set a bet's chance, with break-even odds and expected value to match."""
    b.fair_prob = p
    if not b.priced:
        b.fd_price = decimal_to_american(1 / min(max(p, 0.01), 0.99))
    b.ev = p * american_to_decimal(b.fd_price) - 1
    b.kelly = kelly_fraction(p, b.fd_price)


def summary() -> list[tuple[str, int, int, float, float]]:
    """[(kind of bet, past bets replayed, this season's graded bets, model chance,
    learned chance)] at each kind's typical chance, biggest corrections first."""
    rows = []
    groups = set(saved().get("groups", {})) | set(_live)
    for g in groups:
        info = saved().get("groups", {}).get(g, {})
        typical = info.get("typical", 0.3 if g.startswith("parlay:") else 0.6)
        rows.append((group_name(g), info.get("n", 0), _live_counts.get(g, 0), typical,
                     calibrated(typical, g)))
    rows.sort(key=lambda r: -abs(r[4] - r[3]))
    return rows


# ---------------------------------------------------------------- replaying the past

def _between(p: float) -> bool:
    return 0.03 <= p <= 0.97


def replay_nfl_lines(seasons: list[int], games: list[dict]) -> dict[int, dict[str, list]]:
    """{season: {group: [(chance, won, 1)]}} for moneylines, alternate spreads and
    totals and team totals, priced the way the board prices them."""
    from .board import ALT_SPREAD_RANGE, ALT_TOTAL_RANGE, HALF_POINTS, TEAM_TOTAL_RANGE
    from .extras import LineDistribution
    from .stats import normal_cdf

    out: dict[int, dict[str, list]] = {}
    for s in seasons:
        dist = LineDistribution("nfl", [g for g in games if int(g["season"]) < s])
        got = defaultdict(list)
        for g in games:
            if (int(g["season"]) != s or g["game_type"] != "REG" or not g["home_score"]
                    or not g["spread_line"] or not g["total_line"]):
                continue
            margin, total = int(g["result"]), int(g["total"])
            sp, tot = float(g["spread_line"]), float(g["total_line"])
            try:
                ph, pa = devig_power([implied_probability(int(g["home_moneyline"])),
                                      implied_probability(int(g["away_moneyline"]))])
                if margin:
                    got["nfl:moneyline"] += [(ph, margin > 0, 1.0), (pa, margin < 0, 1.0)]
            except (ValueError, TypeError):
                pass
            for sign in (1, -1):  # home, away
                main = -sp if sign == 1 else sp
                for L in HALF_POINTS + [-x for x in HALF_POINTS]:
                    if abs(L - main) > ALT_SPREAD_RANGE:
                        continue
                    if sign == 1:
                        p = dist.outcome("margin", sp, -L)[0]
                    else:
                        p = 1 - sum(dist.outcome("margin", sp, L))
                    if _between(p):
                        got["nfl:spread"].append((p, sign * margin + L > 0, 1.0))
            for L in (x + 20 for x in HALF_POINTS):
                if abs(L - tot) <= ALT_TOTAL_RANGE:
                    p = dist.outcome("total", tot, L)[0]
                    if _between(p):
                        got["nfl:total"] += [(p, total > L, 1.0), (1 - p, total < L, 1.0)]
            for pts, scored in ((tot / 2 + sp / 2, int(g["home_score"])),
                                (tot / 2 - sp / 2, int(g["away_score"]))):
                for L in HALF_POINTS:
                    if abs(L - pts) <= TEAM_TOTAL_RANGE:
                        p = 1 - normal_cdf((L - pts) / 9.1)
                        if _between(p):
                            got["nfl:team_total"].append((p, scored > L, 1.0))
        out[s] = got
    return out


def replay_props(seasons: list[int], games: list[dict],
                 collect: list | None = None) -> dict[int, dict[str, list]]:
    """{season: {group: [(chance, won, 1)]}}: each week's prop ladders priced from
    the games before it, graded by what the player actually did. With `collect`, each
    bet's context is also appended there (for nfl_edge/signals.py)."""
    from .board import prop_chances
    from .context import name_key
    from .extras import STATS_URL, PlayerModel, _n, defense_table

    stats = {}
    for s in sorted({x for s in seasons for x in (s - 1, s)}):
        stats[s] = [r for r in data.read_csv(STATS_URL.format(season=s), permanent=True)
                    if r.get("season_type") == "REG"]
    out: dict[int, dict[str, list]] = {}
    for s in seasons:
        got = defaultdict(list)
        weeks = sorted({int(r["week"]) for r in stats[s]})
        for week in weeks:
            if week < 3:
                continue
            past = [g for g in games if int(g["season"]) < s or
                    (int(g["season"]) == s and int(g["week"]) < week)]
            this = [g for g in games if int(g["season"]) == s and int(g["week"]) == week
                    and g["game_type"] == "REG" and g["spread_line"] and g["total_line"]]
            pm = PlayerModel(s, past, {s - 1: stats[s - 1],
                                       s: [r for r in stats[s] if int(r["week"]) < week]})
            implied, opponent, favored, game_of = {}, {}, {}, {}
            for g in this:
                game_of[g["home_team"]] = game_of[g["away_team"]] = g
                tot, sp = float(g["total_line"]), float(g["spread_line"])
                implied[g["home_team"]], implied[g["away_team"]] = tot / 2 + sp / 2, tot / 2 - sp / 2
                opponent[g["home_team"]], opponent[g["away_team"]] = g["away_team"], g["home_team"]
                favored[g["home_team"]], favored[g["away_team"]] = sp, -sp
            defense = defense_table([r for r in stats[s] if int(r["week"]) < week])
            pts, latest = {}, {}
            for g in past:
                if g["game_type"] != "REG" or not g["home_score"] or int(g["season"]) < s - 1:
                    continue
                w = 2.0 if int(g["season"]) == s else 1.0
                for side in ("home", "away"):
                    t = g[f"{side}_team"]
                    tw = pts.setdefault(t, [0.0, 0.0])
                    tw[0] += w * int(g[f"{side}_score"])
                    tw[1] += w
                    if int(g["season"]) == s:
                        latest[t] = max(latest.get(t, 0), int(g["week"]))
            for r in stats[s]:
                if int(r["week"]) != week:
                    continue
                key, team = name_key(r["player_display_name"]), r["team"]
                if pm.current_team.get(key) != team or team not in implied or team not in pts:
                    continue
                if (team, str(latest.get(team, 0))) not in pm.appearances.get((key, s), set()):
                    continue  # the board only uses players who played the last game
                factor = implied[team] / (pts[team][0] / pts[team][1])
                for p, _, k, stat in prop_chances(pm, key, factor, None, False, opponent.get(team),
                                                  defense, favored.get(team, 0.0)):
                    if _between(p):
                        actual = sum(_n(r, c) for c in stat.split("+"))
                        got["nfl:prop:" + stat].append((p, actual >= k, 1.0))
                        if collect is not None:  # for fitting the signals (signals.py)
                            collect.append((s, "nfl:prop:" + stat, p, actual >= k,
                                            (pm, key, team, stat, k, defense,
                                             opponent.get(team), game_of[team])))
        out[s] = got
    return out


def replay_college(seasons: list[int]) -> dict[int, dict[str, list]]:
    """{season: {"ncaaf:moneyline": [(chance, won, 1)]}}: the market line (where there
    is one) blended with the college model fit on each season's earlier weeks."""
    from . import cfb
    from .stats import normal_cdf

    lines = cfb.history_lines(set(seasons))  # blended in as on the live board
    out: dict[int, dict[str, list]] = {}
    for s in seasons:
        try:
            rows = cfb.load_schedule(s, current_season=s + 1)
        except data.DataError:
            continue
        got = []
        regular = [r for r in rows if r.get("season_type", "regular") == "regular"]
        for week in sorted({int(r["week"]) for r in regular}):
            if week < 3:
                continue
            before = [r for r in regular if int(r["week"]) < week]
            tests = [r for r in cfb.completed(regular) if int(r["week"]) == week]
            if not tests:
                continue
            model = cfb.CfbModel(before)
            for r in tests:
                hp, ap, _ = model.predict_row(r)
                p = normal_cdf((hp - ap) / cfb.MARGIN_SD)
                if r["game_id"] in lines:
                    p = cfb.MARKET_WEIGHT * lines[r["game_id"]] + (1 - cfb.MARKET_WEIGHT) * p
                margin = float(r["home_points"]) - float(r["away_points"])
                if margin and _between(p):
                    got += [(p, margin > 0, 1.0), (1 - p, margin < 0, 1.0)]
        out[s] = {"ncaaf:moneyline": got}
    return out


def build(seasons: list[int], college_seasons: list[int]) -> tuple[dict, list[str]]:
    """Fit every kind of bet's curve on the replay; check it on the newest season.

    Returns (calibration.json's contents, report lines)."""
    games = data.games(None)
    by_season: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for part in (replay_nfl_lines(seasons, games), replay_props(seasons, games),
                 replay_college(college_seasons)):
        for s, groups in part.items():
            for g, pts in groups.items():
                by_season[s][g] += pts
    groups, report = {}, []
    names = sorted({g for v in by_season.values() for g in v})
    for g in names:
        seasons_with = sorted(s for s in by_season if by_season[s].get(g))
        newest = seasons_with[-1]
        train = [p for s in seasons_with[:-1] for p in by_season[s][g]]
        test = by_season[newest][g]
        line = f"{group_name(g):<22} {len(train) + len(test):>7} bets"
        keep = True
        if train and test:
            curve = fit(train)
            before, after = logloss(test), logloss(test, curve)
            keep = after <= before
            avg_p = sum(p for p, _, _ in test) / len(test)
            hit = sum(won for _, won, _ in test) / len(test)
            line += (f"  {newest}: model said {avg_p:.1%}, won {hit:.1%}; "
                     f"log loss {before:.4f} -> {after:.4f}" + ("" if keep else "  (not kept)"))
        report.append(line)
        everything = train + test
        if keep:
            shown = sorted(p for p, _, _ in everything if p >= 0.5) or [0.6]
            groups[g] = dict(fit(everything), n=len(everything),
                             typical=round(shown[len(shown) // 2], 3))
    return ({"built": date.today().isoformat(), "seasons": seasons,
             "college_seasons": college_seasons, "groups": groups}, report)


def main() -> None:
    from .board import season_for

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="save calibration.json")
    ap.add_argument("--years", type=int, default=3, help="past seasons to replay")
    args = ap.parse_args()
    now = season_for(date.today())
    seasons = list(range(now - args.years, now))
    built, report = build(seasons, seasons)
    print("\n".join(report))
    for g, c in sorted(built["groups"].items()):
        t = c["typical"]
        print(f"  {group_name(g):<22} " + "  ".join(
            f"{x:.0%}->{apply(c, x):.0%}" for x in (0.55, 0.65, 0.75, 0.85, 0.95)))
    if args.write:
        CALIBRATION_FILE.write_text(json.dumps(built, indent=1) + "\n")
        print(f"saved {CALIBRATION_FILE}")


if __name__ == "__main__":
    main()
