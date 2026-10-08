# Jimmy John's Store Metrics Analyzer

A command-line tool that reads your store's daily numbers, scores them against targets, and gives you a ranked list of what to fix first. Each recommendation comes with specific actions and an estimated monthly dollar impact.

It needs only Python 3.9+. There's nothing to install.

## Quick start

```bash
# Try it on the included 90-day sample
python3 -m jj_metrics sample_data/sample_store.csv

# Your own POS exports, as a Markdown report
python3 -m jj_metrics sales_export.csv labor_export.csv --store "#1234" --format markdown -o report.md
```

See [`sample_data/sample_report.md`](sample_data/sample_report.md) for example output.

## Input data

One or more CSV files. These are the columns the tool understands. If you're building a file by hand, use these names with one row per day; POS exports with different names work too (see the next section). Column names are case-insensitive. Only the first three columns are required. Leave out any column you don't track, and the report will list what isn't being measured.

| Column | Required | Meaning |
|---|---|---|
| `date` | yes | `YYYY-MM-DD` |
| `net_sales` | yes | Net sales in $ (`$1,234.56` is fine) |
| `transactions` | yes | Number of orders |
| `labor_hours` | | Total hours worked |
| `labor_cost` | | Wages paid for those hours ($) |
| `food_cost` | | Cost of goods sold ($) |
| `waste_cost` | | Product thrown out ($) |
| `delivery_orders` | | Number of delivery orders |
| `avg_delivery_minutes` | | Average order-to-door time |
| `late_deliveries` | | Deliveries over your time target |
| `online_orders` | | App and web orders |
| `catering_sales` | | Catering revenue ($) |
| `complaints` | | Customer complaints logged |
| `avg_service_seconds` | | In-store order-to-handoff time |

## Using your POS exports (PDQ or any other POS)

You don't need to rename columns or reshape your POS reports. Export them as **CSV**; if a report only exports to Excel, open it and use *Save As → CSV*. Then point the tool at them directly:

```bash
python3 -m jj_metrics sales_export.csv labor_export.csv
```

What it handles automatically:

- **Common header names.** "Business Date", "Net Sales ($)", "Order Count", "Hours Worked", "Gross Pay" and similar are recognized.
- **Report title lines** above the header row, and **Total/Summary rows** at the bottom.
- **US dates** (`10/07/2026`), dates with times (`10/07/2026 11:32 AM`), `$1,234.56` amounts, `(12.50)` negatives and `mm:ss` times.
- **More than one row per day.** Per-shift labor rows or per-order rows are added up into daily totals.
- **Several files**, such as a sales report plus a labor report, merged by date.

### Step 1: check what was recognized

```bash
python3 -m jj_metrics sales_export.csv labor_export.csv --show-columns
```

```
sales_export.csv
  (header found on line 3)
  date                   <- 'Business Date'
  net_sales              <- 'Net Sales ($)'
  transactions           <- 'Order Count'
labor_export.csv
  date                   <- 'Date'
  labor_hours            <- 'Hours Worked'
  labor_cost             <- 'Gross Pay'
  not used: 'Employee'
```

### Step 2: map anything it missed

If a column you need shows up under "not used", or was matched to the wrong metric, write a small JSON mapping file from our column names to your headers. Copy [`pos_mapping.example.json`](pos_mapping.example.json) and replace its header names with the exact ones in your export; the ones in the example are placeholders. Delete any lines you don't need.

```bash
python3 -m jj_metrics sales_export.csv labor_export.csv --map my_pdq_mapping.json
```

Two special settings:

- `"transactions": "@count"`: each row in the export is a single order, so the tool counts rows to get the order count. Use this for an order or transaction detail export.
- `"date_format": "%d.%m.%Y"`: use this only if your dates aren't in a common US or ISO format. It takes Python [strftime codes](https://strftime.org/).

Once your mapping works, reuse the same command every week or month.

## What it reports

- **Scorecard:** each metric, its target, and whether you hit it.
- **Recommendations:** ranked by estimated $/month (cost savings or added sales), then by how far off target you are. Each includes concrete actions and, where useful, the worst day of the week to start on.
- **By day of week:** sales, orders, ticket, labor %, sales per labor hour (SPLH) and waste % for each weekday.
- **Trend:** the first half of the period compared with the second half, to show what's improving or slipping.

Every percentage is a ratio of totals (total labor $ ÷ total sales $), not an average of daily percentages, so busy days get their proper weight. Dollar estimates assume you close the *full* gap to target, so read them as an upper bound.

## Targets

The defaults in [`jj_metrics/targets.py`](jj_metrics/targets.py) are general quick-service sandwich benchmarks, **not official Jimmy John's numbers**. Override any of them with a JSON file:

```bash
python3 -m jj_metrics my_store.csv --targets targets.example.json
```

| Target | Default |
|---|---|
| `labor_pct_max` | 25% |
| `food_cost_pct_max` | 30% |
| `waste_pct_max` | 1.5% |
| `sales_per_labor_hour_min` | $60 |
| `avg_ticket_min` | $12.00 |
| `avg_delivery_minutes_max` | 15 min |
| `late_delivery_pct_max` | 10% |
| `online_mix_pct_min` | 40% |
| `catering_pct_min` | 6% |
| `complaints_per_1000_max` | 2.0 |
| `avg_service_seconds_max` | 30 sec |

## Other options

```
--map JSON                    column mapping for your POS exports
--show-columns                show which columns were recognized, then exit
--format text|markdown|json   output format (default: text)
-o, --output PATH             write to a file instead of the screen
--start / --end YYYY-MM-DD    analyze only part of the data
--store NAME                  store name shown in the report title
--generate-sample PATH        write a sample CSV (use --days N for its length)
```

## Tests

```bash
python3 -m unittest discover -s tests
```
