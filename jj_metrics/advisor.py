"""Turn metrics into prioritized, actionable recommendations."""

from dataclasses import dataclass, field

DAYS_PER_MONTH = 30.4

# metric key -> (label, target key, "max" or "min", format)
SCORECARD = {
    "labor_pct": ("Labor cost %", "labor_pct_max", "max", "pct"),
    "sales_per_labor_hour": ("Sales per labor hour", "sales_per_labor_hour_min", "min", "money"),
    "food_cost_pct": ("Food cost %", "food_cost_pct_max", "max", "pct"),
    "waste_pct": ("Waste %", "waste_pct_max", "max", "pct"),
    "avg_ticket": ("Average ticket", "avg_ticket_min", "min", "money"),
    "avg_delivery_minutes": ("Avg delivery time", "avg_delivery_minutes_max", "max", "minutes"),
    "late_delivery_pct": ("Late deliveries", "late_delivery_pct_max", "max", "pct"),
    "online_mix_pct": ("Online order mix", "online_mix_pct_min", "min", "pct"),
    "catering_pct": ("Catering % of sales", "catering_pct_min", "min", "pct"),
    "complaints_per_1000": ("Complaints / 1,000 orders", "complaints_per_1000_max", "max", "number"),
    "avg_service_seconds": ("In-store service time", "avg_service_seconds_max", "max", "seconds"),
}


@dataclass
class Finding:
    metric: str
    title: str
    detail: str
    actions: list
    monthly_impact: float = None  # estimated $/month, None if not quantifiable
    impact_kind: str = ""         # "savings" or "added sales"
    severity: float = 0.0         # relative gap to target, used to rank unpriced findings
    extra: list = field(default_factory=list)


def scorecard(summary, targets):
    """Rows of (key, label, actual, target, direction, fmt, on_target) for reported metrics."""
    rows = []
    for key, (label, target_key, direction, fmt) in SCORECARD.items():
        actual = summary.get(key)
        if actual is None:
            continue
        target = targets[target_key]
        on_target = actual <= target if direction == "max" else actual >= target
        rows.append((key, label, actual, target, direction, fmt, on_target))
    return rows


def _gap(actual, target, direction):
    """Relative miss vs target (0 when on target)."""
    if not target:
        return 0.0
    miss = actual - target if direction == "max" else target - actual
    return max(miss, 0.0) / target


def _worst_weekday(weekdays, key, highest=True):
    rows = [(name, m[key]) for name, m in weekdays.items() if m.get(key) is not None]
    if len(rows) < 2:
        return None
    return (max if highest else min)(rows, key=lambda x: x[1])


def advise(summary, weekdays, trend, targets):
    """Return a list of Findings, most valuable first."""
    monthly_sales = (summary["avg_daily_sales"] or 0) * DAYS_PER_MONTH
    monthly_tx = (summary["avg_daily_transactions"] or 0) * DAYS_PER_MONTH
    findings = []

    def missed(key):
        actual = summary.get(key)
        if actual is None:
            return None
        _, target_key, direction, _ = SCORECARD[key]
        target = targets[target_key]
        gap = _gap(actual, target, direction)
        return (actual, target, gap) if gap > 0 else None

    # --- Labor -------------------------------------------------------------
    labor = missed("labor_pct")
    splh = missed("sales_per_labor_hour")
    if labor:
        actual, target, gap = labor
        f = Finding(
            metric="labor_pct",
            title="Bring labor cost down to target",
            detail=f"Labor is {actual:.1f}% of sales vs a {target:.1f}% target.",
            monthly_impact=(actual - target) / 100 * monthly_sales,
            impact_kind="savings",
            severity=gap,
            actions=[
                "Build each week's schedule from a sales forecast by hour, not from last week's schedule.",
                "Stagger shift starts and send the first person home as soon as the lunch rush tapers.",
                "Cross-train slicers, wrappers and drivers so fewer people cover slow dayparts.",
                "Set a sales-per-labor-hour goal for each shift and review it with the shift leader daily.",
            ],
        )
        worst = _worst_weekday(weekdays, "labor_pct", highest=True)
        if worst:
            f.extra.append(f"Highest labor % day: {worst[0]} at {worst[1]:.1f}% — start trimming hours there.")
        findings.append(f)
    if splh:
        actual, target, gap = splh
        hours = monthly_sales / actual if actual else None
        excess_hours = hours - monthly_sales / target if hours else None
        f = Finding(
            metric="sales_per_labor_hour",
            title="Raise sales per labor hour",
            detail=(f"Each labor hour produces ${actual:,.2f} in sales vs a ${target:,.2f} target"
                    + (f" — about {excess_hours:,.0f} more hours/month than the sales volume supports."
                       if excess_hours else ".")),
            severity=gap,
            actions=[
                "Match staffing to the hourly sales curve: heavier at 11am–1pm, lean mid-afternoon and late night.",
                "Use slow periods for prep (slicing, bread, veggies) so peak hours need fewer hands.",
            ],
        )
        if labor:
            f.detail += " (Dollar impact is counted under labor cost.)"
        elif excess_hours and summary.get("labor_pct") is not None:
            wage = (summary["labor_pct"] / 100 * monthly_sales) / hours
            f.monthly_impact = excess_hours * wage
            f.impact_kind = "savings"
        worst = _worst_weekday(weekdays, "sales_per_labor_hour", highest=False)
        if worst:
            f.extra.append(f"Lowest SPLH day: {worst[0]} at ${worst[1]:,.2f}/hr.")
        findings.append(f)

    # --- Food cost & waste -------------------------------------------------
    food = missed("food_cost_pct")
    if food:
        actual, target, gap = food
        findings.append(Finding(
            metric="food_cost_pct",
            title="Tighten food cost",
            detail=f"Food cost is {actual:.1f}% of sales vs a {target:.1f}% target.",
            monthly_impact=(actual - target) / 100 * monthly_sales,
            impact_kind="savings",
            severity=gap,
            actions=[
                "Spot-check meat and cheese portions on a scale every shift; over-portioning is the usual culprit.",
                "Do weekly inventory counts on the top 10 cost items and compare actual vs theoretical usage.",
                "Audit comps, voids and employee meals for leakage.",
                "Check invoices against contracted prices before signing for deliveries.",
            ],
        ))
    waste = missed("waste_pct")
    if waste:
        actual, target, gap = waste
        f = Finding(
            metric="waste_pct",
            title="Cut waste",
            detail=f"Waste is {actual:.2f}% of sales vs a {target:.2f}% target.",
            monthly_impact=(actual - target) / 100 * monthly_sales,
            impact_kind="savings",
            severity=gap,
            actions=[
                "Bake bread in smaller, more frequent batches tied to the hourly forecast.",
                "Sell day-old bread instead of tossing it.",
                "Prep sliced veggies and meats to a par level per daypart rather than one big morning batch.",
                "Log every item thrown out with a reason so the biggest sources are visible.",
            ],
        )
        worst = _worst_weekday(weekdays, "waste_pct", highest=True)
        if worst:
            f.extra.append(f"Highest waste day: {worst[0]} at {worst[1]:.2f}% — cut prep pars there first.")
        findings.append(f)

    # --- Revenue -----------------------------------------------------------
    ticket = missed("avg_ticket")
    if ticket:
        actual, target, gap = ticket
        findings.append(Finding(
            metric="avg_ticket",
            title="Grow average ticket",
            detail=(f"Average ticket is ${actual:,.2f} vs a ${target:,.2f} target. "
                    f"Every extra $0.50 per order is about ${0.5 * monthly_tx:,.0f}/month."),
            monthly_impact=(target - actual) * monthly_tx,
            impact_kind="added sales",
            severity=gap,
            actions=[
                "Suggest chips, a cookie or a pickle on every order — at the counter and on the phone.",
                "Offer the upsize (e.g. 8\" to Giant) and a drink as a combo.",
                "Make sure add-on prompts are turned on for app and web orders.",
                "Run a crew contest on add-on attach rate for a few weeks.",
            ],
        ))
    catering = missed("catering_pct")
    if catering:
        actual, target, gap = catering
        findings.append(Finding(
            metric="catering_pct",
            title="Build catering sales",
            detail=f"Catering is {actual:.1f}% of sales vs a {target:.1f}% target.",
            monthly_impact=(target - actual) / 100 * monthly_sales,
            impact_kind="added sales",
            severity=gap,
            actions=[
                "Drop off menus and samples at nearby offices, schools, hospitals and gyms.",
                "Follow up with every catering customer the next day and ask to book a repeat order.",
                "Push box lunches and platters for meetings; mention them on bag stuffers and receipts.",
            ],
        ))
    online = missed("online_mix_pct")
    if online:
        actual, target, gap = online
        findings.append(Finding(
            metric="online_mix_pct",
            title="Shift more orders online",
            detail=f"Online orders are {actual:.1f}% of transactions vs a {target:.1f}% target.",
            severity=gap,
            actions=[
                "Put QR codes for the app and rewards program at the register and on every bag.",
                "Ask in-store guests if they're signed up for rewards.",
                "Confirm your store hours, menu and delivery zone are accurate on every ordering platform.",
            ],
        ))

    # --- Speed & service ---------------------------------------------------
    delivery = missed("avg_delivery_minutes")
    late = missed("late_delivery_pct")
    if delivery or late:
        parts = []
        if delivery:
            parts.append(f"average delivery is {delivery[0]:.1f} min vs a {delivery[1]:.0f} min target")
        if late:
            parts.append(f"{late[0]:.1f}% of deliveries are late vs a {late[1]:.0f}% max")
        findings.append(Finding(
            metric="avg_delivery_minutes" if delivery else "late_delivery_pct",
            title="Speed up delivery",
            detail="Delivery: " + "; ".join(parts) + ".",
            severity=max(delivery[2] if delivery else 0, late[2] if late else 0),
            actions=[
                "Schedule drivers to the peak delivery hours, not the whole shift.",
                "Stage delivery orders in a dedicated spot so drivers grab and go.",
                "Review your delivery radius — trim zones that are routinely late.",
                "Pair orders heading the same direction only when it doesn't push either past target.",
            ],
        ))
        worst = _worst_weekday(weekdays, "avg_delivery_minutes", highest=True)
        if worst:
            findings[-1].extra.append(f"Slowest delivery day: {worst[0]} at {worst[1]:.1f} min.")
    service = missed("avg_service_seconds")
    if service:
        actual, target, gap = service
        findings.append(Finding(
            metric="avg_service_seconds",
            title="Speed up the sandwich line",
            detail=f"In-store service averages {actual:.0f} seconds vs a {target:.0f} second target.",
            severity=gap,
            actions=[
                "Pre-slice and pre-portion before rushes so the line only assembles.",
                "Lock in line positions (bread, meat, veggies, wrap) during peaks.",
                "Time a few orders each shift and post the results.",
            ],
        ))
    complaints = missed("complaints_per_1000")
    if complaints:
        actual, target, gap = complaints
        findings.append(Finding(
            metric="complaints_per_1000",
            title="Reduce customer complaints",
            detail=f"{actual:.1f} complaints per 1,000 orders vs a {target:.1f} target.",
            severity=gap,
            actions=[
                "Categorize every complaint (wrong order, slow, cold, rude) and fix the top category first.",
                "Check each ticket against the sandwich before it's bagged.",
                "Call back unhappy customers the same day.",
            ],
        ))

    # --- Weak day of week --------------------------------------------------
    weakest = _worst_weekday(weekdays, "avg_daily_sales", highest=False)
    avg_daily = summary["avg_daily_sales"]
    if weakest and avg_daily and weakest[1] < 0.8 * avg_daily:
        name, sales = weakest
        lift = (0.9 * avg_daily - sales) * DAYS_PER_MONTH / 7
        findings.append(Finding(
            metric="weekday_sales",
            title=f"Lift {name} sales",
            detail=(f"{name} averages ${sales:,.0f} vs ${avg_daily:,.0f} on a typical day "
                    f"({sales / avg_daily:.0%}). Getting it to 90% of average is worth about "
                    f"${lift:,.0f}/month."),
            monthly_impact=lift,
            impact_kind="added sales",
            severity=1 - sales / avg_daily,
            actions=[
                f"Run a {name}-only rewards offer or local promotion.",
                f"Target catering outreach at {name} meetings and events.",
                f"Staff {name} lean until sales respond.",
            ],
        ))

    # --- Sales trend -------------------------------------------------------
    if trend and "avg_daily_sales" in trend["metrics"]:
        t = trend["metrics"]["avg_daily_sales"]
        change = (t["recent"] - t["earlier"]) / t["earlier"] if t["earlier"] else 0
        if change <= -0.05:
            findings.append(Finding(
                metric="sales_trend",
                title="Reverse the sales decline",
                detail=(f"Average daily sales fell {abs(change):.1%} "
                        f"(${t['earlier']:,.0f} → ${t['recent']:,.0f}) between the first and second half of the period."),
                severity=abs(change),
                actions=[
                    "Check whether the drop is in transactions or ticket size (see the trend table) and target that.",
                    "Look for a local cause: new competitor, road construction, staffing gaps, slower service.",
                    "Re-engage lapsed rewards members with a comeback offer.",
                ],
            ))

    findings.sort(key=lambda f: (f.monthly_impact or 0, f.severity), reverse=True)
    return findings
