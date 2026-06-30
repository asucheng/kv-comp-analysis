from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from statistics import median, mean
from typing import Optional
from mcp_server.models import (
    Subject, Comp, Disclosure, AdjMethod, SourceType, Confidence, PairTrace,
)
from mcp_server.comps import months_between


@dataclass
class Derivation:
    value: float
    method: AdjMethod
    source_type: SourceType
    evidence: str
    confidence: Confidence
    pairs: list[PairTrace] = field(default_factory=list)
    groups: Optional[dict] = None
    regression: Optional[dict] = None


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


# $/sqft magnitude bound for a SINGLE size pair. Two-sided (|rate| < cap) so it removes only
# the meaningless near-zero-Δsqft explosions (price-noise ÷ a tiny denominator) symmetrically —
# it does NOT drop legitimate negative-slope noise, which must stay so the median is unbiased.
_PPSF_CLAMP = 1000.0

# Matched pairs hold features constant to isolate one variable. We do that STRICTLY first
# (every attribute known on both comps and equal — an unknown drops the pair), and only relax
# to null-safe (unknowns tolerated) when strict yields too few pairs to derive from. Same rule
# for time, size and features; confidence is left to the dispersion-based logic, not adjusted
# for relaxing.
_FEATS = ("beds", "full_baths", "half_baths", "garage", "property_type")
_MIN_STRICT_PAIRS = 3


def _features_match(a: Comp, b: Comp, attrs: tuple[str, ...], *, strict: bool) -> bool:
    """strict: each attr must be KNOWN on both comps and equal (an unknown drops the pair).
    null-safe: an attr disqualifies only if known on both and differing (unknowns tolerated)."""
    for f in attrs:
        va, vb = getattr(a, f), getattr(b, f)
        if strict:
            if va is None or vb is None or va != vb:
                return False
        elif va is not None and vb is not None and va != vb:
            return False
    return True


def derive_time_trend(subject: Subject, comps: list[Comp], *, as_of: date) -> Derivation:
    """Market %/month, measured on SIZE-CONTROLLED data so a size-imbalanced comp set
    can't masquerade as a price trend.
    Rung 1: size-matched pairs across time (size held constant by selection).
    Rung 2: grouping of sales on size-normalized $/sqft.
    Rung 3: least-squares on size-normalized $/sqft (small-N fallback)."""
    if len(comps) < 4:
        return _none("fewer than 4 comps; market trend not estimated")
    months = [max(months_between(c.sold_date, as_of), 0) for c in comps]
    n = len(comps)

    # Rung 1: feature-matched pairs across time. Identical features (beds/baths/garage/type)
    # AND same size (+/-5%), differing only in sale date — so the $/sqft gap is pure market
    # movement, not a bed/bath/quality premium. The size match is kept because $/sqft is not
    # size-invariant (larger homes sell at lower $/sqft), so dropping it lets size leak back
    # in through $/sqft. This mirrors the size/feature derivations, which also isolate one
    # variable by holding the others identical.
    SIZE_TOL = 0.05

    def collect(strict: bool):
        rates: list[float] = []
        pairs: list[PairTrace] = []
        for i in range(n):
            for j in range(i + 1, n):
                a, b = comps[i], comps[j]
                big = max(a.sqft, b.sqft)
                if big == 0 or abs(a.sqft - b.sqft) / big > SIZE_TOL:
                    continue
                if not _features_match(a, b, _FEATS, strict=strict):
                    continue
                mi, mj = months[i], months[j]
                if mi == mj:
                    continue
                (older, om), (recent, rm) = ((a, mi), (b, mj)) if mi > mj else ((b, mj), (a, mi))
                op, rp = older.price_per_sqft, recent.price_per_sqft
                if op <= 0:
                    continue
                r = ((rp - op) / op) / (om - rm)
                rates.append(r)
                pairs.append(PairTrace(
                    comp_a=recent.address, comp_b=older.address,
                    detail=f"${rp:.0f}/sqft ({rm:.0f} mo) vs ${op:.0f}/sqft ({om:.0f} mo), {om-rm:.0f} mo apart",
                    value=round(r, 5)))
        return rates, pairs

    rates, pairs = collect(strict=True)
    if len(rates) < _MIN_STRICT_PAIRS:        # too few clean pairs -> tolerate unknown attrs
        rates, pairs = collect(strict=False)
    if rates:
        # No clamp: the median of feature-matched pairs IS the market rate — clamping it would
        # hide a genuinely hot (or cold) market. Thin/noisy data is surfaced via confidence,
        # not by capping the number.
        per_month = median(rates)
        conf = "high" if len(rates) >= 2 else "medium"
        return Derivation(round(per_month, 5), "matched_pair", "article-method",
                          f"{len(rates)} feature-matched pair(s) across time", conf, pairs=pairs)

    # Rungs 2-3: size-normalize each price to the subject's size, then group / regress.
    marg = linreg_slope([c.sqft for c in comps], [c.sold_price for c in comps])
    marg = marg if (marg is not None and 0 < marg < 1000) else 0.0
    sqft0 = subject.sqft or (sum(c.sqft for c in comps) / n)
    if not sqft0:
        return _none("cannot size-normalize: subject and comp sqft unavailable")
    norm = [(c.sold_price - (c.sqft - sqft0) * marg) / sqft0 for c in comps]

    cut = median(months)
    recent = [(m, p) for m, p in zip(months, norm) if m <= cut]
    older = [(m, p) for m, p in zip(months, norm) if m > cut]
    if len(recent) >= 2 and len(older) >= 2:
        rm, rp = median([m for m, _ in recent]), median([p for _, p in recent])
        om, op = median([m for m, _ in older]), median([p for _, p in older])
        gap = om - rm
        if gap > 0 and op > 0:
            per_month = ((rp - op) / op) / gap
            conf = "medium"
            ev = (f"size-normalized: recent ${rp:.0f}/sqft (~{rm:.0f} mo) vs older "
                  f"${op:.0f}/sqft (~{om:.0f} mo) over {gap:.0f} mo")
            return Derivation(round(per_month, 5), "grouping", "article-method", ev, conf,
                              groups={"recent_ppsf": round(rp), "recent_mo": round(rm),
                                      "older_ppsf": round(op), "older_mo": round(om),
                                      "gap_mo": round(gap)})

    slope = linreg_slope([-m for m in months], norm)
    if slope is None:
        return _none("no time variation across comps")
    my = mean(norm)
    per_month = slope / my if my else 0.0
    return Derivation(round(per_month, 5), "regression", "article-method",
                      f"least-squares on size-normalized $/sqft ({n} comps, small-N)", "low",
                      regression={"n": n, "slope_per_mo": round(per_month, 5)})


def _matched_pair_ppsf(subject: Subject, comps: list[Comp], prices: list[float]) -> Optional[Derivation]:
    """Comps identical in features (beds/baths/garage/type) but different in sqft -> Δprice/Δsqft,
    median over EVERY such pair. No minimum size gap (that injected a concavity bias); the only
    per-pair guard is the two-sided |rate| < cap, which drops near-zero-Δsqft explosions on BOTH
    signs without trimming the legitimate negative noise that keeps the median unbiased.
    Positivity is asserted on the FINAL median, not per pair."""
    n = len(comps)

    def collect(strict: bool):
        rates: list[float] = []
        pairs: list[PairTrace] = []
        for i in range(n):
            for j in range(i + 1, n):
                a, b = comps[i], comps[j]
                dsqft = a.sqft - b.sqft
                if dsqft == 0:                 # no size signal / divide-by-zero
                    continue
                if not _features_match(a, b, _FEATS, strict=strict):
                    continue
                r = (prices[i] - prices[j]) / dsqft
                if abs(r) < _PPSF_CLAMP:        # two-sided: drop only the meaningless explosions
                    rates.append(r)
                    pairs.append(PairTrace(
                        comp_a=a.address, comp_b=b.address,
                        detail=f"Δ${abs(prices[i]-prices[j]):,.0f} over {abs(dsqft):,.0f} sqft",
                        value=round(r, 2)))
        return rates, pairs

    rates, pairs = collect(strict=True)
    if len(rates) < _MIN_STRICT_PAIRS:
        rates, pairs = collect(strict=False)
    if rates:
        rate = median(rates)
        if 0 < rate < _PPSF_CLAMP:         # size adds positive value — checked on the AGGREGATE
            conf = "high" if len(rates) >= 2 else "medium"
            return Derivation(round(rate, 2), "matched_pair", "article-method",
                              f"{len(rates)} matched pair(s); per-sqft median ${rate:.0f}",
                              conf, pairs=pairs)
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
                                  f"${sp:.0f}@{ss:.0f}sqft", "medium",
                                  groups={"large_med_price": round(lp), "large_med_sqft": round(ls),
                                          "small_med_price": round(sp), "small_med_sqft": round(ss),
                                          "rate_per_sqft": round(rate, 2)})

    slope = linreg_slope(sqfts, prices)
    if slope is not None and 0 < slope < 1000:
        return Derivation(round(slope, 2), "regression", "article-method",
                          f"slope of price~sqft over {len(comps)} comps", "low",
                          regression={"n": len(comps), "slope_per_sqft": round(slope, 2)})
    return _none("no usable size spread; size not adjusted")


# Plausibility ceiling for the value of ONE unit of a feature. These are sanity guards
# (our-judgment), not adjustment values — generous enough to admit real (even luxury)
# values, tight enough to reject a confounded derivation (e.g. a "$112k garage" that is
# really a size/quality difference). A per-unit value above the cap falls through.
_FEATURE_CAP = {"beds": 80_000.0, "full_baths": 40_000.0, "half_baths": 15_000.0, "garage": 40_000.0, "year_built": 4_000.0}


def _alike_except(a: Comp, b: Comp, factor: str, *, strict: bool) -> bool:
    """True if two comps are comparable on everything EXCEPT `factor`: size within 10% and the
    OTHER features (the other counts + property_type) match per `strict`. This isolates `factor`
    so the price gap reflects it alone, not the size/quality that correlates with feature counts."""
    big = max(a.sqft, b.sqft)
    if big == 0 or abs(a.sqft - b.sqft) / big > 0.10:
        return False
    others = tuple(f for f in _FEATS if f != factor)
    return _features_match(a, b, others, strict=strict)


def derive_feature_unit(subject: Subject, comps: list[Comp],
                        residuals: list[float], factor: str) -> Derivation:
    """$ per ONE unit of `factor` (beds|baths|garage), on the size/time-netted residual.
    Matched pair (comps alike except `factor`; per-unit = Δresidual/Δcount) -> grouping ->
    regression. Capped by `_FEATURE_CAP` to reject confounded values. Null-safe."""
    cap = _FEATURE_CAP.get(factor, 50_000.0)
    known = [(getattr(c, factor), r) for c, r in zip(comps, residuals)
             if getattr(c, factor) is not None]
    counts = sorted({k for k, _ in known})
    if len(known) < 2 or len(counts) < 2:
        return _none(f"no {factor} variation across comps; not adjusted")

    # Rung 1: matched pairs — control for the confounds by selection, value ONE unit. Δcount is
    # an integer ≥1, so there is no near-zero-denominator explosion: keep every pair (both signs)
    # and let the sanity cap apply to the FINAL median.
    n = len(comps)

    def collect(strict: bool):
        rates: list[float] = []
        pairs: list[PairTrace] = []
        for i in range(n):
            for j in range(i + 1, n):
                fa, fb = getattr(comps[i], factor), getattr(comps[j], factor)
                if (fa is None or fb is None or fa == fb
                        or not _alike_except(comps[i], comps[j], factor, strict=strict)):
                    continue
                r = (residuals[i] - residuals[j]) / (fa - fb)
                rates.append(r)
                pairs.append(PairTrace(
                    comp_a=comps[i].address, comp_b=comps[j].address,
                    detail=f"Δresidual ${abs(residuals[i]-residuals[j]):,.0f} over {abs(fa-fb):g} {factor}",
                    value=round(r, 2)))
        return rates, pairs

    rates, pairs = collect(strict=True)
    if len(rates) < _MIN_STRICT_PAIRS:
        rates, pairs = collect(strict=False)
    if rates:
        per_unit = median(rates)
        if 0 < per_unit <= cap:            # positive & plausible — asserted on the AGGREGATE
            conf = "high" if len(rates) >= 3 else "medium"
            return Derivation(round(per_unit, 2), "matched_pair", "article-method",
                              f"{factor}: {len(rates)} pair(s) alike except {factor} "
                              f"(size within 10%); per-unit median ${per_unit:.0f}", conf, pairs=pairs)

    # Rung 2: grouping (above-median vs at-or-below count). Confound-prone, so capped.
    cut = median([k for k, _ in known])
    hi = [(k, r) for k, r in known if k > cut]
    lo = [(k, r) for k, r in known if k <= cut]
    if hi and lo:
        hk, hr = median([k for k, _ in hi]), median([r for _, r in hi])
        lk, lr = median([k for k, _ in lo]), median([r for _, r in lo])
        dcount = hk - lk
        if dcount > 0:
            per_unit = (hr - lr) / dcount
            if 0 < per_unit <= cap:
                return Derivation(round(per_unit, 2), "grouping", "article-method",
                                  f"{factor}: {hk:g}-count median ${hr:.0f} vs {lk:g}-count "
                                  f"${lr:.0f}", "low",
                                  groups={"hi_count": hk, "hi_resid": round(hr),
                                          "lo_count": lk, "lo_resid": round(lr),
                                          "per_unit": round(per_unit, 2)})

    slope = linreg_slope([k for k, _ in known], [r for _, r in known])
    if slope is not None and 0 < slope <= cap:
        return Derivation(round(slope, 2), "regression", "article-method",
                          f"slope of residual~{factor} over {len(known)} comps", "low",
                          regression={"n": len(known), "slope_per_unit": round(slope, 2)})
    return _none(f"{factor} signal too noisy/confounded to value reliably; not adjusted")


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
                caveat=("Gross vintage is filtered (the +/-10yr band); within the band, "
                        "age is dollar-adjusted when a clean rate is derivable, otherwise "
                        f"left unadjusted. An {'older' if avg_gap>0 else 'newer'} comp set "
                        f"may {direction} the subject's value. Condition/rehab is out of scope.")))
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
