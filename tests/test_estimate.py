from datetime import date
from mcp_server.models import Subject, Comp, AdjustmentRules
from mcp_server.estimate import estimate_trend, adjust_comp

AS_OF = date(2026, 6, 1)


def _subject(sqft=2000, yb=1985):
    return Subject(address="S", lat=51.05, lng=-114.08, sqft=sqft, year_built=yb)


def _comp(price, sqft, yb, d=date(2026, 5, 1)):
    return Comp(address="c", lat=51.05, lng=-114.08, sold_price=price,
                sold_date=d, sqft=sqft, year_built=yb)


def test_trend_zero_with_few_comps():
    assert estimate_trend([_comp(800_000, 2000, 1985)], AdjustmentRules(), as_of=AS_OF) == 0.0


def test_trend_is_clamped():
    comps = [_comp(700_000, 2000, 1985, date(2025, 6, 1)),
             _comp(750_000, 2000, 1985, date(2025, 9, 1)),
             _comp(820_000, 2000, 1985, date(2026, 1, 1)),
             _comp(900_000, 2000, 1985, date(2026, 5, 1))]
    t = estimate_trend(comps, AdjustmentRules(), as_of=AS_OF)
    assert -0.02 <= t <= 0.02


def test_adjust_comp_age_premium_for_newer_subject():
    s = _subject(sqft=2000, yb=1990)
    c = _comp(800_000, 2000, yb=1980)  # comp 10 yrs older -> subject newer -> upward age adj
    ca = adjust_comp(s, c, AdjustmentRules(), trend=0.0, as_of=AS_OF)
    age_adj = next(a for a in ca.adjustments if a.factor == "age")
    assert age_adj.pct > 0
    assert ca.adjusted_ppsf > ca.raw_ppsf


def test_adjust_comp_size_larger_comp_adjusts_up():
    s = _subject(sqft=2000, yb=1985)
    c = _comp(880_000, 2200, yb=1985)  # comp 10% larger -> lower $/sqft -> adjust up
    ca = adjust_comp(s, c, AdjustmentRules(), trend=0.0, as_of=AS_OF)
    size_adj = next(a for a in ca.adjustments if a.factor == "size")
    assert size_adj.pct > 0


def test_adjusted_price_uses_subject_sqft():
    s = _subject(sqft=2000, yb=1985)
    c = _comp(800_000, 2000, yb=1985)
    ca = adjust_comp(s, c, AdjustmentRules(), trend=0.0, as_of=AS_OF)
    assert ca.adjusted_price == round(ca.adjusted_ppsf * s.sqft, 0)


# ---------------------------------------------------------------------------
# Task 7: reconciliation, outliers, weighting, confidence
# ---------------------------------------------------------------------------
from mcp_server.estimate import remove_outliers, comp_weight, reconcile
from mcp_server.models import Estimate


def test_remove_outliers_drops_extreme():
    vals = [400, 410, 420, 430, 1000]
    kept_idx = remove_outliers(vals, iqr_mult=1.5)
    assert 4 not in kept_idx and set(kept_idx) == {0, 1, 2, 3}


def test_comp_weight_closer_comp_weighs_more():
    s = _subject()
    near = _comp(800_000, 2000, 1985); near.distance_km = 0.5
    far = _comp(800_000, 2000, 1985); far.distance_km = 2.5
    wn = comp_weight(s, near, AdjustmentRules(), as_of=AS_OF)
    wf = comp_weight(s, far, AdjustmentRules(), as_of=AS_OF)
    assert wn > wf


def test_reconcile_produces_estimate_with_range_and_confidence():
    s = _subject(sqft=2000, yb=1985)
    comps = [_comp(800_000, 2000, 1985), _comp(810_000, 2010, 1986),
             _comp(795_000, 1990, 1984), _comp(805_000, 2005, 1985),
             _comp(800_000, 2000, 1985), _comp(812_000, 2015, 1987)]
    for c in comps:
        c.distance_km = 0.6
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF, ladder_depth=0)
    assert isinstance(est, Estimate)
    assert est.low <= est.point <= est.high
    assert est.confidence == "high"      # >=6 comps, tight dispersion, no widening
    assert len(est.per_comp) >= 4


def test_reconcile_low_confidence_when_sparse():
    s = _subject()
    comps = [_comp(800_000, 2000, 1985), _comp(900_000, 2000, 1985)]
    for c in comps:
        c.distance_km = 0.6
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF, ladder_depth=0)
    assert est.confidence == "low"       # < 4 comps


import pytest


def test_comp_weight_handles_subject_without_year_built():
    s = Subject(address="S", lat=51.05, lng=-114.08, sqft=2000)  # no year_built
    c = _comp(800_000, 2000, 1985); c.distance_km = 0.6
    w = comp_weight(s, c, AdjustmentRules(), as_of=AS_OF)
    assert w > 0


def test_reconcile_range_contains_point_with_skewed_weights():
    s = _subject(sqft=2000, yb=1985)
    # one very close, high-ppsf comp skews the weighted mean toward the top
    comps = [_comp(1_200_000, 2000, 1985), _comp(800_000, 2000, 1985),
             _comp(805_000, 2000, 1985), _comp(795_000, 2000, 1985),
             _comp(800_000, 2000, 1985)]
    comps[0].distance_km = 0.05
    for c in comps[1:]:
        c.distance_km = 2.5
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF, ladder_depth=0)
    assert est.low <= est.point <= est.high


def test_reconcile_empty_comps_raises():
    s = _subject()
    with pytest.raises(ValueError):
        reconcile(s, [], AdjustmentRules(), as_of=AS_OF, ladder_depth=0)
