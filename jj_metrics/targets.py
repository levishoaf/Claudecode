"""Metric targets.

These defaults are general quick-service sandwich benchmarks, not official
Jimmy John's corporate numbers. Override any of them with a JSON file
(see targets.example.json) to match your franchise's own goals.
"""

import json

DEFAULT_TARGETS = {
    "labor_pct_max": 25.0,            # labor cost as % of net sales
    "food_cost_pct_max": 30.0,        # COGS as % of net sales
    "waste_pct_max": 1.5,             # waste as % of net sales
    "sales_per_labor_hour_min": 60.0, # $ net sales per labor hour
    "avg_ticket_min": 12.00,          # $ per transaction
    "avg_delivery_minutes_max": 15.0, # "Freaky Fast" order-to-door
    "late_delivery_pct_max": 10.0,    # % of deliveries over target
    "online_mix_pct_min": 40.0,       # online orders as % of transactions
    "online_sales_pct_min": 40.0,     # online sales as % of net sales
    "catering_pct_min": 6.0,          # catering as % of net sales
    "complaints_per_1000_max": 2.0,   # complaints per 1,000 transactions
    "avg_service_seconds_max": 30.0,  # in-store order-to-handoff time
}


def load_targets(path=None):
    targets = dict(DEFAULT_TARGETS)
    if path:
        with open(path, encoding="utf-8") as f:
            overrides = json.load(f)
        unknown = set(overrides) - set(DEFAULT_TARGETS)
        if unknown:
            raise ValueError(f"unknown target(s) in {path}: {', '.join(sorted(unknown))}")
        targets.update({k: float(v) for k, v in overrides.items()})
    return targets
