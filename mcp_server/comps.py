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
        if criteria.match_beds and c.beds != subject.beds:
            continue
        c.distance_km = dist
        c.include_reason = (
            f"{dist:.1f} km, {size_diff * 100:+.0f}% size, {months} mo ago"
            + (f", Δage {age_diff} yr" if age_diff is not None else "")
        )
        kept.append(c)
    kept.sort(key=lambda c: _similarity_score(subject, c, as_of))
    return kept, flags
