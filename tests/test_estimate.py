from datetime import date
import pytest
from mcp_server.models import Subject, Comp, AdjustmentRules, Overrides, Estimate
from mcp_server.estimate import feat_dollar, apply_adjustments, reconcile
from mcp_server.derivation import (
    Derivation, derive_time_trend, derive_marginal_ppsf, derive_feature_unit,
)
from mcp_server.estimate import DerivedSet

AS_OF = date(2026, 6, 1)


def _subject(sqft=2000, yb=1985, beds=3, baths=2, garage=2):
    return Subject(address="S", lat=51.05, lng=-114.08, sqft=sqft, year_built=yb,
                   beds=beds, baths=baths, garage=garage)


def _comp(price, sqft=2000, yb=1985, beds=3, baths=2, garage=2, d=date(2026, 5, 1), addr="c"):
    c = Comp(address=addr, lat=51.05, lng=-114.08, sold_price=price, sold_date=d,
             sqft=sqft, year_built=yb, beds=beds, baths=baths, garage=garage)
    c.distance_km = 0.6
    return c


def _flat(v):  # a no-op derivation
    return Derivation(v, "none", "our-judgment", "n/a", "low")


def test_feat_dollar_is_null_safe_and_directional():
    assert feat_dollar(3, 2, 5000) == 5000.0    # subject has more -> add to comp
    assert feat_dollar(2, 3, 5000) == -5000.0
    assert feat_dollar(None, 2, 5000) == 0.0
    assert feat_dollar(3, None, 5000) == 0.0


def test_apply_size_brings_larger_comp_down():
    s = _subject(sqft=2000)
    c = _comp(880_000, sqft=2200)               # 200 sqft larger
    derived = DerivedSet(_flat(0.0), Derivation(50.0, "grouping", "article-method", "x", "medium"),
                         _flat(0.0), _flat(0.0), _flat(0.0))
    ca = apply_adjustments(s, c, derived, as_of=AS_OF)
    size = next(a for a in ca.adjustments if a.factor == "size")
    assert size.value_dollar == -10000.0        # 200 * 50, subtracted
    assert ca.adjusted_price == 870_000.0


def test_reconcile_blends_by_median_and_emits_payload():
    s = _subject(sqft=2000)
    comps = [_comp(800_000), _comp(810_000), _comp(795_000),
             _comp(805_000), _comp(800_000), _comp(812_000)]
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF, ladder_depth=0)
    assert isinstance(est, Estimate)
    assert est.low <= est.point <= est.high
    assert len(est.per_comp) == 6
    assert est.disclosures                       # Tier-2 caveats present
    # no weight field leaked through
    assert not hasattr(est.per_comp[0], "weight")


def test_reconcile_respects_overrides():
    s = _subject(sqft=2000)
    comps = [_comp(800_000, sqft=2200), _comp(802_000, sqft=2200),
             _comp(800_000, sqft=2000), _comp(801_000, sqft=2000)]
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF,
                    overrides=Overrides(marginal_ppsf=100.0))
    size = next(a for a in est.per_comp[0].adjustments if a.factor == "size")
    assert size.source_type == "our-judgment"   # _override re-tags as our-judgment
    # override applied: a 200-sqft-larger comp gets -$20,000
    big = next((ca for ca in est.per_comp if ca.raw_price in (800_000, 802_000) and ca.raw_ppsf < 380), None)
    assert big is not None
    assert any(a.factor == "size" and a.value_dollar == -20000.0 for a in big.adjustments)


def test_reconcile_empty_raises():
    with pytest.raises(ValueError):
        reconcile(_subject(), [], AdjustmentRules(), as_of=AS_OF)


def test_estimate_exposes_coefficient_traces():
    from datetime import date
    from mcp_server.models import Subject, Comp, AdjustmentRules
    from mcp_server.estimate import reconcile
    s = Subject(address="S", lat=51.05, lng=-114.08, sqft=1800, year_built=1985,
                beds=3, baths=2, garage=2)
    comps = [Comp(address=a, lat=51.05, lng=-114.08, sold_price=p, sold_date=date(2026, 5, 1),
                  sqft=sq, year_built=1985, beds=3, baths=2, garage=g)
             for a, p, sq, g in [("a", 700_000, 1800, 1), ("b", 712_000, 1800, 2),
                                 ("c", 705_000, 2000, 1), ("d", 718_000, 2000, 2)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 1))
    factors = [c.factor for c in est.coefficients]
    assert factors == ["time", "size", "beds", "baths", "garage"]
    size = next(c for c in est.coefficients if c.factor == "size")
    assert size.is_pct is False and size.value > 0  # positive $/sqft derived
    time = next(c for c in est.coefficients if c.factor == "time")
    assert time.is_pct is True


def test_coefficient_equation_adapts_to_grouping_method():
    from datetime import date
    from mcp_server.models import Subject, Comp, AdjustmentRules
    from mcp_server.estimate import reconcile
    s = Subject(address="S", lat=51.05, lng=-114.08, sqft=2000, year_built=1985,
                beds=3, baths=2, garage=2)
    # size spread present, but beds differ across the size gap -> no size matched pair
    # -> derive_marginal_ppsf falls to the grouping rung
    comps = [Comp(address=a, lat=51.05, lng=-114.08, sold_price=p, sold_date=date(2026, 5, 1),
                  sqft=sq, year_built=1985, beds=b, baths=2, garage=2)
             for a, p, sq, b in [("a", 680_000, 1800, 3), ("b", 690_000, 1810, 3),
                                 ("c", 760_000, 2200, 4), ("d", 770_000, 2210, 4)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 1))
    size = next(c for c in est.coefficients if c.factor == "size")
    assert size.method == "grouping"
    assert size.groups is not None and not size.pairs
    assert "half" in size.equation  # equation reflects grouping, not matched-pair


def test_feature_equation_discloses_size_constraint():
    # Feature pairs are held within 10% size of each other (so leftover size-adjustment error
    # can't leak into the feature value). The report's methodology line must say so, not hide it.
    from mcp_server.models import Subject, Comp, AdjustmentRules
    from mcp_server.estimate import reconcile
    s = Subject(address="S", lat=51.05, lng=-114.08, sqft=1800, year_built=1985,
                beds=3, baths=2, garage=2, property_type="detached")
    comps = [Comp(address=a, lat=51.05, lng=-114.08, sold_price=p, sold_date=date(2026, 5, 1),
                  sqft=sq, year_built=1985, beds=3, baths=2, garage=g, property_type="detached")
             for a, p, sq, g in [("a", 700_000, 1800, 1), ("b", 712_000, 1800, 2),
                                 ("c", 705_000, 1850, 1), ("d", 718_000, 1850, 2)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 1))
    garage = next(c for c in est.coefficients if c.factor == "garage")
    assert garage.method == "matched_pair"
    assert "10%" in garage.equation            # the size constraint is disclosed
    assert "10%" in garage.aggregate or "10%" in garage.summary
