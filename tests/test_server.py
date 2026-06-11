from datetime import date
import pytest
from mcp_server.server import build_tools
from mcp_server.models import Subject, Estimate, CrossCheck, FindCompsResult
from mcp_server.compsource.base import PropertyRecord
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
    # search returns nothing and these fields aren't geocodable/overridden
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides={"sqft": 1800})
    assert s.provenance["year_built"] == "missing"


def _match(slug, addr, **kw):
    return PropertyRecord(address=addr, slug=slug, resolved_address=addr, **kw)


def test_get_subject_resolves_from_top_search_hit():
    top = _match("122-auburn-bay-heights-se-calgary-ab", "122 Auburn Bay Heights SE Calgary AB",
                 sqft=1450, year_built=2006, beds=2, baths=2.1, lat=50.88, lng=-113.96,
                 hd_estimate=537100, community="Auburn Bay")
    tools = build_tools(source=StubCompSource(matches=[top]),
                        geocoder=StubGeocoder((51.05, -114.07)), as_of=date(2026, 6, 1))
    s = tools.get_subject("122 Auburn Bay Heights SE")
    assert s.sqft == 1450 and s.provenance["sqft"] == "honestdoor"
    assert s.resolved_address == "122 Auburn Bay Heights SE Calgary AB"
    assert s.lat == 50.88 and s.provenance["lat"] == "honestdoor"  # search coords, not geocoder


def test_get_subject_returns_match_candidates_for_confirmation():
    matches = [_match("122-auburn-bay-heights-se-calgary-ab", "122 Auburn Bay Heights SE Calgary AB", sqft=1450),
               _match("122-auburn-bay-close-se-calgary-ab", "122 Auburn Bay Close SE Calgary AB", sqft=1961),
               _match("122-auburn-bay-manor-se-calgary-ab", "122 Auburn Bay Manor SE Calgary AB", sqft=1437)]
    tools = build_tools(source=StubCompSource(matches=matches),
                        geocoder=StubGeocoder((51.05, -114.07)), as_of=date(2026, 6, 1))
    s = tools.get_subject("122 Auburn Bay")
    assert s.resolved_address == "122 Auburn Bay Heights SE Calgary AB"
    assert s.match_candidates == ["122 Auburn Bay Close SE Calgary AB",
                                  "122 Auburn Bay Manor SE Calgary AB"]


def test_get_subject_no_match_leaves_resolved_address_none():
    s = TOOLS.get_subject("999 Nowhere St", overrides={"sqft": 1800})
    assert s.resolved_address is None and s.match_candidates == []


def test_find_comps_returns_filtered_result():
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    res = TOOLS.find_comps(s)
    assert isinstance(res, FindCompsResult)
    assert len(res.comps) >= 4
    assert all(c.distance_km is not None for c in res.comps)


def test_find_comps_fetches_at_hard_radius_and_max_lookback():
    # The fetch must cover the widest the ladder ever reaches on the server-bounded
    # axes: radius is a hard 3 km (never widened), recency is fetched at the 12 mo cap.
    calls: dict = {}

    class RecordingSource(StubCompSource):
        def recent_sales(self, *, lat, lng, radius_km, lookback_months, as_of):
            calls["radius_km"] = radius_km
            calls["lookback_months"] = lookback_months
            return super().recent_sales(lat=lat, lng=lng, radius_km=radius_km,
                                        lookback_months=lookback_months, as_of=as_of)

    tools = build_tools(source=RecordingSource(), geocoder=StubGeocoder((51.05, -114.07)),
                        as_of=date(2026, 6, 1))
    s = tools.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    tools.find_comps(s)
    assert calls["radius_km"] == 3.0 and calls["lookback_months"] == 12


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


def test_estimate_value_payload_and_overrides():
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    res = TOOLS.find_comps(s)
    est = TOOLS.estimate_value(s, res.comps, overrides={"marginal_ppsf": 60.0})
    assert est.point > 0
    assert est.per_comp and est.per_comp[0].adjustments
    assert est.disclosures                              # Tier-2 caveats present
    size = next(a for a in est.per_comp[0].adjustments if a.factor == "size")
    assert size.source_type == "our-judgment"           # override re-tags it


def test_render_report_writes_file(tmp_path):
    from datetime import date
    from mcp_server.server import Tools
    from mcp_server.models import (
        Subject, Comp, AdjustmentRules, ReportComp, ReportPayload,
    )
    from mcp_server.estimate import reconcile
    s = Subject(address="138 Cranberry Place SE", resolved_address="138 Cranberry Place SE",
                lat=51.0, lng=-114.0, sqft=1416, year_built=2007, beds=3, baths=3, garage=1)
    comps = [Comp(address=a, lat=51.0, lng=-114.0, sold_price=p, sold_date=date(2026, 4, 1),
                  sqft=sq, year_built=2007, beds=3, baths=3, garage=2, distance_km=0.2)
             for a, p, sq in [("71 Cranberry", 536_500, 1429), ("78 Cranberry", 560_000, 1425),
                              ("420 Cranberry", 535_000, 1356), ("389 Cranberry", 558_500, 1358)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 10))
    payload = ReportPayload(subject=s, comps=[ReportComp(comp=c) for c in comps], estimate=est,
                            confidence_reasoning="ok", as_of=date(2026, 6, 10))
    tools = Tools(source=None, as_of=date(2026, 6, 10))
    path = tools.render_report(payload, out_dir=str(tmp_path))
    import os
    assert os.path.isabs(path) and os.path.exists(path)
    assert path.endswith("138-cranberry-place-se-2026-06-10.html")
    assert "<details" in open(path, encoding="utf-8").read()
