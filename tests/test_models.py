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


def test_criteria_secondary_toggles_all_off_by_default():
    # Comp selection uses only Sam's 5; bed/bath/garage are handled by the adjustment
    # engine, not by filtering — so all exact-match toggles default OFF.
    c = Criteria()
    assert c.match_beds is False and c.match_baths is False and c.match_garage is False
    assert c.match_type is False


def test_relaxation_records_a_boolean_toggle():
    from mcp_server.models import Relaxation
    r = Relaxation(step="match_garage", **{"from": True, "to": False})
    assert r.from_ is True and r.to is False
    assert r.model_dump(by_alias=True) == {"step": "match_garage", "from": True, "to": False}


def test_adjustment_rules_trimmed_to_config_only():
    from mcp_server.models import AdjustmentRules
    r = AdjustmentRules()
    assert (r.min_comps, r.outlier_iqr, r.drop_outliers) == (4, 1.5, False)
    # invented constants are gone (incl. the removed trend_clamp — the trend is no longer capped)
    assert not hasattr(r, "age_rate")
    assert not hasattr(r, "trend_clamp")
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


def test_coefficient_trace_and_report_payload_models():
    from datetime import date
    from mcp_server.models import (
        PairTrace, CoefficientTrace, Estimate, CompAdjustment,
        ReportComp, ReportPayload, Subject, Comp,
    )
    pt = PairTrace(comp_a="A", comp_b="B", detail="Δ$10,000 over 100 sqft", value=100.0)
    ct = CoefficientTrace(
        factor="size", method="matched_pair", source_type="article-method",
        value=284.0, is_pct=False, confidence="high",
        equation="per-sqft $ = median of Δprice / Δsqft", pairs=[pt],
        aggregate="median of 1 pair = $284/sqft", summary="1 matched pair",
    )
    est = Estimate(point=500_000, low=480_000, high=520_000, confidence="high",
                   per_comp=[], coefficients=[ct])
    assert est.coefficients[0].pairs[0].comp_a == "A"
    subj = Subject(address="S", sqft=1800)
    comp = Comp(address="C", lat=51.0, lng=-114.0, sold_price=500_000,
                sold_date=date(2026, 5, 1), sqft=1800)
    payload = ReportPayload(
        subject=subj, comps=[ReportComp(comp=comp, kept=True)], estimate=est,
        confidence_reasoning="Tight cluster.", target_warnings=["Subject's own sale in pool."],
        verify_next=["Confirm basement."], as_of=date(2026, 6, 10),
    )
    assert payload.comps[0].kept is True
    assert payload.estimate.coefficients[0].value == 284.0
