"""Test the college model against closing lines, out of sample.

    python -m nfl_edge.cfb_backtest

Settings are chosen on the training seasons, then scored on the test
season against the consensus closing line (average across books).
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from statistics import mean

from . import cfb, data
from .stats import normal_cdf


def market_lines(seasons: set[int]) -> dict[str, tuple[float, float | None]]:
    """game_id -> (home margin implied by spread, total) averaged across books."""
    spreads: dict[str, list[float]] = defaultdict(list)
    totals: dict[str, list[float]] = defaultdict(list)
    for r in data.read_csv(cfb.LINES_URL, permanent=True):
        try:
            season = int(float(r["season"]))
            line = float(r["lines"])
        except ValueError:
            continue
        if season not in seasons:
            continue
        home = r["game_desc"].split("@")[-1]
        if r["market_type"] == "spread" and r["abbr"] == home:
            spreads[r["game_id"]].append(-line)
        elif r["market_type"] == "total" and r["abbr"] == "over":
            totals[r["game_id"]].append(line)
    return {g: (mean(v), mean(totals[g]) if totals.get(g) else None) for g, v in spreads.items()}


def walk_forward(season_rows: dict[int, list[dict]], lines, ridge: float, blend: float):
    """[(model margin, model total, market margin, market total, margin, total, weight)]"""
    out = []
    for rows in season_rows.values():
        regular = [r for r in rows if r["season_type"] == "regular"]
        weeks = sorted({int(r["week"]) for r in regular})
        for week in weeks:
            if week < 3:
                continue
            before = [r for r in regular if int(r["week"]) < week]
            tests = [r for r in cfb.completed(regular) if int(r["week"]) == week
                     and r["game_id"] in lines and r["home_division"] == "fbs"
                     and r["away_division"] == "fbs"]
            if not tests:
                continue
            model = cfb.CfbModel(before, ridge=ridge, elo_blend=blend)
            for r in tests:
                hp, ap, _ = model.predict_row(r)
                mkt_margin, mkt_total = lines[r["game_id"]]
                h, a = int(float(r["home_points"])), int(float(r["away_points"]))
                played = min(model.ratings.games_played.get(cfb.team_key(r, s), 0)
                             for s in ("home", "away"))
                out.append((hp - ap, hp + ap, mkt_margin, mkt_total, h - a, h + a,
                            min(1.0, played / 6)))
    return out


def logloss(margin_pred: float, margin: int) -> float:
    p = min(max(normal_cdf(margin_pred / cfb.MARGIN_SD), 1e-9), 1 - 1e-9)
    return -math.log(p if margin > 0 else 1 - p)


def score(recs, fn):
    games = [r for r in recs if r[4] != 0]
    ll = mean(logloss(fn(r)[0], r[4]) for r in games)
    mae = mean(abs(r[4] - fn(r)[0]) for r in games)
    tot = [r for r in games if r[3] is not None]
    tmae = mean(abs(r[5] - fn(r)[1]) for r in tot)
    return ll, mae, tmae


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--train", default="2023,2024")
    p.add_argument("--test", type=int, default=2025)
    args = p.parse_args()
    train = [int(s) for s in args.train.split(",")]
    seasons = set(train) | {args.test}
    print("Loading schedules and lines...")
    rows = {s: cfb.load_schedule(s, current_season=args.test + 1) for s in sorted(seasons)}
    lines = market_lines(seasons)

    print("\nChoosing settings on", ", ".join(map(str, train)))
    best = None
    for ridge in (2.0, 4.0, 8.0):
        for blend in (0.0, 0.5, 1.0):
            recs = walk_forward({s: rows[s] for s in train}, lines, ridge, blend)
            ll, mae, _ = score(recs, lambda r: (r[0], r[1]))
            print(f"  prior strength {ridge:>3.0f} games, Elo blend {blend:.1f}: "
                  f"log loss {ll:.4f}, margin MAE {mae:.2f}")
            if best is None or ll < best[0]:
                best = (ll, ridge, blend)
    _, ridge, blend = best
    print(f"Best: prior strength {ridge:.0f}, Elo blend {blend:.1f}")

    test = walk_forward({args.test: rows[args.test]}, lines, ridge, blend)
    print(f"\nOut-of-sample test: {args.test}, {len(test)} FBS games (weeks 3+)\n")
    print(f"{'Prediction':<30} {'Win logloss':>11} {'Margin MAE':>11} {'Total MAE':>10}")
    rows_out = [
        ("Closing consensus line", lambda r: (r[2], r[3] if r[3] is not None else r[1])),
        ("College model", lambda r: (r[0], r[1])),
        ("Market + 10% model", lambda r: (0.9 * r[2] + 0.1 * r[0],
                                          0.9 * (r[3] or r[1]) + 0.1 * r[1])),
    ]
    for name, fn in rows_out:
        ll, mae, tmae = score(test, fn)
        print(f"{name:<30} {ll:>11.4f} {mae:>11.2f} {tmae:>10.2f}")

    ats = [(r[0] > r[2]) == (r[4] > r[2]) for r in test if r[4] != r[2]]
    big = [(r[0] > r[2]) == (r[4] > r[2]) for r in test if r[4] != r[2] and abs(r[0] - r[2]) >= 5]
    ou = [(r[1] > r[3]) == (r[5] > r[3]) for r in test if r[3] is not None and r[5] != r[3]]
    print(f"\nModel side vs closing spread: {sum(ats)}/{len(ats)} = {sum(ats) / len(ats):.1%}")
    print(f"  only when model differs by 5+: {sum(big)}/{len(big)} = {sum(big) / max(1, len(big)):.1%}")
    print(f"Model side vs closing total:  {sum(ou)}/{len(ou)} = {sum(ou) / len(ou):.1%}")
    print("Break-even at -110 is 52.4%.")
    resid = [r[5] - r[1] for r in test]
    print(f"\n(Total prediction error SD: {math.sqrt(mean(e * e for e in resid)):.1f})")


if __name__ == "__main__":
    main()
