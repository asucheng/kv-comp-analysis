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
        == (3.0, 0.20, 6, 10, 4)


def test_criteria_secondary_toggles_default_on_except_type():
    # beds/baths/garage matching is strict-by-default (null-safe) so the ladder can
    # relax it; property-type matching stays OFF (subject type is often unknown and
    # the filter is not null-safe).
    c = Criteria()
    assert c.match_beds is True and c.match_baths is True and c.match_garage is True
    assert c.match_type is False


def test_relaxation_records_a_boolean_toggle():
    from mcp_server.models import Relaxation
    r = Relaxation(step="match_garage", **{"from": True, "to": False})
    assert r.from_ is True and r.to is False
    assert r.model_dump(by_alias=True) == {"step": "match_garage", "from": True, "to": False}


def test_adjustment_rules_defaults():
    r = AdjustmentRules()
    assert r.age_rate == 0.005
    assert r.size_elast == 0.20
    assert r.trend_clamp == 0.02
