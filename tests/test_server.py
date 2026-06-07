from datetime import date
from mcp_server.server import build_tools
from mcp_server.compsource.synthetic import SyntheticCompSource
from mcp_server.models import Subject, Estimate, CrossCheck, FindCompsResult

TOOLS = build_tools(source=SyntheticCompSource(seed=42), as_of=date(2026, 6, 1))


def test_get_subject_fills_attrs_and_provenance():
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides={"sqft": 2100})
    assert isinstance(s, Subject)
    assert s.sqft == 2100 and s.provenance["sqft"] == "user"
    assert s.community and s.provenance["community"] == "honestdoor"


def test_get_subject_marks_missing():
    s = TOOLS.get_subject("Unknown Rd")
    assert "year_built" in s.provenance


def test_find_comps_returns_filtered_result():
    s = TOOLS.get_subject("123 Maple Dr, Calgary")
    res = TOOLS.find_comps(s)
    assert isinstance(res, FindCompsResult)
    assert all(c.distance_km is not None for c in res.comps)


def test_estimate_value_runs_on_found_comps():
    s = TOOLS.get_subject("123 Maple Dr, Calgary")
    res = TOOLS.find_comps(s)
    est = TOOLS.estimate_value(s, res.comps, ladder_depth=len(res.relaxations))
    assert isinstance(est, Estimate)
    assert est.low <= est.point <= est.high


def test_cross_check_compares_to_avm():
    s = TOOLS.get_subject("123 Maple Dr, Calgary")
    est = TOOLS.estimate_value(s, TOOLS.find_comps(s).comps)
    cc = TOOLS.cross_check(s, est.point)
    assert isinstance(cc, CrossCheck)
    assert cc.verdict


import pytest


def test_find_comps_raises_clear_error_when_subject_missing_geo():
    s = Subject(address="Unknown Rd")  # no lat/lng/sqft
    with pytest.raises(ValueError) as exc:
        TOOLS.find_comps(s)
    msg = str(exc.value).lower()
    assert "lat" in msg and "sqft" in msg


def test_relaxation_serializes_with_from_alias():
    from mcp_server.models import Relaxation
    r = Relaxation(step="radius_km", **{"from": 3.0, "to": 5.0})
    d = r.model_dump(by_alias=True)
    assert d == {"step": "radius_km", "from": 3.0, "to": 5.0}
