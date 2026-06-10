from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from statistics import median, mean
from typing import Optional
from mcp_server.models import Subject, Comp, Disclosure, AdjMethod, SourceType, Confidence
from mcp_server.comps import months_between


@dataclass
class Derivation:
    value: float
    method: AdjMethod
    source_type: SourceType
    evidence: str
    confidence: Confidence


def _none(reason: str) -> Derivation:
    return Derivation(0.0, "none", "our-judgment", reason, "low")


def linreg_slope(xs: list[float], ys: list[float]) -> Optional[float]:
    """Least-squares slope dy/dx, or None if x has zero variance / <2 points."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    my = sum(ys) / n
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def _clamp(v: float, c: float) -> float:
    return max(-c, min(c, v))


def derive_time_trend(comps: list[Comp], *, as_of: date, clamp: float) -> Derivation:
    """Market-conditions %/month. Grouping of sales (primary) -> regression (small-N fallback)."""
    if len(comps) < 4:
        return _none("fewer than 4 comps; market trend not estimated")
    months = [max(months_between(c.sold_date, as_of), 0) for c in comps]
    ppsf = [c.price_per_sqft for c in comps]

    # Grouping: split at median months into recent vs older; need >=2 each and a time gap.
    cut = median(months)
    recent = [(m, p) for m, p in zip(months, ppsf) if m <= cut]
    older = [(m, p) for m, p in zip(months, ppsf) if m > cut]
    if len(recent) >= 2 and len(older) >= 2:
        rm, rp = median([m for m, _ in recent]), median([p for _, p in recent])
        om, op = median([m for m, _ in older]), median([p for _, p in older])
        gap = om - rm
        if gap > 0 and op > 0:
            per_month = _clamp(((rp - op) / op) / gap, clamp)
            ev = (f"recent comps median ${rp:.0f}/sqft (~{rm:.0f} mo) vs older "
                  f"${op:.0f}/sqft (~{om:.0f} mo) over {gap:.0f} mo")
            return Derivation(round(per_month, 5), "grouping", "article-method", ev, "medium")

    # Regression fallback: slope of ppsf vs months-ago, normalized to a fraction.
    slope = linreg_slope([-m for m in months], ppsf)   # more-recent = larger x
    if slope is None:
        return _none("no time variation across comps")
    my = mean(ppsf)
    per_month = _clamp(slope / my if my else 0.0, clamp)
    return Derivation(round(per_month, 5), "regression", "article-method",
                      f"least-squares over {len(comps)} comps (small-N fallback)", "low")


def _matched_pair_ppsf(subject: Subject, comps: list[Comp], prices: list[float]) -> Optional[Derivation]:
    """Two comps alike except sqft (>=8% apart, same beds/baths/garage) -> Δprice/Δsqft."""
    n = len(comps)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = comps[i], comps[j]
            dsqft = a.sqft - b.sqft
            if a.sqft == 0 or b.sqft == 0 or abs(dsqft) / max(a.sqft, b.sqft) < 0.08:
                continue
            if (a.beds, a.baths, a.garage) != (b.beds, b.baths, b.garage):
                continue
            rate = (prices[i] - prices[j]) / dsqft
            if 0 < rate < 1000:
                return Derivation(round(rate, 2), "matched_pair", "article-method",
                                  f"pair {a.address}/{b.address}: Δ${prices[i]-prices[j]:.0f} "
                                  f"over {dsqft:.0f} sqft", "high")
    return None


def derive_marginal_ppsf(subject: Subject, comps: list[Comp], prices: list[float]) -> Derivation:
    """$ per extra sqft (GLA). matched pair -> grouping (larger vs smaller half) -> regression."""
    if len(comps) < 2:
        return _none("need >=2 comps to derive a size rate")
    pair = _matched_pair_ppsf(subject, comps, prices)
    if pair:
        return pair

    sqfts = [c.sqft for c in comps]
    cut = median(sqfts)
    large = [(s, p) for s, p in zip(sqfts, prices) if s > cut]
    small = [(s, p) for s, p in zip(sqfts, prices) if s <= cut]
    if large and small:
        ls, lp = median([s for s, _ in large]), median([p for _, p in large])
        ss, sp = median([s for s, _ in small]), median([p for _, p in small])
        dsqft = ls - ss
        if dsqft > 0:
            rate = (lp - sp) / dsqft
            if 0 < rate < 1000:
                return Derivation(round(rate, 2), "grouping", "article-method",
                                  f"larger half median ${lp:.0f}@{ls:.0f}sqft vs smaller "
                                  f"${sp:.0f}@{ss:.0f}sqft", "medium")

    slope = linreg_slope(sqfts, prices)
    if slope is not None and 0 < slope < 1000:
        return Derivation(round(slope, 2), "regression", "article-method",
                          f"slope of price~sqft over {len(comps)} comps", "low")
    return _none("no usable size spread; size not adjusted")


def derive_feature_unit(subject: Subject, comps: list[Comp],
                        residuals: list[float], factor: str) -> Derivation:
    """$ per unit of `factor` (beds|baths|garage), on the size/time-netted residual.
    Grouping: comps with above-median count vs at-or-below, per unit of count gap.
    Regression fallback: slope of residual ~ count. Null-safe: only known counts used."""
    known = [(getattr(c, factor), r) for c, r in zip(comps, residuals)
             if getattr(c, factor) is not None]
    counts = sorted({k for k, _ in known})
    if len(known) < 2 or len(counts) < 2:
        return _none(f"no {factor} variation across comps; not adjusted")

    cut = median([k for k, _ in known])
    hi = [(k, r) for k, r in known if k > cut]
    lo = [(k, r) for k, r in known if k <= cut]
    if hi and lo:
        hk, hr = median([k for k, _ in hi]), median([r for _, r in hi])
        lk, lr = median([k for k, _ in lo]), median([r for _, r in lo])
        dcount = hk - lk
        if dcount > 0:
            per_unit = (hr - lr) / dcount
            if 0 < per_unit < 200_000:
                return Derivation(round(per_unit, 2), "grouping", "article-method",
                                  f"{factor}: {hk:g}-count median ${hr:.0f} vs {lk:g}-count "
                                  f"${lr:.0f}", "medium")

    slope = linreg_slope([k for k, _ in known], [r for _, r in known])
    if slope is not None and 0 < slope < 200_000:
        return Derivation(round(slope, 2), "regression", "article-method",
                          f"slope of residual~{factor} over {len(known)} comps", "low")
    return _none(f"{factor} signal too flat; not adjusted")


def compute_disclosures(subject: Subject, comps: list[Comp]) -> list[Disclosure]:
    """Tier-2 imbalance caveats: dimensions we filter but don't adjust."""
    out: list[Disclosure] = []

    # Vintage: mean comp year vs subject; >2 yr one-sided gap is worth flagging.
    years = [c.year_built for c in comps if c.year_built is not None]
    if subject.year_built and years:
        avg_gap = subject.year_built - (sum(years) / len(years))  # +ve => comps older
        if abs(avg_gap) >= 2:
            direction = "understate" if avg_gap > 0 else "overstate"
            out.append(Disclosure(
                factor="age",
                skew=f"comps average {abs(avg_gap):.0f} yr {'older' if avg_gap>0 else 'newer'} than subject",
                direction=direction,
                caveat=("Age is controlled by the +/-10yr filter, not adjusted; an "
                        f"{'older' if avg_gap>0 else 'newer'} comp set may {direction} a "
                        "newer subject. Condition/rehab is out of scope.")))
        else:
            out.append(Disclosure(factor="age", skew="comps balanced in vintage",
                                  direction="unknown", caveat="No material vintage skew."))

    # Location: mean distance + directional clustering hint.
    dists = [c.distance_km for c in comps if c.distance_km is not None]
    if dists:
        out.append(Disclosure(
            factor="location", skew=f"comps average {sum(dists)/len(dists):.1f} km away",
            direction="unknown",
            caveat=("Distance is filtered (<=3km), not adjusted, and we lack per-community "
                    "data; if comps sit in a different-value pocket the baseline may be biased.")))
    return out
