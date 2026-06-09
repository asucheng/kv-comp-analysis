from __future__ import annotations
from datetime import date
from mcp_server.models import Subject, Comp, Criteria
from mcp_server.geo import haversine_km


def months_between(earlier: date, as_of: date) -> int:
    """Whole months from `earlier` to `as_of` (negative if earlier is in the future)."""
    return (as_of.year - earlier.year) * 12 + (as_of.month - earlier.month)


def _similarity_score(subject: Subject, c: Comp, as_of: date) -> float:
    """Lower = more similar. Composite over distance, size, age, recency."""
    dist = c.distance_km if c.distance_km is not None else 0.0
    size_diff = abs(c.sqft - subject.sqft) / subject.sqft
    if subject.year_built and c.year_built:
        age_diff = abs(c.year_built - subject.year_built)
    else:
        age_diff = 0
    months = max(months_between(c.sold_date, as_of), 0)
    return dist / 10 + size_diff + age_diff / 20 + months / 24


def _attr_mismatch(subj_val, comp_val) -> bool:
    """True only when BOTH values are known and differ — so a missing bed/bath/garage
    value never silently drops a comp (garage in particular is often unknown)."""
    return subj_val is not None and comp_val is not None and subj_val != comp_val


def filter_and_rank(
    subject: Subject, candidates: list[Comp], criteria: Criteria, *, as_of: date
) -> tuple[list[Comp], list[str]]:
    """Apply Sam's 5 (+secondary) filters, annotate, and rank by similarity."""
    flags: list[str] = []
    kept: list[Comp] = []
    for c in candidates:
        dist = haversine_km(subject.lat, subject.lng, c.lat, c.lng)
        if dist > criteria.radius_km:
            continue
        size_diff = abs(c.sqft - subject.sqft) / subject.sqft
        if size_diff > criteria.size_pct:
            continue
        months = months_between(c.sold_date, as_of)
        if months < 0 or months > criteria.lookback_months:
            continue
        age_diff = None
        if subject.year_built and c.year_built:
            age_diff = abs(c.year_built - subject.year_built)
            if age_diff > criteria.age_years:
                continue
        if criteria.match_type and c.property_type != subject.property_type:
            continue
        if criteria.match_beds and _attr_mismatch(subject.beds, c.beds):
            continue
        if criteria.match_baths and _attr_mismatch(subject.baths, c.baths):
            continue
        if criteria.match_garage and _attr_mismatch(subject.garage, c.garage):
            continue
        kept_c = c.model_copy()
        kept_c.distance_km = dist
        kept_c.include_reason = (
            f"{dist:.1f} km, {size_diff * 100:+.0f}% size, {months} mo ago"
            + (f", Δage {age_diff} yr" if age_diff is not None else "")
        )
        kept.append(kept_c)
    kept.sort(key=lambda c: _similarity_score(subject, c, as_of))
    return kept, flags


from mcp_server.models import Relaxation, FindCompsResult

# Ordered relaxation ladder: (dimension, new_value), applied cumulatively.
# Sam's hard limits (radius 3km, size 20%, age 10yr) NEVER widen — if nothing
# qualifies within them the honest answer is "no comps." The only sanctioned widen is
# recency 6 -> 12 months. Past that we loosen the secondary exact-match toggles,
# least-defining first (garage, then baths, beds, and finally property type).
LADDER: list[tuple[str, float | bool]] = [
    ("lookback_months", 12),
    ("match_garage", False),
    ("match_baths", False),
    ("match_beds", False),
    ("match_type", False),
]


def find_with_ladder(
    subject: Subject, candidates: list[Comp], criteria: Criteria, *, as_of: date
) -> FindCompsResult:
    """Filter with Sam's 5; if under min_comps, relax one ladder step at a time."""
    current = criteria.model_copy()
    relaxations: list[Relaxation] = []
    flags: list[str] = []

    # Honesty: if an exact-match toggle is on but the subject's value is unknown, the
    # constraint can't be applied — say so rather than appearing to enforce it.
    for attr, on in (("beds", criteria.match_beds), ("baths", criteria.match_baths),
                     ("garage", criteria.match_garage)):
        if on and getattr(subject, attr) is None:
            flags.append(f"{attr} match requested but subject {attr} unknown — constraint skipped")

    kept, _ = filter_and_rank(subject, candidates, current, as_of=as_of)
    ladder = iter(LADDER)
    while len(kept) < criteria.min_comps:
        step = next(ladder, None)
        if step is None:
            flags.append(
                f"Insufficient comps: found {len(kept)} of {criteria.min_comps} "
                "after exhausting the widening ladder."
            )
            break
        dim, new_val = step
        old_val = getattr(current, dim)
        # Only apply a step that actually LOOSENS. A toggle loosens by going
        # True -> False; a numeric limit loosens by increasing. (bool is checked
        # first since it's a subclass of int.)
        if isinstance(new_val, bool):
            if old_val == new_val:        # toggle already off — nothing to relax
                continue
        elif new_val <= old_val:          # numeric not an increase — skip
            continue
        setattr(current, dim, new_val)
        relaxations.append(Relaxation(step=dim, **{"from": old_val, "to": new_val}))
        flags.append(f"Relaxed {dim}: {old_val} -> {new_val}")
        kept, _ = filter_and_rank(subject, candidates, current, as_of=as_of)

    return FindCompsResult(
        comps=kept,
        candidates_considered=len(candidates),
        relaxations=relaxations,
        flags=flags,
    )
