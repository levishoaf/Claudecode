"""Render the analysis as plain text, Markdown or JSON."""

import json
from datetime import date

from .advisor import SCORECARD, scorecard


def fmt_value(value, fmt):
    if value is None:
        return "—"
    if fmt == "pct":
        return f"{value:.1f}%"
    if fmt == "money":
        return f"${value:,.2f}"
    if fmt == "minutes":
        return f"{value:.1f} min"
    if fmt == "seconds":
        return f"{value:.0f} sec"
    return f"{value:,.1f}"


def _fmt_change(change, fmt):
    """Signed change; percentages are shown in percentage points."""
    text = f"{abs(change):.1f} pts" if fmt == "pct" else fmt_value(abs(change), fmt)
    if text == (f"{0:.1f} pts" if fmt == "pct" else fmt_value(0, fmt)):
        return "no change"
    return ("+" if change > 0 else "-") + text


def _table(headers, rows, markdown):
    if markdown:
        lines = ["| " + " | ".join(headers) + " |",
                 "|" + "|".join("---" for _ in headers) + "|"]
        lines += ["| " + " | ".join(row) + " |" for row in rows]
        return "\n".join(lines)
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda cells: "  ".join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip()
    return "\n".join([line(headers), line("-" * w for w in widths)] + [line(r) for r in rows])


def _heading(text, markdown, level=2):
    if markdown:
        return "#" * level + " " + text
    return f"{text}\n{'=' * len(text)}" if level == 1 else f"{text}\n{'-' * len(text)}"


def render(records, summary, weekdays, trend, findings, targets, markdown=False, store=None,
           dayparts=None, shifts=None, add_ons=None):
    out = []
    title = "Jimmy John's Store Performance Report" + (f" — {store}" if store else "")
    out.append(_heading(title, markdown, 1))
    out.append(
        f"Period: {records[0].day} to {records[-1].day} ({summary['days']} days)\n"
        f"Net sales: ${summary['total_sales']:,.0f} (avg ${summary['avg_daily_sales']:,.0f}/day)  ·  "
        f"Orders: {summary['total_transactions']:,.0f} (avg {summary['avg_daily_transactions']:,.0f}/day)"
    )
    if summary["days"] < 28:
        out.append(f"Note: only {summary['days']} days of data. Day-of-week and shift results come from "
                   f"just a few samples each — load 4+ weeks for patterns you can trust.")

    # Scorecard
    rows = scorecard(summary, targets)
    out.append(_heading("Scorecard", markdown))
    out.append(_table(
        ["Metric", "Actual", "Target", "Status"],
        [[label, fmt_value(actual, fmt),
          ("≤ " if direction == "max" else "≥ ") + fmt_value(target, fmt),
          "On target" if ok else "NEEDS WORK"]
         for _, label, actual, target, direction, fmt, ok in rows],
        markdown,
    ))
    hits = sum(1 for r in rows if r[-1])
    out.append(f"{hits} of {len(rows)} metrics on target.")

    # Recommendations
    out.append(_heading("Recommendations (highest impact first)", markdown))
    if not findings:
        out.append("Every reported metric is on target. Keep doing what you're doing — "
                   "consider tightening the targets file to push further.")
    for i, f in enumerate(findings, 1):
        impact = (f" — est. ${f.monthly_impact:,.0f}/month {f.impact_kind}"
                  if f.monthly_impact else "")
        out.append((f"### {i}. {f.title}{impact}" if markdown else f"{i}. {f.title}{impact}"))
        body = [f.detail] + f.extra
        indent = "" if markdown else "   "
        lines = [indent + b for b in body]
        lines += [f"{indent}- {a}" for a in f.actions]
        out.append("\n".join(lines))
    savings = sum(f.monthly_impact or 0 for f in findings if f.impact_kind == "savings")
    added = sum(f.monthly_impact or 0 for f in findings if f.impact_kind == "added sales")
    if savings or added:
        out.append(
            f"Estimated monthly opportunity: ${savings:,.0f} in cost savings"
            f" + ${added:,.0f} in added sales."
            "\n(Estimates assume you close the full gap to target; treat them as an upper bound.)"
        )

    # Day of week
    if len(weekdays) > 1:
        out.append(_heading("By day of week", markdown))
        cols = [("Avg sales", "avg_daily_sales", "money"), ("Avg orders", "avg_daily_transactions", "count"),
                ("Avg ticket", "avg_ticket", "money"), ("Labor %", "labor_pct", "pct"),
                ("SPLH", "sales_per_labor_hour", "money"), ("Waste %", "waste_pct", "pct")]
        cols = [c for c in cols if any(m[c[1]] is not None for m in weekdays.values())]
        out.append(_table(
            ["Day"] + [c[0] for c in cols],
            [[day] + [f"{m[key]:,.0f}" if fmt == "count" else fmt_value(m[key], fmt)
                      for _, key, fmt in cols]
             for day, m in weekdays.items()],
            markdown,
        ))

    # Shifts
    if dayparts:
        out.append(_heading("By shift", markdown))
        rows = [[name, fmt_value(m["avg_daily_sales"], "money"),
                 f"{m['avg_daily_transactions']:,.0f}",
                 fmt_value(m["avg_ticket"], "money"),
                 fmt_value(m["labor_pct"], "pct")]
                for name, m in list(dayparts.items()) + list((shifts or {}).items())]
        out.append(_table(["Shift", "Avg sales", "Avg orders", "Avg ticket", "Labor %"], rows, markdown))

    # Add-ons
    if add_ons:
        out.append(_heading("Add-ons per 100 orders", markdown))
        out.append(_table(
            ["Item", "Sold", "Per 100 orders", "Avg price"],
            [[name, f"{a['count']:,.0f}", f"{a['per_100_orders']:.0f}", fmt_value(a["avg_price"], "money")]
             for name, a in add_ons.items()],
            markdown,
        ))
        out.append("Counts are items sold on their own; combos already include a side and a drink.")

    # Trend
    if trend:
        out.append(_heading("Trend: first half vs second half", markdown))
        e, r = trend["earlier_range"], trend["recent_range"]
        out.append(f"Earlier: {e[0]} to {e[1]}  ·  Recent: {r[0]} to {r[1]}")
        labels = {"avg_daily_sales": ("Avg daily sales", "money"),
                  "avg_daily_transactions": ("Avg daily orders", "number")}
        labels.update({k: (v[0], v[3]) for k, v in SCORECARD.items()})
        rows = []
        for key, (label, fmt) in labels.items():
            t = trend["metrics"].get(key)
            if not t:
                continue
            rows.append([label, fmt_value(t["earlier"], fmt), fmt_value(t["recent"], fmt),
                         _fmt_change(t["recent"] - t["earlier"], fmt)])
        out.append(_table(["Metric", "Earlier", "Recent", "Change"], rows, markdown))

    missing = [label for key, (label, *_rest) in SCORECARD.items() if summary.get(key) is None
               and not (key.startswith("online_") and (summary.get("online_mix_pct") is not None
                                                       or summary.get("online_sales_pct") is not None))]
    if missing:
        out.append(_heading("Not measured", markdown))
        out.append("Add these columns to your data to get advice on them: " + ", ".join(missing) + ".")

    return "\n\n".join(out) + "\n"


def to_json(records, summary, weekdays, trend, findings, targets,
            dayparts=None, shifts=None, add_ons=None):
    def default(o):
        if isinstance(o, date):
            return o.isoformat()
        raise TypeError(f"not serializable: {type(o)}")

    payload = {
        "period": {"start": records[0].day, "end": records[-1].day},
        "summary": summary,
        "targets": targets,
        "scorecard": [
            {"metric": key, "label": label, "actual": actual, "target": target,
             "direction": direction, "on_target": ok}
            for key, label, actual, target, direction, _fmt, ok in scorecard(summary, targets)
        ],
        "recommendations": [
            {"metric": f.metric, "title": f.title, "detail": f.detail, "notes": f.extra,
             "actions": f.actions, "estimated_monthly_impact": f.monthly_impact,
             "impact_kind": f.impact_kind or None}
            for f in findings
        ],
        "by_weekday": weekdays,
        "by_shift": dayparts or {},
        "by_weekday_shift": shifts or {},
        "add_ons": add_ons or {},
        "trend": trend,
    }
    return json.dumps(payload, indent=2, default=default) + "\n"
