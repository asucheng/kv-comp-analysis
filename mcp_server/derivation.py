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


def derive_time_trend(subject: Subject, comps: list[Comp], *, as_of: date, clamp: float) -> Derivation:
    """Market %/month, measured on SIZE-CONTROLLED data so a size-imbalanced comp set
    can't masquerade as a price trend.
    Rung 1: size-matched pairs across time (size held constant by selection).
    Rung 2: grouping of sales on size-normalized $/sqft.
    Rung 3: least-squares on size-normalized $/sqft (small-N fallback)."""
    if len(comps) < 4:
        return _none("fewer than 4 comps; market trend not estimated")
    months = [max(months_between(c.sold_date, as_of), 0) for c in comps]
    n = len(comps)

    # Rung 1: size-matched pairs across time. Same size (+/-5%) but different sale dates,
    # so any $/sqft difference is pure market movement.
    SIZE_TOL = 0.05
    rates: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = comps[i], comps[j]
            big = max(a.sqft, b.sqft)
            if big == 0 or abs(a.sqft - b.sqft) / big > SIZE_TOL:
                continue
            mi, mj = months[i], months[j]
            if mi == mj:
                continue
            (older, om), (recent, rm) = ((a, mi), (b, mj)) if mi > mj else ((b, mj), (a, mi))
            op, rp = older.price_per_sqft, recent.price_per_sqft
            if op <= 0:
                continue
            rates.append(((rp - op) / op) / (om - rm))
    if rates:
        raw = median(rates)
        per_month = _clamp(raw, clamp)
        conf = "low" if per_month != raw else ("high" if len(rates) >= 2 else "medium")
        return Derivation(round(per_month, 5), "matched_pair", "article-method",
                          f"{len(rates)} size-matched pair(s) across time", conf)

    # Rungs 2-3: size-normalize each price to the subject's size, then group / regress.
    marg = linreg_slope([c.sqft for c in comps], [c.sold_price for c in comps])
    marg = marg if (marg is not None and 0 < marg < 1000) else 0.0
    sqft0 = subject.sqft or (sum(c.sqft for c in comps) / n)
    norm = [(c.sold_price - (c.sqft - sqft0) * marg) / sqft0 for c in comps]

    cut = median(months)
    recent = [(m, p) for m, p in zip(months, norm) if m <= cut]
    older = [(m, p) for m, p in zip(months, norm) if m > cut]
    if len(recent) >= 2 and len(older) >= 2:
        rm, rp = median([m for m, _ in recent]), median([p for _, p in recent])
        om, op = median([m for m, _ in older]), median([p for _, p in older])
        gap = om - rm
        if gap > 0 and op > 0:
            raw = ((rp - op) / op) / gap
            per_month = _clamp(raw, clamp)
            conf = "low" if per_month != raw else "medium"
            ev = (f"size-normalized: recent ${rp:.0f}/sqft (~{rm:.0f} mo) vs older "
                  f"${op:.0f}/sqft (~{om:.0f} mo) over {gap:.0f} mo")
            return Derivation(round(per_month, 5), "grouping", "article-method", ev, conf)

    slope = linreg_slope([-m for m in months], norm)
    if slope is None:
        return _none("no time variation across comps")
    my = mean(norm)
    raw = slope / my if my else 0.0
    per_month = _clamp(raw, clamp)
    return Derivation(round(per_month, 5), "regression", "article-method",
                      f"least-squares on size-normalized $/sqft ({n} comps, small-N)", "low")


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


def compute_disclosures(subject: Subject, comps: list[Comp], *, as_of: date) -> list[Disclosure]:
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
                        f"{'older' if avg_gap>0 else 'newer'} comp set may {direction} "
                        "the subject's value. Condition/rehab is out of scope.")))
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

    # Time-mix: recent vs older comps differing systematically in size can confound the
    # trend even after size-control; surface it.
    if len(comps) >= 4:
        ms = [max(months_between(c.sold_date, as_of), 0) for c in comps]
        cut = median(ms)
        recent_sqft = [c.sqft for c, m in zip(comps, ms) if m <= cut]
        older_sqft = [c.sqft for c, m in zip(comps, ms) if m > cut]
        if recent_sqft and older_sqft:
            rs, os_ = mean(recent_sqft), mean(older_sqft)
            if os_ and abs(rs - os_) / os_ >= 0.08:
                bigger = "larger" if rs > os_ else "smaller"
                out.append(Disclosure(
                    factor="time",
                    skew=f"recent comps average {bigger} ({rs:.0f} vs {os_:.0f} sqft)",
                    direction="unknown",
                    caveat=("Recent and older comps differ in size, so the market trend was "
                            "measured on size-controlled data to avoid a size effect leaking "
                            "in; still, treat the time figure with extra caution.")))

    return out
