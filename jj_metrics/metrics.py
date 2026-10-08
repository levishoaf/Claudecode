"""Compute store metrics from daily records.

Every rate is a ratio of totals (e.g. total labor $ / total sales $), not an
average of daily percentages, so busy days carry their real weight. Each
metric only uses the days that actually reported the columns it needs.
"""

import calendar


def _ratio(records, numerator, denominator, scale=1.0):
    """sum(numerator) / sum(denominator) over records reporting both columns."""
    num = den = 0.0
    for r in records:
        n, d = _value(r, numerator), _value(r, denominator)
        if n is None or d is None:
            continue
        num += n
        den += d
    if den == 0:
        return None
    return num / den * scale


def _weighted_avg(records, column, weight):
    """Average of a per-day average column, weighted by a count column."""
    total = weights = 0.0
    for r in records:
        v, w = _value(r, column), _value(r, weight)
        if v is None:
            continue
        if w is None:
            w = 1.0
        total += v * w
        weights += w
    if weights == 0:
        return None
    return total / weights


def _value(record, name):
    if name == "net_sales":
        return record.net_sales
    if name == "transactions":
        return record.transactions
    return record.get(name)


def compute(records):
    """Return the headline metrics for a list of DayRecords (None = no data)."""
    days = len(records)
    total_sales = sum(r.net_sales for r in records)
    total_tx = sum(r.transactions for r in records)
    return {
        "days": days,
        "total_sales": total_sales,
        "total_transactions": total_tx,
        "avg_daily_sales": total_sales / days if days else None,
        "avg_daily_transactions": total_tx / days if days else None,
        "avg_ticket": total_sales / total_tx if total_tx else None,
        "labor_pct": _ratio(records, "labor_cost", "net_sales", 100),
        "sales_per_labor_hour": _ratio(records, "net_sales", "labor_hours"),
        "food_cost_pct": _ratio(records, "food_cost", "net_sales", 100),
        "waste_pct": _ratio(records, "waste_cost", "net_sales", 100),
        "avg_delivery_minutes": _weighted_avg(records, "avg_delivery_minutes", "delivery_orders"),
        "late_delivery_pct": _ratio(records, "late_deliveries", "delivery_orders", 100),
        "delivery_mix_pct": _ratio(records, "delivery_orders", "transactions", 100),
        "online_mix_pct": _ratio(records, "online_orders", "transactions", 100),
        "catering_pct": _ratio(records, "catering_sales", "net_sales", 100),
        "complaints_per_1000": _ratio(records, "complaints", "transactions", 1000),
        "avg_service_seconds": _weighted_avg(records, "avg_service_seconds", "transactions"),
    }


def by_weekday(records):
    """Metrics grouped by day of week, Monday first. Weekdays with no data are omitted."""
    groups = {}
    for r in records:
        groups.setdefault(r.day.weekday(), []).append(r)
    return {calendar.day_name[wd]: compute(groups[wd]) for wd in sorted(groups)}


def trend(records):
    """Compare the most recent half of the period with the earlier half.

    Returns None when there are fewer than 14 days, since a shorter split is
    mostly noise.
    """
    if len(records) < 14:
        return None
    mid = len(records) // 2
    earlier, recent = compute(records[:mid]), compute(records[mid:])
    changes = {}
    for key, new in recent.items():
        old = earlier.get(key)
        if key in ("days", "total_sales", "total_transactions") or old is None or new is None:
            continue
        changes[key] = {"earlier": old, "recent": new}
    return {
        "earlier_range": (records[0].day, records[mid - 1].day),
        "recent_range": (records[mid].day, records[-1].day),
        "metrics": changes,
    }
