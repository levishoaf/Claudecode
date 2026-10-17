"""Fit the context factors and test whether they help, out of sample.

    python -m nfl_edge.backtest            # report only
    python -m nfl_edge.backtest --write    # also refit factors.json on all seasons

Factors are fit on the training seasons, then scored on later test seasons
they never saw, against the closing market line.
"""

from __future__ import annotations

import argparse
import json
import math

from . import research
from .factors import (
    FACTORS_FILE,
    MARGIN_TERMS,
    TOTAL_TERMS,
    Coefficients,
    margin_vector,
    ols,
    total_vector,
)
from .stats import MARGIN_SD, normal_cdf


def shrink(beta: list[float], se: list[float]) -> list[float]:
    """Pull each estimate toward 0 by its noise: b * b^2 / (b^2 + se^2).
    A clear effect is kept; one smaller than its error bar mostly vanishes."""
    return [b * b * b / (b * b + s * s) if b or s else 0.0 for b, s in zip(beta, se)]


def fit(records: list[research.Record]) -> tuple[Coefficients, dict]:
    m_rows = [margin_vector(r.features, r.base_margin) for r in records]
    m_beta, m_se = ols(m_rows, [r.margin - r.base_margin for r in records])
    t_rows = [total_vector(r.features) for r in records]
    t_beta, t_se = ols(t_rows, [r.total - r.base_total for r in records])
    m_beta, t_beta = shrink(m_beta, m_se), shrink(t_beta, t_se)
    coefs = Coefficients(
        {n: b for (n, _, _), b in zip(MARGIN_TERMS, m_beta)},
        {n: b for (n, _, _), b in zip(TOTAL_TERMS, t_beta)},
    )
    se = {"margin": {n: s for (n, _, _), s in zip(MARGIN_TERMS, m_se)},
          "total": {n: s for (n, _, _), s in zip(TOTAL_TERMS, t_se)}}
    return coefs, se


def adjusted(r: research.Record, c: Coefficients) -> tuple[float, float]:
    """(margin, total) after factor adjustments."""
    dm = sum(c.margin[n] * v for (n, _, _), v in zip(MARGIN_TERMS, margin_vector(r.features, r.base_margin)))
    dt = sum(c.total[n] * v for (n, _, _), v in zip(TOTAL_TERMS, total_vector(r.features)))
    return r.base_margin + dm, r.base_total + dt


def logloss(p: float, won: bool) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -math.log(p if won else 1 - p)


def evaluate(test: list[research.Record], coefs: Coefficients) -> None:
    games = [r for r in test if r.margin != 0]
    print(f"\nOut-of-sample test: {len(games)} games\n")

    def report(name: str, margin_fn, total_fn) -> None:
        ll = sum(logloss(normal_cdf(margin_fn(r) / MARGIN_SD), r.margin > 0) for r in games) / len(games)
        mae = sum(abs(r.margin - margin_fn(r)) for r in games) / len(games)
        tot = [r for r in games if r.total_line is not None]
        tmae = sum(abs(r.total - total_fn(r)) for r in tot) / len(tot)
        print(f"{name:<34} {ll:>9.4f} {mae:>11.2f} {tmae:>10.2f}")

    print(f"{'Prediction':<34} {'Win logloss':>9} {'Margin MAE':>11} {'Total MAE':>10}")
    report("Closing market line", lambda r: r.spread_line,
           lambda r: r.total_line)
    report("Team ratings only", lambda r: r.base_margin, lambda r: r.base_total)
    report("Ratings + all factors", lambda r: adjusted(r, coefs)[0], lambda r: adjusted(r, coefs)[1])
    for w in (0.1, 0.25):
        report(f"Market + {w:.0%} ratings+factors",
               lambda r, w=w: (1 - w * r.sample_weight) * r.spread_line + w * r.sample_weight * adjusted(r, coefs)[0],
               lambda r, w=w: (1 - w * r.sample_weight) * r.total_line + w * r.sample_weight * adjusted(r, coefs)[1])

    ats = [(adjusted(r, coefs)[0] > r.spread_line) == (r.margin > r.spread_line)
           for r in games if r.margin != r.spread_line]
    ou = [(adjusted(r, coefs)[1] > r.total_line) == (r.total > r.total_line)
          for r in games if r.total_line is not None and r.total != r.total_line]
    strong = [(adjusted(r, coefs)[0] > r.spread_line) == (r.margin > r.spread_line)
              for r in games if r.margin != r.spread_line and abs(adjusted(r, coefs)[0] - r.spread_line) >= 3]
    print(f"\nModel side vs closing spread: {sum(ats)}/{len(ats)} = {sum(ats) / len(ats):.1%}")
    print(f"  only when model differs by 3+: {sum(strong)}/{len(strong)} = "
          f"{sum(strong) / max(1, len(strong)):.1%}")
    print(f"Model side vs closing total:  {sum(ou)}/{len(ou)} = {sum(ou) / len(ou):.1%}")
    print("Break-even at -110 is 52.4%.")


def market_check(records: list[research.Record]) -> None:
    """Does the closing line already price each factor? Regress the market's
    own error on the factors; coefficients near 0 mean it does."""
    rows = [margin_vector(r.features, r.spread_line) for r in records]
    beta, se = ols(rows, [r.margin - r.spread_line for r in records])
    print("\nDoes the closing spread already price these in? (market error ~ factors)")
    for (name, label, _), b, s in zip(MARGIN_TERMS, beta, se):
        flag = "  <- possible mispricing" if abs(b) > 2 * s else ""
        print(f"  {label:<20} {b:+7.3f} +/- {s:.3f}{flag}")


def print_coefficients(coefs: Coefficients, se: dict) -> None:
    print("\nFitted effects after shrinkage (points; +/- one standard error of the raw fit)")
    print("  Margin (home minus away):")
    for name, label, _ in MARGIN_TERMS:
        print(f"    {name:<14} {coefs.margin[name]:+7.3f} +/- {se['margin'][name]:.3f}")
    print("  Total points:")
    for name, label, _ in TOTAL_TERMS:
        print(f"    {name:<14} {coefs.total[name]:+7.3f} +/- {se['total'][name]:.3f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", type=int, default=2015)
    p.add_argument("--split", type=int, default=2021, help="first test season")
    p.add_argument("--end", type=int, default=2025)
    p.add_argument("--stats-file", help="nflverse games.csv path or URL")
    p.add_argument("--data-dir", help="folder with injuries_YYYY.csv / snap_counts_YYYY.csv")
    p.add_argument("--write", action="store_true", help=f"refit on all seasons and save {FACTORS_FILE.name}")
    args = p.parse_args()

    print("Building walk-forward dataset...")
    records = research.build(range(args.start, args.end + 1), args.stats_file, args.data_dir,
                             current_season=args.end + 1)
    train = [r for r in records if r.season < args.split]
    test = [r for r in records if r.season >= args.split]

    coefs, se = fit(train)
    print(f"\nTrained on {args.start}-{args.split - 1} ({len(train)} games)")
    print_coefficients(coefs, se)
    evaluate(test, coefs)
    market_check(records)

    if args.write:
        coefs, se = fit(records)
        FACTORS_FILE.write_text(json.dumps({
            "fit_seasons": f"{args.start}-{args.end}",
            "games": len(records),
            "margin": {k: round(v, 4) for k, v in coefs.margin.items()},
            "total": {k: round(v, 4) for k, v in coefs.total.items()},
        }, indent=2) + "\n")
        print(f"\nWrote {FACTORS_FILE} (fit on all {len(records)} games)")


if __name__ == "__main__":
    main()
