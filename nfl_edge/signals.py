"""Which signals really predict NFL bets, and how much: weights learned from results.

    python -m nfl_edge.signals            # replay past seasons and report
    python -m nfl_edge.signals --write    # also save signal_weights.json

Every prop bet and moneyline of recent seasons is replayed with only what was
known before kickoff: the bet's chance (after the learned calibration) and its
signals (recent record at the line, usage trend, matchup, home or away, rest,
travel, a quarterback change, wind and cold). A logistic fit on the older
seasons says how much each signal moves the true chance beyond what the chance
already says; it's kept for a kind of bet only if it predicts the newest season
better than the chance alone. Signals that don't help get no weight.

The same functions compute the signals live (`nfl_edge/insights.py`), so the
weights mean the same thing on this week's bets as on the replay.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from .context import name_key
from .learn import apply as apply_curve, fit as fit_curve
from .venues import STADIUMS

WEIGHTS_FILE = Path(__file__).with_name("signal_weights.json")
RIDGE = 20.0  # pulls every weight toward 0 (no effect) unless the data insists

PROP_FEATURES = ["record", "trend", "comfy", "matchup", "home", "short_week", "travel",
                 "qb_change", "wind", "cold"]
ML_FEATURES = ["home", "short_week", "opp_short_week", "bye", "opp_bye", "travel",
               "qb_change", "opp_qb_change"]
LABELS = {"record": ("strong record", "weak record"),
          "trend": ("usage rising", "usage falling"),
          "comfy": ("high floor", "low floor"),
          "matchup": ("good matchup", "tough matchup"),
          "home": ("home game", "road game"),
          "short_week": ("", "short week"), "opp_short_week": ("opponent on a short week", ""),
          "bye": ("off a bye", ""), "opp_bye": ("", "opponent off a bye"),
          "travel": ("", "long travel"), "qb_change": ("", "QB change"),
          "opp_qb_change": ("opponent QB change", ""),
          "wind": ("", "wind"), "cold": ("", "cold")}
VOLUME = {"receiving_yards": "targets", "receptions": "targets",
          "rushing_yards": "carries", "passing_yards": "attempts", "passing_tds": "attempts",
          "rushing_tds+receiving_tds": "carries+targets"}


def logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x)) if x > -500 else 0.0


def _n(row: dict, col: str) -> float:
    try:
        return float(row.get(col) or 0)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- the signals

def player_games(pm, key: str, team: str) -> list[dict]:
    """His role games this season and (with the same team) last season, oldest first."""
    rows = [r for s, t, r in pm.logs.get(key, []) if s == pm.season or t == team]
    return sorted(rows, key=lambda r: (int(r.get("season") or pm.season), int(r["week"])))


def usual_qb(games: list[dict], team: str, season: int) -> str | None:
    """The team's most frequent starting QB in this season's finished games."""
    starts = Counter()
    for g in games:
        if g.get("season") != str(season) or not g.get("home_score"):
            continue
        for side in ("home", "away"):
            if g[f"{side}_team"] == team and g.get(f"{side}_qb_name"):
                starts[g[f"{side}_qb_name"]] += 1
    return starts.most_common(1)[0][0] if starts else None


def home_stadiums(games: list[dict]) -> dict[str, str]:
    seen: dict[str, Counter] = defaultdict(Counter)
    for g in games:
        if g.get("stadium_id") and g.get("location", "Home") == "Home":
            seen[g["home_team"]][g["stadium_id"]] += 1
    return {t: c.most_common(1)[0][0] for t, c in seen.items()}


def _rest(g: dict, side: str) -> int:
    try:
        return int(float(g.get(f"{side}_rest") or 7))
    except ValueError:
        return 7


def game_features(g: dict, team: str, stadiums: dict, games: list[dict], season: int,
                  qb_hurt: bool = False) -> dict:
    """Signals about the game itself, for `team`."""
    side = "home" if g["home_team"] == team else "away"
    other = "away" if side == "home" else "home"
    travel = 0.0
    home_st, st = stadiums.get(team), g.get("stadium_id")
    if side == "away" and home_st in STADIUMS and st in STADIUMS:
        travel = 1.0 if abs(STADIUMS[home_st][1] - STADIUMS[st][1]) / 15 >= 1.5 else 0.0

    def qb_change(s, t):
        usual = usual_qb(games, t, season)
        listed = g.get(f"{s}_qb_name")
        return 1.0 if usual and listed and listed != usual else 0.0
    return {"home": 1.0 if side == "home" and g.get("location", "Home") == "Home" else
            0.0 if g.get("location") == "Neutral" else -1.0 if side == "away" else 0.0,
            "short_week": 1.0 if _rest(g, side) <= 5 else 0.0,
            "opp_short_week": 1.0 if _rest(g, other) <= 5 else 0.0,
            "bye": 1.0 if _rest(g, side) >= 13 else 0.0,
            "opp_bye": 1.0 if _rest(g, other) >= 13 else 0.0,
            "travel": travel,
            "qb_change": 1.0 if qb_hurt else qb_change(side, team),
            "opp_qb_change": qb_change(other, g[f"{other}_team"])}


def prop_features(pm, key: str, team: str, stat: str, k: float, p: float, defense,
                  opponent: str | None, game: dict, wind: float | None,
                  temp: float | None) -> dict:
    """A prop bet's signals: `k` is the number he needs (e.g. 50 for 50+ yards)."""
    rows = player_games(pm, key, team)
    vals = [sum(_n(r, c) for c in stat.split("+")) for r in rows]
    last8 = vals[-8:]
    n = len(last8)
    record = ((sum(v >= k for v in last8) / n - p) * n / (n + 4)) if n else 0.0
    comfy = (sum(v >= max(k * 1.2, k + 1) for v in last8) / n - 0.5) if n else 0.0
    vol = [sum(_n(r, c) for c in VOLUME.get(stat, "targets").split("+")) for r in rows]
    trend = 0.0
    if len(vol) >= 4 and sum(vol) > 0:
        trend = max(-1.0, min(1.0, (sum(vol[-3:]) / 3) / (sum(vol) / len(vol)) - 1))
    matchup = 0.0
    if defense is not None and opponent and stat != "rushing_tds+receiving_tds":
        matchup = math.log(max(defense(opponent, pm.position.get(key, ""), stat), 1e-3))
    feats = {"record": record, "trend": trend, "comfy": comfy, "matchup": matchup,
             "wind": 1.0 if (wind or 0) >= 15 else 0.0,
             "cold": 1.0 if temp is not None and temp <= 25 else 0.0}
    feats.update({f: game.get(f, 0.0) for f in ("home", "short_week", "travel", "qb_change")})
    return feats


# ---------------------------------------------------------------- the weights

def fit_weights(rows: list[tuple[float, dict, bool]], names: list[str],
                ridge: float = RIDGE) -> dict[str, float]:
    """Logistic weights for `names` on top of each row's chance (an offset)."""
    w = [0.0] * len(names)
    xs = [([f.get(n, 0.0) for n in names], logit(p), 1.0 if y else 0.0) for p, f, y in rows]
    for _ in range(30):
        g = [ridge * wi for wi in w]
        h = [[ridge if i == j else 0.0 for j in range(len(w))] for i in range(len(w))]
        for x, off, y in xs:
            q = sigmoid(off + sum(a * b for a, b in zip(w, x)))
            r, v = q - y, q * (1 - q)
            for i in range(len(w)):
                if x[i]:
                    g[i] += r * x[i]
                    for j in range(len(w)):
                        if x[j]:
                            h[i][j] += v * x[i] * x[j]
        step = _solve(h, g)
        w = [a - b for a, b in zip(w, step)]
        if max(abs(s) for s in step) < 1e-7:
            break
    return {n: round(v, 4) for n, v in zip(names, w)}


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def shift(weights: dict[str, float], feats: dict) -> float:
    """How far the signals move the chance, in log-odds."""
    return sum(w * feats.get(n, 0.0) for n, w in weights.items())


def loss(rows, weights=None) -> float:
    tot = 0.0
    for p, f, y in rows:
        q = sigmoid(logit(p) + (shift(weights, f) if weights else 0.0))
        q = min(max(q, 1e-6), 1 - 1e-6)
        tot -= math.log(q if y else 1 - q)
    return tot / len(rows)


_saved: dict | None = None


def _file() -> dict:
    global _saved
    if _saved is None:
        try:
            _saved = json.loads(WEIGHTS_FILE.read_text())
        except (OSError, ValueError):
            _saved = {}
    return _saved


def saved() -> dict:
    """{group: {feature: weight}} for the kinds of bet whose signals passed the test."""
    return _file().get("groups", {})


def tested(group: str) -> bool:
    """Whether this kind of bet was tested (its signals kept or found not to help)."""
    return group in _file().get("tested", [])


def reasons(weights: dict, feats: dict, limit: int = 3) -> list[str]:
    """The signals moving this bet most, in words."""
    parts = sorted(((w * feats.get(n, 0.0), n) for n, w in weights.items()),
                   key=lambda x: -abs(x[0]))
    out = []
    for v, n in parts:
        if abs(v) < 0.03 or len(out) >= limit:
            continue
        word = LABELS.get(n, (n, n))[0 if v > 0 else 1]
        if word:
            out.append(word)
    return out


# ---------------------------------------------------------------- replaying the past

def replay(seasons: list[int]) -> dict[int, dict[str, list]]:
    """{season: {group: [(calibrated-ready chance, features, won)]}} for props and
    moneylines, built exactly as the live board builds them."""
    from . import data, learn
    from .odds import devig_power, implied_probability

    games = data.games(None)
    stadiums = home_stadiums([g for g in games if int(g["season"]) >= min(seasons) - 1])
    collect: list = []
    learn.replay_props(seasons, games, collect=collect)
    out: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    gcache: dict = {}
    for s, group, p, won, ctx in collect:
        pm, key, team, stat, k, defense, opp, g = ctx
        gk = (g["game_id"], team)
        if gk not in gcache:
            gcache[gk] = game_features(g, team, stadiums, ctx_games(games, s, int(g["week"])), s)
        gf = gcache[gk]
        f = prop_features(pm, key, team, stat, k, p, defense, opp, gf,
                          _float(g.get("wind")), _float(g.get("temp")))
        out[s][group].append((p, f, won))
    for g in games:
        s = int(g["season"])
        if s not in seasons or g["game_type"] != "REG" or not g["home_score"]:
            continue
        try:
            ph, pa = devig_power([implied_probability(int(g["home_moneyline"])),
                                  implied_probability(int(g["away_moneyline"]))])
        except (ValueError, TypeError):
            continue
        margin = int(g["result"])
        if not margin:
            continue
        before = ctx_games(games, s, int(g["week"]))
        for team, p, won in ((g["home_team"], ph, margin > 0), (g["away_team"], pa, margin < 0)):
            out[s]["nfl:moneyline"].append((p, game_features(g, team, stadiums, before, s), won))
    return out


_BEFORE: dict = {}


def ctx_games(games, season: int, week: int) -> list[dict]:
    """This season's games before `week` (cached for the replay)."""
    k = (id(games), season, week)
    if k not in _BEFORE:
        _BEFORE[k] = [g for g in games if g["season"] == str(season) and int(g["week"]) < week]
    return _BEFORE[k]


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build(seasons: list[int]) -> tuple[dict, list[str]]:
    data_ = replay(seasons)
    newest = max(seasons)
    groups, report = {}, []
    for group in sorted({g for v in data_.values() for g in v}):
        names = ML_FEATURES if group.endswith("moneyline") else PROP_FEATURES
        train = [x for s in seasons if s != newest for x in data_[s].get(group, [])]
        test = data_[newest].get(group, [])
        if not train or not test:
            continue
        # Score signals on top of the chance as it's shown live: calibrated only for
        # the kinds of bet the learned calibration covers (learn.py).
        from . import learn

        calibrated = group in learn.saved().get("groups", {})
        curve = fit_curve([(p, y, 1.0) for p, _, y in train]) if calibrated else None
        cal = lambda rows: [(apply_curve(curve, p), f, y) for p, f, y in rows]  # noqa: E731
        w = fit_weights(cal(train), names)
        before, after = loss(cal(test)), loss(cal(test), w)
        keep = after < before - 3e-4  # a clear gain, not noise
        report.append(f"{group:<36} {len(train) + len(test):>6} bets  {newest}: log loss "
                      f"{before:.4f} -> {after:.4f}  {'kept' if keep else 'not kept'}  "
                      + ", ".join(f"{n} {v:+.2f}" for n, v in w.items() if abs(v) >= 0.02))
        if keep:
            every = train + test
            curve = fit_curve([(p, y, 1.0) for p, _, y in every]) if calibrated else None
            groups[group] = fit_weights([(apply_curve(curve, p), f, y) for p, f, y in every],
                                        names)
    tested = sorted({g for v in data_.values() for g in v})
    return {"seasons": seasons, "tested": tested, "groups": groups}, report


def main() -> None:
    from datetime import date

    from .board import season_for

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="save signal_weights.json")
    ap.add_argument("--years", type=int, default=3)
    a = ap.parse_args()
    now = season_for(date.today())
    built, report = build(list(range(now - a.years, now)))
    print("\n".join(report))
    if a.write:
        WEIGHTS_FILE.write_text(json.dumps(built, indent=1) + "\n")
        print(f"saved {WEIGHTS_FILE}")


if __name__ == "__main__":
    main()
