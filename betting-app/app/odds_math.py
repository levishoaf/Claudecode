"""Pure odds math: conversions, de-vigging, consensus, EV, Kelly, parlays.

Everything here is an *estimate* built from market prices. Nothing is guaranteed.
"""
from __future__ import annotations

from math import prod
from typing import Iterable, Sequence


# ---------- price conversions ----------
def american_to_decimal(american: float) -> float:
    if american == 0 or -100 < american < 100:
        raise ValueError(f"invalid American odds: {american}")
    return 1 + american / 100 if american > 0 else 1 + 100 / abs(american)


def decimal_to_american(decimal: float) -> int:
    if decimal <= 1:
        raise ValueError(f"decimal odds must be > 1: {decimal}")
    if decimal >= 2:
        return int(round((decimal - 1) * 100))
    return int(round(-100 / (decimal - 1)))


def implied_prob_from_decimal(decimal: float) -> float:
    return 1 / decimal


def implied_prob_from_american(american: float) -> float:
    return 1 / american_to_decimal(american)


# ---------- removing the vig ----------
def devig_proportional(implied: Sequence[float]) -> list[float]:
    """Normalise implied probabilities so they sum to 1 (multiplicative method)."""
    total = sum(implied)
    if total <= 0:
        raise ValueError("implied probabilities must sum to > 0")
    return [p / total for p in implied]


def devig_power(implied: Sequence[float]) -> list[float]:
    """Power method: find k with sum(p_i ** k) == 1. Shrinks longshots slightly more
    than the proportional method (accounts for favourite-longshot bias)."""
    total = sum(implied)
    if total <= 0:
        raise ValueError("implied probabilities must sum to > 0")
    if abs(total - 1) < 1e-12:
        return list(implied)
    lo, hi = (1.0, 50.0) if total > 1 else (0.01, 1.0)
    for _ in range(200):
        k = (lo + hi) / 2
        s = sum(p ** k for p in implied)
        if (s > 1) == (total > 1):
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    out = [p ** k for p in implied]
    return devig_proportional(out)  # tidy residual numerical error


DEVIG_METHODS = {"proportional": devig_proportional, "power": devig_power}


def devig(implied: Sequence[float], method: str = "power") -> list[float]:
    return DEVIG_METHODS[method](implied)


def weighted_consensus(vectors: Sequence[Sequence[float]], weights: Sequence[float]) -> list[float]:
    """Weighted mean of no-vig probability vectors (one vector per book), renormalised."""
    if not vectors or len(vectors) != len(weights):
        raise ValueError("need one weight per vector")
    n = len(vectors[0])
    wsum = sum(weights)
    if wsum <= 0:
        raise ValueError("weights must sum to > 0")
    avg = [sum(w * v[i] for v, w in zip(vectors, weights)) / wsum for i in range(n)]
    return devig_proportional(avg)


# ---------- value ----------
def edge(model_prob: float, decimal: float) -> float:
    """Model probability minus the price's implied probability (percentage points as a fraction)."""
    return model_prob - 1 / decimal


def expected_value(model_prob: float, decimal: float) -> float:
    """Expected profit per $1 staked."""
    return model_prob * decimal - 1


def kelly_full(model_prob: float, decimal: float) -> float:
    b = decimal - 1
    if b <= 0:
        return 0.0
    return max(0.0, (model_prob * decimal - 1) / b)


def kelly_stake(model_prob: float, decimal: float, fraction: float = 0.25, cap: float = 0.02) -> float:
    """Fractional Kelly as a share of bankroll, floored at 0 and capped."""
    return min(cap, kelly_full(model_prob, decimal) * fraction)


# ---------- parlays ----------
def parlay_decimal(decimals: Iterable[float]) -> float:
    return prod(decimals)


def parlay_prob(probs: Iterable[float]) -> float:
    """Joint win probability ASSUMING independent legs (use only for different games)."""
    return prod(probs)


def parlay_summary(legs: Sequence[tuple[float, float]]) -> dict:
    """legs = [(model_prob, decimal_odds), ...] -> combined stats (independence assumed)."""
    dec = parlay_decimal(d for _, d in legs)
    p = parlay_prob(pr for pr, _ in legs)
    return {
        "decimal": dec,
        "american": decimal_to_american(dec),
        "prob": p,
        "implied_prob": 1 / dec,
        "ev": expected_value(p, dec),
    }
