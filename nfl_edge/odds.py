"""Odds conversion, vig removal and bet sizing math."""

from __future__ import annotations


def american_to_decimal(american: float) -> float:
    if american == 0:
        raise ValueError("American odds cannot be 0")
    if american > 0:
        return 1 + american / 100
    return 1 + 100 / -american


def decimal_to_american(decimal: float) -> int:
    if decimal <= 1:
        raise ValueError("Decimal odds must be greater than 1")
    if decimal >= 2:
        return round((decimal - 1) * 100)
    return round(-100 / (decimal - 1))


def implied_probability(american: float) -> float:
    return 1 / american_to_decimal(american)


def devig_multiplicative(implied: list[float]) -> list[float]:
    """Scale implied probabilities proportionally so they sum to 1."""
    total = sum(implied)
    return [p / total for p in implied]


def devig_power(implied: list[float], tol: float = 1e-10) -> list[float]:
    """Find k such that sum(p_i ** k) == 1.

    Shifts more of the vig onto longshots than the multiplicative method,
    which better matches how books actually shade prices.
    """
    if abs(sum(implied) - 1) < tol:
        return list(implied)
    lo, hi = 0.01, 10.0
    for _ in range(200):
        k = (lo + hi) / 2
        total = sum(p ** k for p in implied)
        if abs(total - 1) < tol:
            break
        # p < 1, so raising k shrinks the sum.
        if total > 1:
            lo = k
        else:
            hi = k
    return [p ** k for p in implied]


DEVIG_METHODS = {
    "multiplicative": devig_multiplicative,
    "power": devig_power,
}


def expected_value(fair_prob: float, american: float) -> float:
    """Expected profit per 1 unit staked."""
    return fair_prob * american_to_decimal(american) - 1


def kelly_fraction(fair_prob: float, american: float) -> float:
    """Full-Kelly fraction of bankroll; 0 when the bet has no edge."""
    b = american_to_decimal(american) - 1
    f = (b * fair_prob - (1 - fair_prob)) / b
    return max(f, 0.0)
