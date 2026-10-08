"""Load daily store data from CSV."""

import csv
from dataclasses import dataclass, field
from datetime import date

REQUIRED_COLUMNS = ("date", "net_sales", "transactions")

# Optional numeric columns. Any that are missing or blank are simply skipped
# by the metrics that need them.
OPTIONAL_COLUMNS = (
    "labor_hours",          # total crew + manager hours worked
    "labor_cost",           # wages for those hours ($)
    "food_cost",            # cost of goods sold ($)
    "waste_cost",           # thrown-out product ($)
    "delivery_orders",      # count of delivery orders
    "avg_delivery_minutes", # average order-to-door time
    "late_deliveries",      # deliveries over the delivery-time target
    "online_orders",        # app + web orders (pickup and delivery)
    "catering_sales",       # catering / box-lunch revenue ($)
    "complaints",           # customer complaints logged
    "avg_service_seconds",  # in-store order-to-handoff time
)


@dataclass
class DayRecord:
    day: date
    net_sales: float
    transactions: float
    values: dict = field(default_factory=dict)

    def get(self, name):
        """Return an optional column's value, or None if it wasn't provided."""
        return self.values.get(name)


class DataError(ValueError):
    pass


def _parse_number(raw, column, line):
    text = raw.strip().replace("$", "").replace(",", "")
    if text.endswith("%"):
        raise DataError(f"line {line}: column '{column}' should be a number, not a percentage")
    try:
        return float(text)
    except ValueError:
        raise DataError(f"line {line}: column '{column}' has non-numeric value {raw!r}") from None


def load_csv(path):
    """Read a CSV of daily store data and return DayRecords sorted by date."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise DataError(f"{path} is empty")
        headers = {h.strip().lower(): h for h in reader.fieldnames}
        missing = [c for c in REQUIRED_COLUMNS if c not in headers]
        if missing:
            raise DataError(f"{path} is missing required column(s): {', '.join(missing)}")

        records = []
        for line, row in enumerate(reader, start=2):
            raw = {key: (row.get(orig) or "") for key, orig in headers.items()}
            if not any(v.strip() for v in raw.values()):
                continue
            try:
                day = date.fromisoformat(raw["date"].strip())
            except ValueError:
                raise DataError(f"line {line}: date {raw['date']!r} must be YYYY-MM-DD") from None
            values = {}
            for column in OPTIONAL_COLUMNS:
                if raw.get(column, "").strip():
                    values[column] = _parse_number(raw[column], column, line)
            records.append(DayRecord(
                day=day,
                net_sales=_parse_number(raw["net_sales"], "net_sales", line),
                transactions=_parse_number(raw["transactions"], "transactions", line),
                values=values,
            ))

    if not records:
        raise DataError(f"{path} has no data rows")
    records.sort(key=lambda r: r.day)
    return records
