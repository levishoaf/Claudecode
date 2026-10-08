import json
import os
import tempfile
import unittest
from datetime import date, timedelta

from jj_metrics import advisor, metrics
from jj_metrics.cli import main
from jj_metrics.loader import DataError, DayRecord, describe_columns, load_csv, load_mapping
from jj_metrics.targets import DEFAULT_TARGETS, load_targets


def day(i, sales=2000.0, tx=160, **values):
    return DayRecord(day=date(2026, 1, 5) + timedelta(days=i), net_sales=sales,
                     transactions=tx, values=values)


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class LoaderTests(TempDirTest):
    def test_parses_currency_and_skips_blank_optionals(self):
        path = self.write("d.csv", "Date,Net_Sales,Transactions,Labor_Cost,Food_Cost\n"
                                   '2026-01-02,"$1,500.00",120,375,\n'
                                   "2026-01-01,1000,80,,300\n")
        records = load_csv(path)
        self.assertEqual([r.day for r in records], [date(2026, 1, 1), date(2026, 1, 2)])
        self.assertEqual(records[1].net_sales, 1500.0)
        self.assertEqual(records[1].get("labor_cost"), 375.0)
        self.assertIsNone(records[1].get("food_cost"))
        self.assertIsNone(records[0].get("labor_cost"))

    def test_missing_required_column(self):
        path = self.write("d.csv", "date,net_sales\n2026-01-01,100\n")
        with self.assertRaisesRegex(DataError, "transactions"):
            load_csv(path)

    def test_bad_number_reports_line(self):
        path = self.write("d.csv", "date,net_sales,transactions\n2026-01-01,abc,10\n")
        with self.assertRaisesRegex(DataError, "line 2"):
            load_csv(path)

    def test_bad_date(self):
        path = self.write("d.csv", "date,net_sales,transactions\n2026.01.01,100,10\n")
        with self.assertRaisesRegex(DataError, "can't read date"):
            load_csv(path)


class PosExportTests(TempDirTest):
    def test_report_with_title_lines_totals_and_us_dates(self):
        path = self.write("sales.csv", "Daily Sales Summary\nStore 1234,,\n"
                                       "Business Date,Net Sales ($),Order Count,Avg Delivery Time,Delivery Count\n"
                                       '10/01/2026,"$2,000.00",160,16:30,50\n'
                                       '10/02/2026,"$1,000.00",80,12:00,30\n'
                                       'Total,"$3,000.00",240,,80\n')
        records = load_csv(path)
        self.assertEqual([r.day for r in records], [date(2026, 10, 1), date(2026, 10, 2)])
        self.assertEqual(records[0].net_sales, 2000.0)
        self.assertEqual(records[0].get("avg_delivery_minutes"), 16.5)

    def test_shift_rows_are_totaled_and_files_merged(self):
        sales = self.write("sales.csv", "Date,Net Sales,Orders\n2026-10-01,2000,160\n")
        labor = self.write("labor.csv", "Employee,Date,Hours Worked,Gross Pay\n"
                                        "Ann,10/01/2026,8,120\nBob,10/01/2026 06:00 AM,7.5,112.50\n")
        [record] = load_csv([sales, labor])
        self.assertEqual(record.get("labor_hours"), 15.5)
        self.assertEqual(record.get("labor_cost"), 232.5)

    def test_order_level_export_with_mapping(self):
        orders = self.write("orders.csv", "Order #,Closed,Total,Ticket Time (s)\n"
                                          "1,10/01/2026 11:02 AM,10.00,20\n"
                                          "2,10/01/2026 11:05 AM,14.00,40\n"
                                          "3,10/02/2026 12:00 PM,9.50,30\n")
        mapping = self.write("map.json", json.dumps({
            "date": "Closed", "net_sales": "Total", "transactions": "@count",
            "avg_service_seconds": "Ticket Time (s)"}))
        records = load_csv(orders, load_mapping(mapping))
        self.assertEqual([(r.net_sales, r.transactions) for r in records], [(24.0, 2), (9.5, 1)])
        self.assertEqual(records[0].get("avg_service_seconds"), 30.0)

    def test_custom_date_format(self):
        path = self.write("d.csv", "Date,Net Sales,Orders\n01.10.2026,100,10\n")
        mapping = {"date_format": "%d.%m.%Y"}
        self.assertEqual(load_csv(path, mapping)[0].day, date(2026, 10, 1))

    def test_same_column_in_two_files_is_an_error(self):
        a = self.write("a.csv", "Date,Net Sales,Orders\n2026-10-01,100,10\n")
        b = self.write("b.csv", "Date,Net Sales\n2026-10-01,100\n")
        with self.assertRaisesRegex(DataError, "net_sales"):
            load_csv([a, b])

    def test_unrecognized_sales_column_points_to_mapping(self):
        path = self.write("d.csv", "Date,Revenue,Orders\n2026-10-01,100,10\n")
        with self.assertRaisesRegex(DataError, "--map"):
            load_csv(path)

    def test_show_columns(self):
        path = self.write("d.csv", "Business Date,Net Sales,Orders,Notes\n2026-10-01,100,10,x\n")
        text = describe_columns([path])
        self.assertIn("net_sales              <- 'Net Sales'", text)
        self.assertIn("not used: 'Notes'", text)


class TargetsTests(TempDirTest):
    def test_override_and_reject_unknown(self):
        self.assertEqual(load_targets(None), DEFAULT_TARGETS)
        path = self.write("t.json", json.dumps({"labor_pct_max": 22}))
        self.assertEqual(load_targets(path)["labor_pct_max"], 22.0)
        bad = self.write("bad.json", json.dumps({"labour": 22}))
        with self.assertRaisesRegex(ValueError, "labour"):
            load_targets(bad)


class MetricsTests(unittest.TestCase):
    def test_rates_are_ratio_of_totals(self):
        records = [day(0, sales=1000, labor_cost=300, labor_hours=20),
                   day(1, sales=3000, labor_cost=600, labor_hours=40)]
        m = metrics.compute(records)
        self.assertAlmostEqual(m["labor_pct"], 900 / 4000 * 100)
        self.assertAlmostEqual(m["sales_per_labor_hour"], 4000 / 60)
        self.assertAlmostEqual(m["avg_ticket"], 4000 / 320)
        self.assertIsNone(m["food_cost_pct"])

    def test_rate_ignores_days_missing_the_column(self):
        records = [day(0, sales=1000, food_cost=300), day(1, sales=5000)]
        self.assertAlmostEqual(metrics.compute(records)["food_cost_pct"], 30.0)

    def test_delivery_time_weighted_by_orders(self):
        records = [day(0, delivery_orders=10, avg_delivery_minutes=10),
                   day(1, delivery_orders=30, avg_delivery_minutes=20)]
        self.assertAlmostEqual(metrics.compute(records)["avg_delivery_minutes"], 17.5)

    def test_by_weekday_and_trend(self):
        records = [day(i) for i in range(14)]
        self.assertEqual(list(metrics.by_weekday(records))[0], "Monday")
        self.assertEqual(len(metrics.by_weekday(records)), 7)
        self.assertIsNone(metrics.trend(records[:13]))
        self.assertEqual(metrics.trend(records)["metrics"]["avg_daily_sales"]["recent"], 2000)


class AdvisorTests(unittest.TestCase):
    def advise(self, records):
        summary = metrics.compute(records)
        return advisor.advise(summary, metrics.by_weekday(records), metrics.trend(records),
                              DEFAULT_TARGETS)

    def test_on_target_store_gets_no_findings(self):
        records = [day(i, sales=2000, tx=160, labor_cost=440, labor_hours=30, food_cost=560,
                       waste_cost=20, catering_sales=150, online_orders=70, complaints=0)
                   for i in range(14)]
        self.assertEqual(self.advise(records), [])

    def test_labor_savings_estimate(self):
        # 30% labor vs 25% target on $2,000/day -> 5% of monthly sales.
        records = [day(i, sales=2000, labor_cost=600) for i in range(7)]
        labor = [f for f in self.advise(records) if f.metric == "labor_pct"][0]
        self.assertAlmostEqual(labor.monthly_impact, 0.05 * 2000 * advisor.DAYS_PER_MONTH)
        self.assertEqual(labor.impact_kind, "savings")

    def test_splh_not_double_counted_with_labor(self):
        records = [day(i, sales=2000, labor_cost=600, labor_hours=40) for i in range(7)]
        splh = [f for f in self.advise(records) if f.metric == "sales_per_labor_hour"][0]
        self.assertIsNone(splh.monthly_impact)

    def test_findings_sorted_by_impact(self):
        records = [day(i, sales=2000, tx=200, labor_cost=520, food_cost=700) for i in range(7)]
        impacts = [f.monthly_impact or 0 for f in self.advise(records)]
        self.assertEqual(impacts, sorted(impacts, reverse=True))

    def test_weak_weekday_and_declining_sales(self):
        records = [day(i, sales=(800 if (date(2026, 1, 5) + timedelta(days=i)).weekday() == 6
                                 else 2000) * (1 if i < 14 else 0.85), tx=170)
                   for i in range(28)]
        metrics_found = {f.metric for f in self.advise(records)}
        self.assertIn("weekday_sales", metrics_found)
        self.assertIn("sales_trend", metrics_found)


class CliTests(TempDirTest):
    def test_end_to_end_all_formats(self):
        csv_path = os.path.join(self.tmp.name, "sample.csv")
        self.assertEqual(main(["--generate-sample", csv_path, "--days", "30"]), 0)
        for fmt, needle in [("text", "Scorecard"), ("markdown", "## Scorecard"),
                            ("json", '"recommendations"')]:
            out = os.path.join(self.tmp.name, f"report.{fmt}")
            self.assertEqual(main([csv_path, "--format", fmt, "-o", out]), 0)
            with open(out, encoding="utf-8") as f:
                text = f.read()
            self.assertIn(needle, text)
        with open(os.path.join(self.tmp.name, "report.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["summary"]["days"], 30)

    def test_bad_file_returns_error(self):
        self.assertEqual(main([os.path.join(self.tmp.name, "nope.csv")]), 1)

    def test_empty_date_range(self):
        csv_path = os.path.join(self.tmp.name, "sample.csv")
        main(["--generate-sample", csv_path, "--days", "10"])
        self.assertEqual(main([csv_path, "--start", "2099-01-01"]), 1)


if __name__ == "__main__":
    unittest.main()
