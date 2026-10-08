"""Command-line entry point."""

import argparse
import sys
from datetime import date

from . import advisor, metrics, report
from .loader import DataError, describe_columns, load_files, load_mapping
from .sample import generate
from .targets import load_targets


def build_parser():
    p = argparse.ArgumentParser(
        prog="jj_metrics",
        description="Analyze Jimmy John's store data and recommend how to improve key metrics.",
    )
    p.add_argument("csv", nargs="*",
                   help="CSV export(s) of store data; several files (e.g. sales + labor) are merged by date")
    p.add_argument("--map", metavar="JSON",
                   help="column mapping for POS exports whose headers aren't recognized automatically")
    p.add_argument("--show-columns", action="store_true",
                   help="show which columns were recognized in each file, then exit")
    p.add_argument("--targets", help="JSON file overriding default metric targets")
    p.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    p.add_argument("--output", "-o", help="write the report to this file instead of stdout")
    p.add_argument("--store", help="store name or number to show in the report title")
    p.add_argument("--start", type=date.fromisoformat, help="only analyze days on/after YYYY-MM-DD")
    p.add_argument("--end", type=date.fromisoformat, help="only analyze days on/before YYYY-MM-DD")
    p.add_argument("--generate-sample", metavar="PATH",
                   help="write a sample data CSV to PATH and exit")
    p.add_argument("--days", type=int, default=90, help="days of sample data to generate (default 90)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.generate_sample:
        generate(args.generate_sample, days=args.days)
        print(f"Wrote {args.days} days of sample data to {args.generate_sample}")
        return 0
    if not args.csv:
        build_parser().error("a CSV file is required (or use --generate-sample)")

    try:
        mapping = load_mapping(args.map)
        if args.show_columns:
            print(describe_columns(args.csv, mapping))
            return 0
        dataset = load_files(args.csv, mapping)
        targets = load_targets(args.targets)
    except (OSError, DataError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    records = [r for r in dataset.records
               if (not args.start or r.day >= args.start) and (not args.end or r.day <= args.end)]
    # Add-on counts are weekly totals, so they can't be trimmed to a date range.
    add_ons = {} if (args.start or args.end) else metrics.add_on_rates(dataset.item_mix)
    if not records:
        print("error: no data in the selected date range", file=sys.stderr)
        return 1

    summary = metrics.compute(records)
    weekdays = metrics.by_weekday(records)
    trend = metrics.trend(records)
    dayparts = metrics.by_daypart(records)
    shifts = metrics.by_weekday_daypart(records)
    findings = advisor.advise(summary, weekdays, trend, targets,
                              dayparts=dayparts, shifts=shifts, add_ons=add_ons)

    extra = dict(dayparts=dayparts, shifts=shifts, add_ons=add_ons)
    if args.format == "json":
        text = report.to_json(records, summary, weekdays, trend, findings, targets, **extra)
    else:
        text = report.render(records, summary, weekdays, trend, findings, targets,
                             markdown=args.format == "markdown", store=args.store, **extra)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Report written to {args.output}")
    else:
        sys.stdout.write(text)
    return 0
