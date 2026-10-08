# Jimmy John's Store Metrics Analyzer

A command-line tool that reads your store's daily numbers, scores them against targets, and gives you a ranked list of what to fix first. Each recommendation comes with specific actions and an estimated monthly dollar impact.

It needs only Python 3.9+. There's nothing to install.

## Quick start

```bash
# Try it on the included 90-day sample
python3 -m jj_metrics sample_data/sample_store.csv

# Your own data, as a Markdown report
python3 -m jj_metrics my_store.csv --store "#1234" --format markdown -o report.md
```

See [`sample_data/sample_report.md`](sample_data/sample_report.md) for example output.

## Input data

A CSV file with **one row per day**. Column names are case-insensitive. Only the first three columns are required. Leave out any column you don't track, and the report will list what isn't being measured.

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

Most POS and back-office systems can export daily sales, labor and order counts. Paste them into a spreadsheet with these headers and save it as CSV.

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
