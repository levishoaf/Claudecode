"""Generate realistic-looking sample data to try the tool with."""

import csv
import random
from datetime import date, timedelta

from .loader import OPTIONAL_COLUMNS, REQUIRED_COLUMNS

# Relative sales volume by weekday (Monday first). Weekdays are lunch-driven.
WEEKDAY_VOLUME = [1.0, 1.05, 1.08, 1.1, 1.15, 0.85, 0.62]


def generate(path, days=90, start=None, seed=7):
    """Write a sample CSV of `days` days ending yesterday (or starting at `start`)."""
    rng = random.Random(seed)
    start = start or date.today() - timedelta(days=days)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(REQUIRED_COLUMNS + OPTIONAL_COLUMNS)
        for i in range(days):
            day = start + timedelta(days=i)
            drift = 1 - 0.0008 * i  # slow decline so the trend section has something to show
            tx = round(165 * WEEKDAY_VOLUME[day.weekday()] * drift * rng.uniform(0.9, 1.1))
            ticket = rng.uniform(10.6, 11.6)
            catering = rng.choice([0, 0, 0, 0, 120, 180, 260]) * rng.uniform(0.8, 1.2)
            sales = tx * ticket + catering
            # Staffing doesn't flex with demand much — weekends end up overstaffed.
            hours = rng.uniform(34, 40) if day.weekday() < 5 else rng.uniform(30, 34)
            wage = rng.uniform(14.5, 15.5)
            delivery = round(tx * rng.uniform(0.28, 0.36))
            avg_delivery = rng.uniform(13, 19) + (2 if day.weekday() == 4 else 0)
            late = round(delivery * max(0.0, (avg_delivery - 12) / 30))
            writer.writerow([
                day.isoformat(),
                f"{sales:.2f}",
                tx,
                f"{hours:.1f}",
                f"{hours * wage:.2f}",
                f"{sales * rng.uniform(0.275, 0.305):.2f}",
                f"{sales * rng.uniform(0.012, 0.026) * (1.5 if day.weekday() >= 5 else 1):.2f}",
                delivery,
                f"{avg_delivery:.1f}",
                late,
                round(tx * rng.uniform(0.38, 0.46)),
                f"{catering:.2f}",
                rng.choice([0, 0, 0, 0, 0, 0, 0, 1, 1, 2]),
                f"{rng.uniform(23, 33):.0f}",
            ])
    return path
