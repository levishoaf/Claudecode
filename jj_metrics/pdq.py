"""Read PDQ POS "Weekly Sales Report" exports (.xls, or the same report saved as CSV).

Layout of that report: one row per line item ("IN-Sub", "Labor $", "# Of Sales"...),
then a Summary column, a #EA (item count) column, and two columns per day for
the AM and PM shifts, with the dates in the row under the day headers.
"""

import csv
import io

from .loader import DataError, parse_date, parse_number

TITLE = "weekly sales report"

# Our column <- the PDQ row(s) that are added together for it.
# Sales uses "=Adjusted Sales" because that is what PDQ divides labor by for
# its own Labor %, so the numbers here match the report.
ROWS = {
    "net_sales": ["=Adjusted Sales"],
    "transactions": ["# Of Sales"],
    "labor_cost": ["Labor $"],
    "waste_cost": ["-Waste"],
    "catering_sales": ["Box Lunch", "Platters"],
    "online_sales": ["Total Online Orders"],
}

# Weekly item counts (#EA column) used for add-on attach rates.
ITEMS = {
    "Cookies": ["Cookie"],
    "Sides (chips, pickles)": ["IN-Side", "DEL-Side"],
    "Drinks": ["IN-Pop", "DEL-Pop"],
    "Combos": ["IN-Combos / Kids 1/2 off", "DEL-Combos / Kids 1/2 off"],
}


def _label(text):
    return " ".join(str(text).replace("\xa0", " ").split()).lower()


def is_weekly_sales_report(grid):
    return any(row and _label(row[0]) == TITLE for row in grid[:5])


def read_grid(path):
    """Return the first sheet of an .xls/.csv file as a list of rows of strings."""
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            return [row for row in csv.reader(f)]
    try:
        import xlrd
    except ImportError:
        raise DataError(f"{path}: reading .xls files needs the xlrd package "
                        f"(pip install xlrd), or save the report as CSV from Excel.") from None
    try:
        book = xlrd.open_workbook(path, logfile=io.StringIO())
    except xlrd.XLRDError as e:
        raise DataError(f"{path}: can't open as an Excel file ({e})") from None
    sheet = book.sheet_by_index(0)
    grid = []
    for r in range(sheet.nrows):
        row = []
        for c in range(sheet.ncols):
            cell = sheet.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                row.append(xlrd.xldate_as_datetime(cell.value, book.datemode).date().isoformat())
            elif cell.ctype == xlrd.XL_CELL_NUMBER:
                row.append(repr(cell.value))
            else:
                row.append(str(cell.value))
        grid.append(row)
    return grid


def parse_weekly_sales(grid, path="report"):
    """Parse a Weekly Sales Report grid.

    Returns (days, parts, items):
      days  {date: {column: value}}             AM + PM combined
      parts {date: {"AM"/"PM": {column: value}}}
      items {"orders": n, "items": {name: {"count": n, "sales": $}}}
    """
    header_at = next((i for i, row in enumerate(grid)
                      if row and _label(row[0]) == "sales item"), None)
    if header_at is None or header_at + 2 >= len(grid):
        raise DataError(f"{path}: doesn't look like a PDQ Weekly Sales Report (no 'Sales Item' row)")
    date_row, part_row = grid[header_at + 1], grid[header_at + 2]

    # Map each day column to (date, daypart). The date sits over the AM column.
    columns, current = {}, None
    for c in range(3, len(part_row)):
        text = date_row[c].strip() if c < len(date_row) else ""
        if text:
            current = parse_date(text)
            if current is None:
                raise DataError(f"{path}: can't read date {text!r} in the day header row")
        part = part_row[c].strip().upper()
        if current and part:
            columns[c] = (current, part)
    if not columns:
        raise DataError(f"{path}: no day columns found under the 'Sales Item' header")

    rows = {_label(row[0]): row for row in grid[header_at + 3:] if row and row[0].strip()}

    def find(label):
        return rows.get(_label(label))

    for column in ("net_sales", "transactions"):
        if not any(find(label) for label in ROWS[column]):
            raise DataError(f"{path}: missing the '{ROWS[column][0]}' row")

    days, parts = {}, {}
    for column, labels in ROWS.items():
        for label in labels:
            row = find(label)
            if not row:
                continue
            for c, (day, part) in columns.items():
                raw = row[c] if c < len(row) else ""
                if not raw.strip():
                    continue
                value = parse_number(raw, column, f"{path} row '{row[0].strip()}'")
                bucket = parts.setdefault(day, {}).setdefault(part, {})
                bucket[column] = bucket.get(column, 0.0) + value
                days.setdefault(day, {})
                days[day][column] = days[day].get(column, 0.0) + value

    items = {}
    orders_row = find("# Of Sales")
    weekly_orders = parse_number(orders_row[2], "transactions", path) if len(orders_row) > 2 else 0
    for name, labels in ITEMS.items():
        count = sales = 0.0
        for label in labels:
            row = find(label)
            if row and len(row) > 2:
                sales += parse_number(row[1], name, path)
                count += parse_number(row[2], name, path)
        if count:
            items[name] = {"count": count, "sales": sales}
    item_mix = {"orders": weekly_orders, "items": items} if items and weekly_orders else None
    return days, parts, item_mix
