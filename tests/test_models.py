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


def test_adjustment_rules_trimmed_to_config_only():
    from mcp_server.models import AdjustmentRules
    r = AdjustmentRules()
    assert (r.trend_clamp, r.min_comps, r.outlier_iqr, r.drop_outliers) == (0.02, 4, 1.5, False)
    # invented constants are gone
    assert not hasattr(r, "age_rate")
    assert not hasattr(r, "size_elast")
    assert not hasattr(r, "weight_a")


def test_adjustment_payload_shape():
    from mcp_server.models import Adjustment
    a = Adjustment(factor="size", method_used="grouping", source_type="article-method",
                   value_dollar=-10000.0, evidence="8 comps, grouped", confidence="medium",
                   rationale="200 sqft larger x $50/sqft")
    assert a.value_pct is None and a.value_dollar == -10000.0


def test_disclosure_shape():
    from mcp_server.models import Disclosure
    d = Disclosure(factor="age", skew="comps avg 5 yr older", direction="understate",
                   caveat="older set may understate a newer subject")
    assert d.source_type == "our-judgment"


def test_overrides_all_optional():
    from mcp_server.models import Overrides
    o = Overrides()
    assert o.marginal_ppsf is None and o.garage_value is None
    assert Overrides(marginal_ppsf=50.0).marginal_ppsf == 50.0
