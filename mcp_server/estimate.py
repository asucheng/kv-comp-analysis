from __future__ import annotations
from datetime import date
from statistics import mean, median, pstdev, quantiles
from mcp_server.models import (
    Subject, Comp, AdjustmentRules, Adjustment, CompAdjustment, Estimate, Confidence,
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


def remove_outliers(values: list[float], *, iqr_mult: float = 1.5) -> list[int]:
    """Return indices of values within median ± iqr_mult*IQR. No-op if < 4 values."""
    if len(values) < 4:
        return list(range(len(values)))
    q1, _, q3 = quantiles(values, n=4)
    iqr = q3 - q1
    lo, hi = median(values) - iqr_mult * iqr, median(values) + iqr_mult * iqr
    return [i for i, v in enumerate(values) if lo <= v <= hi]


def comp_weight(subject: Subject, comp: Comp, rules: AdjustmentRules, *, as_of: date) -> float:
    dist = comp.distance_km if comp.distance_km is not None else 0.0
    size_pct = abs(comp.sqft - subject.sqft) / subject.sqft
    age_diff = abs((comp.year_built or subject.year_built) - subject.year_built)
    months = max(months_between(comp.sold_date, as_of), 0)
    denom = (1 + rules.weight_a * dist + rules.weight_b * size_pct
             + rules.weight_c * age_diff + rules.weight_d * months)
    return round(1 / denom, 4)


def _confidence(n: int, cov: float, ladder_depth: int) -> Confidence:
    if n < 4 or cov > 0.20 or ladder_depth >= 3:
        return "low"
    if n >= 6 and cov <= 0.10 and ladder_depth == 0:
        return "high"
    return "medium"


def reconcile(
    subject: Subject, comps: list[Comp], rules: AdjustmentRules, *,
    as_of: date, ladder_depth: int = 0,
) -> Estimate:
    notes: list[str] = []
    trend = estimate_trend(comps, rules, as_of=as_of)
    notes.append(f"Market trend applied: {trend*100:.2f}%/mo")
    adjusted = [adjust_comp(subject, c, rules, trend=trend, as_of=as_of) for c in comps]

    kept_idx = remove_outliers([ca.adjusted_ppsf for ca in adjusted],
                               iqr_mult=rules.outlier_iqr)
    if len(kept_idx) < len(adjusted):
        notes.append(f"Dropped {len(adjusted) - len(kept_idx)} outlier comp(s)")
    kept = [adjusted[i] for i in kept_idx]
    kept_comps = [comps[i] for i in kept_idx]

    for ca, c in zip(kept, kept_comps):
        ca.weight = comp_weight(subject, c, rules, as_of=as_of)

    wsum = sum(ca.weight for ca in kept) or 1.0
    reconciled_ppsf = sum(ca.adjusted_ppsf * ca.weight for ca in kept) / wsum
    point = round(reconciled_ppsf * subject.sqft, 0)

    ppsf_vals = sorted(ca.adjusted_ppsf for ca in kept)
    if len(ppsf_vals) >= 4:
        q1, _, q3 = quantiles(ppsf_vals, n=4)
    else:
        q1, q3 = ppsf_vals[0], ppsf_vals[-1]
    low, high = round(q1 * subject.sqft, 0), round(q3 * subject.sqft, 0)

    m = mean(ppsf_vals)
    cov = (pstdev(ppsf_vals) / m) if (len(ppsf_vals) > 1 and m) else 0.0
    conf = _confidence(len(kept), cov, ladder_depth)
    notes.append(f"{len(kept)} comps, $/sqft CoV {cov:.2f}, ladder depth {ladder_depth}")

    return Estimate(point=point, low=low, high=high, confidence=conf,
                    per_comp=kept, method_notes=notes)
