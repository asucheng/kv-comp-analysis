from datetime import date
from mcp_server.models import Subject, Comp, Criteria, AdjustmentRules


def test_subject_defaults_and_provenance():
    s = Subject(address="123 Maple Dr, Calgary, AB")
    assert s.community is None
    assert s.provenance == {}


def test_comp_computes_price_per_sqft():
    c = Comp(address="1 A St", lat=51.0, lng=-114.0, sold_price=800_000,
             sold_date=date(2026, 1, 16), sqft=2000)
    assert c.price_per_sqft == 400.0


def test_criteria_defaults_match_sams_rules():
    c = Criteria()
    assert (c.radius_km, c.size_pct, c.lookback_months, c.age_years, c.min_comps) \
        == (3.0, 0.20, 12, 10, 4)


def test_adjustment_rules_defaults():
    r = AdjustmentRules()
    assert r.age_rate == 0.005
    assert r.size_elast == 0.20
    assert r.trend_clamp == 0.02
