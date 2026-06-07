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
