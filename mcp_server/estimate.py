from __future__ import annotations
from datetime import date
from statistics import mean
from mcp_server.models import (
    Subject, Comp, AdjustmentRules, Adjustment, CompAdjustment,
)
from mcp_server.comps import months_between


def estimate_trend(comps: list[Comp], rules: AdjustmentRules, *, as_of: date) -> float:
    """Monthly $/sqft trend via least-squares slope of ppsf vs months-old.
    Returns 0.0 if < 4 comps; clamped to ±rules.trend_clamp."""
    if len(comps) < 4:
        return 0.0
    xs = [-months_between(c.sold_date, as_of) for c in comps]  # more recent = larger x
    ys = [c.price_per_sqft for c in comps]
    mx, my = mean(xs), mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    monthly = slope / my if my else 0.0  # fractional change per month
    return round(max(-rules.trend_clamp, min(rules.trend_clamp, monthly)), 5)


def adjust_comp(
    subject: Subject, comp: Comp, rules: AdjustmentRules, *, trend: float, as_of: date
) -> CompAdjustment:
    """Adjust one comp's $/sqft to subject-equivalent via time/age/size line items.
    Pure: `as_of` is passed in so there is no hidden global state."""
    raw_ppsf = comp.price_per_sqft
    months_old = max(months_between(comp.sold_date, as_of), 0)
    adjustments: list[Adjustment] = []

    # Time: bring the sale to "today" using the market trend.
    time_pct = trend * months_old
    adjustments.append(Adjustment(
        factor="time", pct=round(time_pct, 5),
        rationale=f"{months_old} mo old @ {trend*100:.2f}%/mo market trend"))

    # Age: newer subject than comp -> upward; rate per year of difference.
    age_pct = (rules.age_rate * (subject.year_built - comp.year_built)
               if (subject.year_built and comp.year_built) else 0.0)
    adjustments.append(Adjustment(
        factor="age", pct=round(age_pct, 5),
        rationale=f"age diff {(subject.year_built or 0) - (comp.year_built or 0)} yr"))

    # Size: larger comp has lower $/sqft -> adjust toward (smaller) subject.
    size_gap = (comp.sqft - subject.sqft) / subject.sqft
    size_pct = rules.size_elast * size_gap
    adjustments.append(Adjustment(
        factor="size", pct=round(size_pct, 5),
        rationale=f"size gap {size_gap*100:+.0f}%"))

    multiplier = 1.0
    for a in adjustments:
        multiplier *= (1 + a.pct)
    adjusted_ppsf = round(raw_ppsf * multiplier, 2)
    return CompAdjustment(
        address=comp.address,
        raw_price=comp.sold_price,
        raw_ppsf=raw_ppsf,
        adjustments=adjustments,
        adjusted_ppsf=adjusted_ppsf,
        adjusted_price=round(adjusted_ppsf * subject.sqft, 0),
        weight=0.0,  # filled in during reconciliation
    )
