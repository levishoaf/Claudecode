"""Load store data from one or more CSV exports.

Handles the shapes POS exports usually come in:
  * report title lines above the real header row
  * column names that differ from ours ("Business Date", "Net Sales ($)")
  * a "Total" row at the bottom
  * one row per order or per shift instead of one per day (rows are totaled by date)
  * sales and labor in separate files (merged by date)
"""

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime

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
    "online_sales",         # app + web order revenue ($)
    "complaints",           # customer complaints logged
    "avg_service_seconds",  # in-store order-to-handoff time
)

NUMERIC_COLUMNS = ("net_sales", "transactions") + OPTIONAL_COLUMNS

# Columns that are per-row averages, so several rows for one day are combined
# as a weighted average (weight column, or 1 per row if it isn't present)
# instead of being added up.
AVERAGE_COLUMNS = {
    "avg_delivery_minutes": "delivery_orders",
    "avg_service_seconds": "transactions",
}

# Header names commonly seen in POS and payroll exports. Headers are compared
# after normalizing (lowercase, punctuation like $ # ( ) removed), and each
# column's own name ("net_sales" -> "net sales") always matches.
ALIASES = {
    "date": ["business date", "business day", "sales date", "report date", "order date",
             "day", "date time", "closed date", "close date"],
    "net_sales": ["net sales", "net revenue", "net total", "total net sales", "net amount"],
    "transactions": ["orders", "order count", "total orders", "transaction count", "tickets",
                     "ticket count", "checks", "check count"],
    "labor_hours": ["hours", "total hours", "hours worked", "labor hrs", "total labor hours"],
    "labor_cost": ["labor dollars", "labor amount", "total labor", "total labor cost",
                   "wages", "total wages", "total pay", "gross pay"],
    "food_cost": ["cogs", "cost of goods", "cost of goods sold", "food cost dollars"],
    "waste_cost": ["waste", "waste dollars", "waste amount", "spoilage"],
    "delivery_orders": ["deliveries", "delivery count", "delivery order count"],
    "avg_delivery_minutes": ["avg delivery time", "average delivery time", "delivery time"],
    "late_deliveries": ["late orders", "late delivery count"],
    "online_orders": ["online order count", "web orders", "app orders"],
    "catering_sales": ["catering", "catering net sales", "catering revenue"],
    "online_sales": ["online net sales", "total online sales", "online revenue"],
    "complaints": ["complaint count", "guest complaints"],
    "avg_service_seconds": ["avg service time", "average service time", "service time"],
}

COUNT_ROWS = "@count"  # mapping value meaning "each row is one order"

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y",
                "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%b-%y")


@dataclass
class DayRecord:
    day: date
    net_sales: float
    transactions: float
    values: dict = field(default_factory=dict)
    # Optional shift split, e.g. {"AM": {"net_sales": ..., "labor_cost": ...}, "PM": {...}}
    parts: dict = field(default_factory=dict)

    def get(self, name):
        """Return an optional column's value, or None if it wasn't provided."""
        return self.values.get(name)


class DataError(ValueError):
    pass


def normalize(header):
    text = header.strip().lower().replace("_", " ").replace("/", " ")
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def load_mapping(path):
    """Read a JSON column mapping: {"net_sales": "Their Header", ..., "date_format": "%m/%d/%Y"}."""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        mapping = json.load(f)
    unknown = set(mapping) - set(REQUIRED_COLUMNS) - set(OPTIONAL_COLUMNS) - {"date_format"}
    if unknown:
        raise DataError(f"unknown column(s) in {path}: {', '.join(sorted(unknown))}")
    return mapping


def match_columns(headers, mapping=None):
    """Return {our column: header index} for a header row."""
    mapping = mapping or {}
    normalized = [normalize(h) for h in headers]
    found = {}
    for column in REQUIRED_COLUMNS + OPTIONAL_COLUMNS:
        if column in mapping:
            wanted = mapping[column]
            if wanted == COUNT_ROWS:
                found[column] = COUNT_ROWS
            elif normalize(wanted) in normalized:
                found[column] = normalized.index(normalize(wanted))
            continue
        for name in [normalize(column)] + ALIASES.get(column, []):
            if name in normalized and normalized.index(name) not in found.values():
                found[column] = normalized.index(name)
                break
    return found


def parse_date(text, date_format=None):
    text = text.strip()
    candidates = [text]
    head = re.split(r"[ T]", text, maxsplit=1)[0]
    if head != text:
        candidates.append(head)  # "10/07/2026 11:32 AM" -> "10/07/2026"
    formats = (date_format,) if date_format else DATE_FORMATS
    for candidate in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                pass
    return None


def parse_number(raw, column, where):
    text = raw.strip().replace("$", "").replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if text.endswith("%"):
        raise DataError(f"{where}: column '{column}' should be a number, not a percentage")
    if column == "avg_delivery_minutes" and ":" in text:
        text = str(_clock_to_seconds(text, column, where) / 60)
    elif column == "avg_service_seconds" and ":" in text:
        text = str(_clock_to_seconds(text, column, where))
    try:
        value = float(text)
    except ValueError:
        raise DataError(f"{where}: column '{column}' has non-numeric value {raw!r}") from None
    return -value if negative else value


def _clock_to_seconds(text, column, where):
    """'14:30' (mm:ss) or '0:14:30' (h:mm:ss) -> seconds."""
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        raise DataError(f"{where}: column '{column}' has unreadable time {text!r}") from None
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def _find_header(rows, mapping, path):
    """Index of the first row (within the first 25) that has a recognizable date column."""
    for i, row in enumerate(rows[:25]):
        if "date" in match_columns(row, mapping):
            return i
    raise DataError(
        f"{path}: couldn't find a date column. Add a column mapping (--map) that names "
        f"your date column, e.g. {{\"date\": \"Business Date\"}}.")


def read_file(path, mapping=None):
    """Read one CSV and return ({date: {column: value}}, {column: header name})."""
    mapping = mapping or {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise DataError(f"{path} is empty")
    header_at = _find_header(rows, mapping, path)
    headers = rows[header_at]
    columns = match_columns(headers, mapping)

    sums = {}      # date -> {column: total}
    weighted = {}  # date -> {avg column: [value * weight, weight]}
    for line, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        if not any(cell.strip() for cell in row):
            continue
        where = f"{path} line {line}"
        cell = lambda col: row[columns[col]] if columns[col] < len(row) else ""
        raw_date = cell("date").strip()
        if not raw_date or raw_date.lower().startswith(("total", "grand total", "summary")):
            continue
        day = parse_date(raw_date, mapping.get("date_format"))
        if day is None:
            raise DataError(f"{where}: can't read date {raw_date!r}. If it's in an unusual "
                            f"format, set \"date_format\" in your column mapping.")
        row_values = {}
        for column, index in columns.items():
            if column == "date":
                continue
            if index == COUNT_ROWS:
                row_values[column] = 1.0
            elif cell(column).strip():
                row_values[column] = parse_number(cell(column), column, where)

        day_sums = sums.setdefault(day, {})
        for column, value in row_values.items():
            weight_column = AVERAGE_COLUMNS.get(column)
            if weight_column:
                weight = row_values.get(weight_column, 1.0)
                acc = weighted.setdefault(day, {}).setdefault(column, [0.0, 0.0])
                acc[0] += value * weight
                acc[1] += weight
            else:
                day_sums[column] = day_sums.get(column, 0.0) + value

    for day, columns_acc in weighted.items():
        for column, (total, weight) in columns_acc.items():
            if weight:
                sums[day][column] = total / weight

    matched = {col: ("(one order per row)" if idx == COUNT_ROWS else headers[idx])
               for col, idx in columns.items()}
    return sums, matched


@dataclass
class Dataset:
    records: list
    item_mix: dict = None  # weekly add-on item counts, when the export has them


def _read_any(path, mapping):
    """Return (days, parts, item_mix) for a CSV export or a PDQ Weekly Sales Report."""
    from . import pdq

    if path.lower().endswith((".xls", ".xlsx")) or _looks_like_pdq_csv(path):
        if path.lower().endswith(".xlsx"):
            raise DataError(f"{path}: .xlsx isn't supported yet; save it as CSV from Excel.")
        grid = pdq.read_grid(path)
        if not pdq.is_weekly_sales_report(grid):
            raise DataError(f"{path}: only PDQ Weekly Sales Reports can be read from Excel files; "
                            f"save other reports as CSV.")
        return pdq.parse_weekly_sales(grid, path)
    days, _matched = read_file(path, mapping)
    return days, {}, None


def _looks_like_pdq_csv(path):
    from . import pdq

    with open(path, newline="", encoding="utf-8-sig") as f:
        head = [row for _, row in zip(range(5), csv.reader(f))]
    return pdq.is_weekly_sales_report(head)


def load_files(paths, mapping=None, warn=sys.stderr):
    """Read and merge one or more exports into a Dataset with DayRecords sorted by date."""
    if isinstance(paths, str):
        paths = [paths]
    merged = {}        # date -> {column: value}
    merged_parts = {}  # date -> {daypart: {column: value}}
    source = {}        # (date, column) -> file that provided it
    found = set()      # columns seen in any file
    item_mix = None
    for path in paths:
        days, parts, items = _read_any(path, mapping)
        for day, values in days.items():
            for column in values:
                if (day, column) in source:
                    raise DataError(f"{day}: '{column}' appears in both {source[day, column]} and "
                                    f"{path}. Remove the duplicate file or column.")
                source[day, column] = path
            found.update(values)
            merged.setdefault(day, {}).update(values)
        for day, by_part in parts.items():
            merged_parts.setdefault(day, {}).update(by_part)
        if items:
            if item_mix is None:
                item_mix = {"orders": 0.0, "items": {}}
            item_mix["orders"] += items["orders"]
            for name, item in items["items"].items():
                acc = item_mix["items"].setdefault(name, {"count": 0.0, "sales": 0.0})
                acc["count"] += item["count"]
                acc["sales"] += item["sales"]

    for column in ("net_sales", "transactions"):
        if column not in found:
            raise DataError(
                f"no '{column}' column found in {', '.join(paths)}. Run with --show-columns to "
                f"see what was recognized, then add a column mapping (--map).")

    records, skipped = [], 0
    for day in sorted(merged):
        values = dict(merged[day])
        if "net_sales" not in values or "transactions" not in values:
            skipped += 1
            continue
        records.append(DayRecord(
            day=day,
            net_sales=values.pop("net_sales"),
            transactions=values.pop("transactions"),
            values=values,
            parts=merged_parts.get(day, {}),
        ))
    if skipped and warn:
        print(f"warning: skipped {skipped} day(s) that had no sales or order count", file=warn)
    if not records:
        raise DataError(f"no data rows found in {', '.join(paths)}")
    return Dataset(records, item_mix)


def load_csv(paths, mapping=None, warn=sys.stderr):
    """Read and merge one or more exports into DayRecords sorted by date."""
    return load_files(paths, mapping, warn).records


def describe_columns(paths, mapping=None):
    """Human-readable summary of which headers were matched in each file."""
    lines = []
    from . import pdq

    for path in paths:
        lines.append(path)
        if path.lower().endswith(".xls") or _looks_like_pdq_csv(path):
            lines.append("  PDQ Weekly Sales Report:")
            for column, labels in pdq.ROWS.items():
                lines.append(f"  {column:<22} <- " + " + ".join(repr(l) for l in labels))
            lines.append("  add-on counts          <- " + ", ".join(pdq.ITEMS))
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        try:
            header_at = _find_header(rows, mapping or {}, path)
        except DataError as e:
            lines.append(f"  {e}")
            continue
        headers = rows[header_at]
        columns = match_columns(headers, mapping)
        if header_at:
            lines.append(f"  (header found on line {header_at + 1})")
        for column in REQUIRED_COLUMNS + OPTIONAL_COLUMNS:
            if column in columns:
                idx = columns[column]
                name = "(one order per row)" if idx == COUNT_ROWS else repr(headers[idx])
                lines.append(f"  {column:<22} <- {name}")
        used = {idx for idx in columns.values() if idx != COUNT_ROWS}
        unused = [h for i, h in enumerate(headers) if i not in used and h.strip()]
        if unused:
            lines.append("  not used: " + ", ".join(repr(h) for h in unused))
    return "\n".join(lines)
