"""Turn game context into point adjustments, and fit how big each one is."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .context import Features

FACTORS_FILE = Path(__file__).with_name("factors.json")

# Each term: (name, label shown to users, function of (features, base margin)).
MARGIN_TERMS = [
    ("home_field", "home field", lambda f, m: f.home_field),
    ("travel_diff", "travel", lambda f, m: f.travel_diff),
    ("div_shrink", "divisional game", lambda f, m: f.div_game * m),
    ("off_inj_diff", "offensive injuries", lambda f, m: f.off_inj_home - f.off_inj_away),
    ("def_inj_diff", "defensive injuries", lambda f, m: f.def_inj_home - f.def_inj_away),
    ("qb_diff", "starting QB out", lambda f, m: f.qb_out_home - f.qb_out_away),
]
TOTAL_TERMS = [
    ("intercept", "scoring environment", lambda f: 1.0),
    ("wind", "wind", lambda f: f.wind_excess),
    ("cold", "cold", lambda f: f.cold),
    ("indoor", "dome", lambda f: f.indoor),
    ("off_inj_sum", "offensive injuries", lambda f: f.off_inj_home + f.off_inj_away),
    ("def_inj_sum", "defensive injuries", lambda f: f.def_inj_home + f.def_inj_away),
    ("qb_sum", "starting QB out", lambda f: f.qb_out_home + f.qb_out_away),
]


def margin_vector(f: Features, base_margin: float) -> list[float]:
    return [fn(f, base_margin) for _, _, fn in MARGIN_TERMS]


def total_vector(f: Features) -> list[float]:
    return [fn(f) for _, _, fn in TOTAL_TERMS]


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def ols(rows: list[list[float]], y: list[float], ridge: float = 1e-6) -> tuple[list[float], list[float]]:
    """Least squares coefficients and their standard errors."""
    k = len(rows[0])
    xtx = [[sum(r[i] * r[j] for r in rows) + (ridge if i == j else 0) for j in range(k)]
           for i in range(k)]
    xty = [sum(r[i] * t for r, t in zip(rows, y)) for i in range(k)]
    beta = solve(xtx, xty)
    resid = [t - sum(b * v for b, v in zip(beta, r)) for r, t in zip(rows, y)]
    sigma2 = sum(e * e for e in resid) / max(1, len(y) - k)
    se = []
    for i in range(k):
        unit = [1.0 if j == i else 0.0 for j in range(k)]
        se.append(math.sqrt(max(0.0, sigma2 * solve(xtx, unit)[i])))
    return beta, se


class Coefficients:
    def __init__(self, margin: dict[str, float], total: dict[str, float]):
        self.margin = margin
        self.total = total

    @classmethod
    def zero(cls) -> "Coefficients":
        return cls({n: 0.0 for n, _, _ in MARGIN_TERMS}, {n: 0.0 for n, _, _ in TOTAL_TERMS})

    @classmethod
    def load(cls, path: Path = FACTORS_FILE) -> "Coefficients":
        if not path.exists():
            return cls.zero()
        data = json.loads(path.read_text())
        return cls(data["margin"], data["total"])

    def adjustments(self, f: Features, base_margin: float) -> list[tuple[str, float, float]]:
        """[(label, margin change, total change)] for non-trivial effects.
        Margin is home minus away points."""
        out: dict[str, list[float]] = {}
        for (name, label, _), v in zip(MARGIN_TERMS, margin_vector(f, base_margin)):
            out.setdefault(label, [0.0, 0.0])[0] += self.margin.get(name, 0.0) * v
        for (name, label, _), v in zip(TOTAL_TERMS, total_vector(f)):
            out.setdefault(label, [0.0, 0.0])[1] += self.total.get(name, 0.0) * v
        return [(label, m, t) for label, (m, t) in out.items() if abs(m) >= 0.05 or abs(t) >= 0.05]
