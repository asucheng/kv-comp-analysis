from datetime import date
import pytest
from mcp_server.server import build_tools
from mcp_server.models import Subject, Estimate, CrossCheck, FindCompsResult
from tests.stubs import StubCompSource, StubGeocoder

TOOLS = build_tools(source=StubCompSource(), geocoder=StubGeocoder((51.05, -114.07)),
                    as_of=date(2026, 6, 1))
SUBJECT_OVERRIDES = {"sqft": 1800, "year_built": 2000, "property_type": "detached"}


def test_get_subject_geocodes_missing_latlng():
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides={"sqft": 1800})
    assert isinstance(s, Subject)
    assert s.lat == 51.05 and s.lng == -114.07
    assert s.provenance["lat"] == "geocoded" and s.provenance["lng"] == "geocoded"
    assert s.sqft == 1800 and s.provenance["sqft"] == "user"


def test_get_subject_overrides_win_over_geocode():
    s = TOOLS.get_subject("123 Maple Dr", overrides={"lat": 50.0, "lng": -114.0})
    assert s.lat == 50.0 and s.provenance["lat"] == "user"


def test_get_subject_marks_missing_when_unresolvable():
    # source has no record and these fields aren't geocodable/overridden
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides={"sqft": 1800})
    assert s.provenance["year_built"] == "missing"


def test_find_comps_returns_filtered_result():
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    res = TOOLS.find_comps(s)
    assert isinstance(res, FindCompsResult)
    assert len(res.comps) >= 4
    assert all(c.distance_km is not None for c in res.comps)


def test_estimate_value_runs_on_found_comps():
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    res = TOOLS.find_comps(s)
    est = TOOLS.estimate_value(s, res.comps, ladder_depth=len(res.relaxations))
    assert isinstance(est, Estimate)
    assert est.low <= est.point <= est.high


def test_cross_check_returns_verdict():
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    est = TOOLS.estimate_value(s, TOOLS.find_comps(s).comps)
    cc = TOOLS.cross_check(s, est.point)
    assert isinstance(cc, CrossCheck)
    assert cc.verdict


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
