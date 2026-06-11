from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from statistics import median, mean, pstdev, quantiles
from typing import Optional
from mcp_server.models import (
    Subject, Comp, AdjustmentRules, Overrides, Adjustment, CompAdjustment,
    Estimate, Confidence, CoefficientTrace,
)
from mcp_server.comps import months_between
from mcp_server.derivation import (
    Derivation, derive_time_trend, derive_marginal_ppsf, derive_feature_unit,
    compute_disclosures,
)


@dataclass
class DerivedSet:
    time: Derivation
    size: Derivation
    beds: Derivation
    baths: Derivation
    garage: Derivation


def feat_dollar(subj_count, comp_count, per_unit: float) -> float:
    """Null-safe directional feature dollar: subject richer than comp -> add to comp."""
    if subj_count is None or comp_count is None:
        return 0.0
    return round((subj_count - comp_count) * per_unit, 2)


def _override(dv: Derivation, value) -> Derivation:
    return Derivation(value, dv.method, "our-judgment", f"override (was {dv.value})", "medium")


_UNIT = {"beds": "bed", "baths": "bath", "garage": "garage"}


def _adj(factor, method, source, *, pct=None, dollar=None, evidence, conf) -> Adjustment:
    if pct is not None:
        rationale = f"{factor}: {pct*100:+.2f}% ({evidence})"
    else:
        rationale = f"{factor}: ${dollar:+,.0f} ({evidence})"
    return Adjustment(factor=factor, method_used=method, source_type=source,
                      value_pct=pct, value_dollar=dollar, evidence=evidence,
                      confidence=conf, rationale=rationale)


def _coeff(factor: str, dv: Derivation, *, is_pct: bool, unit: Optional[str] = None) -> CoefficientTrace:
    n = len(dv.pairs)
    if is_pct:
        aggregate = f"median of {n} size-matched pair(s) = {dv.value*100:+.3f}%/mo" if n else dv.evidence
    elif unit:
        aggregate = f"median of {n} matched pair(s) = ${dv.value:,.0f}/{unit}" if n else dv.evidence
    else:
        aggregate = f"median of {n} matched pair(s) = ${dv.value:,.0f}/sqft" if n else dv.evidence

    if dv.method == "matched_pair":
        if is_pct:
            equation = "monthly % = median over size-matched pairs of ((p_recent − p_older)/p_older) / Δmonths"
        elif unit:
            equation = (f"per-{unit} $ = median of Δresidual / Δ{factor} over pairs alike except "
                        f"{factor} (same other features, size within 10%)")
        else:
            equation = "per-sqft $ = median over matched pairs of Δprice / Δsqft"
    elif dv.method == "grouping":
        if is_pct:
            equation = "monthly % = (recent − older median $/sqft) / Δmonths, size-normalized"
        elif unit:
            equation = f"per-{unit} $ = (higher-count − lower-count median residual) / Δcount"
        else:
            equation = "per-sqft $ = (larger-half − smaller-half median price) / Δsqft"
    elif dv.method == "regression":
        kind = "%/mo" if is_pct else (f"$/{unit}" if unit else "$/sqft")
        equation = f"least-squares slope ({kind}) across the comp set"
    else:  # none — not adjusted
        equation = "no usable signal in this comp set; not adjusted"

    return CoefficientTrace(
        factor=factor, method=dv.method, source_type=dv.source_type, value=dv.value,
        is_pct=is_pct, confidence=dv.confidence, equation=equation, pairs=dv.pairs,
        groups=dv.groups, regression=dv.regression, aggregate=aggregate, summary=dv.evidence)


def apply_adjustments(subject: Subject, comp: Comp, derived: DerivedSet, *, as_of: date) -> CompAdjustment:
    """Sequence time -> size -> beds -> baths -> garage on the comp's price."""
    raw_ppsf = comp.price_per_sqft
    months = max(months_between(comp.sold_date, as_of), 0)
    adjustments: list[Adjustment] = []

    time_pct = derived.time.value * months
    p = comp.sold_price * (1 + time_pct)
    adjustments.append(_adj("time", derived.time.method, derived.time.source_type,
                            pct=round(time_pct, 5),
                            evidence=f"{months} mo @ {derived.time.value*100:.2f}%/mo; {derived.time.evidence}",
                            conf=derived.time.confidence))

    size_dollar = -(comp.sqft - subject.sqft) * derived.size.value
    p += size_dollar
    adjustments.append(_adj("size", derived.size.method, derived.size.source_type,
                            dollar=round(size_dollar, 2),
                            evidence=f"{comp.sqft - subject.sqft:+.0f} sqft @ ${derived.size.value:.0f}/sqft; {derived.size.evidence}",
                            conf=derived.size.confidence))

    for factor, dv in (("beds", derived.beds), ("baths", derived.baths), ("garage", derived.garage)):
        sc, cc = getattr(subject, factor), getattr(comp, factor)
        d = feat_dollar(sc, cc, dv.value)
        p += d
        unit = _UNIT[factor]
        # Always express the per-UNIT value × the count gap, so a double-garage comp
        # reads "2 × $/garage", never "$X per double garage".
        if d != 0:
            ev = f"subject {sc:g} vs comp {cc:g} {unit} -> {sc - cc:+g} x ${dv.value:,.0f}/{unit} ({dv.method})"
        else:
            ev = dv.evidence
        adjustments.append(_adj(factor, dv.method, dv.source_type, dollar=d,
                                evidence=ev, conf=dv.confidence))

    adjusted_price = round(p, 0)
    return CompAdjustment(
        address=comp.address, raw_price=comp.sold_price, raw_ppsf=raw_ppsf,
        adjustments=adjustments, adjusted_price=adjusted_price,
        adjusted_ppsf=round(adjusted_price / subject.sqft, 2) if subject.sqft else 0.0,
    )


def remove_outliers(values: list[float], *, iqr_mult: float = 1.5) -> list[int]:
    """Return indices of values within median ± iqr_mult*IQR. No-op if < 4 values."""
    if len(values) < 4:
        return list(range(len(values)))
    q1, _, q3 = quantiles(values, n=4)
    iqr = q3 - q1
    lo, hi = median(values) - iqr_mult * iqr, median(values) + iqr_mult * iqr
    return [i for i, v in enumerate(values) if lo <= v <= hi]


def _confidence(n: int, cov: float, ladder_depth: int, derived: DerivedSet) -> Confidence:
    base: Confidence
    if n < 4 or cov > 0.20 or ladder_depth >= 3:
        base = "low"
    elif n >= 6 and cov <= 0.10 and ladder_depth == 0:
        base = "high"
    else:
        base = "medium"
    # Method strength: if time or size leaned on regression/none, or a Tier-1 derivation
    # reports low confidence, cap at medium.
    weak = {"regression", "none"}
    if (derived.time.method in weak or derived.size.method in weak
            or derived.time.confidence == "low" or derived.size.confidence == "low"):
        if base == "high":
            base = "medium"
    return base


def reconcile(subject: Subject, comps: list[Comp], rules: AdjustmentRules, *,
              as_of: date, ladder_depth: int = 0, overrides: Optional[Overrides] = None) -> Estimate:
    if not comps:
        raise ValueError("reconcile requires at least one comp")
    overrides = overrides or Overrides()
    notes: list[str] = []

    # 1. time
    time = derive_time_trend(subject, comps, as_of=as_of)
    if overrides.time_pct_per_month is not None:
        time = _override(time, overrides.time_pct_per_month)
    tprices = [c.sold_price * (1 + time.value * max(months_between(c.sold_date, as_of), 0))
               for c in comps]

    # 2. size (on time-adjusted prices)
    size = derive_marginal_ppsf(subject, comps, tprices)
    if overrides.marginal_ppsf is not None:
        size = _override(size, overrides.marginal_ppsf)
    sprices = [tp - (c.sqft - subject.sqft) * size.value for tp, c in zip(tprices, comps)]

    # 3-5. features, each netted out before the next
    resid = list(sprices)
    feats: dict[str, Derivation] = {}
    ov = {"beds": overrides.bed_value, "baths": overrides.bath_value, "garage": overrides.garage_value}
    for factor in ("beds", "baths", "garage"):
        dv = derive_feature_unit(subject, comps, resid, factor)
        if ov[factor] is not None:
            dv = _override(dv, ov[factor])
        feats[factor] = dv
        resid = [r - feat_dollar(getattr(subject, factor), getattr(c, factor), dv.value)
                 for r, c in zip(resid, comps)]

    derived = DerivedSet(time, size, feats["beds"], feats["baths"], feats["garage"])
    notes.append(f"time {time.method} {time.value*100:.2f}%/mo; size {size.method} ${size.value:.0f}/sqft")
    for fname in ("beds", "baths", "garage"):
        fdv = feats[fname]
        if fdv.value:
            notes.append(f"{fname}: ${fdv.value:,.0f} per {_UNIT[fname]} ({fdv.method}; {fdv.evidence})")

    per_comp = [apply_adjustments(subject, c, derived, as_of=as_of) for c in comps]

    prices = [ca.adjusted_price for ca in per_comp]
    if rules.drop_outliers:
        keep = remove_outliers(prices, iqr_mult=rules.outlier_iqr)
        if len(keep) < len(prices):
            notes.append(f"dropped {len(prices)-len(keep)} outlier(s)")
        per_comp = [per_comp[i] for i in keep]
        prices = [prices[i] for i in keep]
        comps = [comps[i] for i in keep]   # keep disclosures consistent with the blended set

    point = round(median(prices), 0)
    if len(prices) >= 4:
        q1, _, q3 = quantiles(sorted(prices), n=4)
    else:
        q1, q3 = min(prices), max(prices)
    low, high = round(min(q1, point), 0), round(max(q3, point), 0)

    ppsf_vals = [ca.adjusted_ppsf for ca in per_comp]
    m = mean(ppsf_vals)
    cov = (pstdev(ppsf_vals) / m) if (len(ppsf_vals) > 1 and m) else 0.0
    conf = _confidence(len(per_comp), cov, ladder_depth, derived)
    notes.append(f"{len(per_comp)} comps, $/sqft CoV {cov:.2f}, ladder depth {ladder_depth}")

    coefficients = [
        _coeff("time", time, is_pct=True),
        _coeff("size", size, is_pct=False),
        _coeff("beds", feats["beds"], is_pct=False, unit="bed"),
        _coeff("baths", feats["baths"], is_pct=False, unit="bath"),
        _coeff("garage", feats["garage"], is_pct=False, unit="garage"),
    ]

    return Estimate(point=point, low=low, high=high, confidence=conf, per_comp=per_comp,
                    coefficients=coefficients,
                    disclosures=compute_disclosures(subject, comps, as_of=as_of), method_notes=notes)
