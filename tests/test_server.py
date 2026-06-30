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
    # Geocode-first: coordinates come from the geocoder (authoritative), NOT the
    # fuzzy search hit — attributes still come from the matched listing.
    assert s.lat == 51.05 and s.provenance["lat"] == "geocoded"


def test_get_subject_geocode_overrides_search_hit_coords():
    # A subject the index resolves only fuzzily (e.g. a brand-new build matching the
    # nearest indexed house) must still get its true coordinates from the geocoder.
    top = _match("100-newbuild-way-se-calgary-ab", "100 Newbuild Way SE Calgary AB",
                 sqft=2200, lat=50.88, lng=-113.96)
    tools = build_tools(source=StubCompSource(matches=[top]),
                        geocoder=StubGeocoder((51.10, -114.20)), as_of=date(2026, 6, 1))
    s = tools.get_subject("100 Newbuild Way SE")
    assert (s.lat, s.lng) == (51.10, -114.20) and s.provenance["lat"] == "geocoded"


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


def test_estimate_value_returns_an_estimate_id():
    # The id is the handle the agent passes to render_report instead of re-emitting the
    # whole (huge) estimate object back through the model.
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    est = TOOLS.estimate_value(s, TOOLS.find_comps(s).comps)
    assert est.estimate_id and est.estimate_id.startswith("est_")


def test_render_from_estimate_uses_cached_bundle(tmp_path):
    # render_report must NOT need the estimate/comps re-passed: estimate_value cached the
    # subject+comps+estimate under the id, so the agent passes only the id + its narrative.
    import os
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    est = TOOLS.estimate_value(s, TOOLS.find_comps(s).comps)
    path = TOOLS.render_from_estimate(
        est.estimate_id, confidence_reasoning="solid set", out_dir=str(tmp_path))
    assert os.path.isabs(path) and os.path.exists(path)
    html = open(path, encoding="utf-8").read()
    assert "solid set" in html and "Comparable sales" in html


def test_find_comps_returns_a_comps_id():
    # The id is the handle the agent passes to estimate_value instead of re-emitting the
    # whole comp array (which Desktop truncates).
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    res = TOOLS.find_comps(s)
    assert res.comps_id and res.comps_id.startswith("comps_")


def test_estimate_from_comps_matches_direct_call():
    # estimate_value(comps_id) must use the SAME full comp set find_comps produced.
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    res = TOOLS.find_comps(s)
    direct = TOOLS.estimate_value(s, res.comps, ladder_depth=len(res.relaxations))
    via_id = TOOLS.estimate_from_comps(res.comps_id)
    assert via_id.point == direct.point and len(via_id.per_comp) == len(res.comps)


def test_estimate_from_comps_applies_exclusions(tmp_path):
    # Curate an outlier out of the VALUE by naming it (address+reason) — it's dropped from
    # the calc and shown as excluded in the report.
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    res = TOOLS.find_comps(s)
    drop = res.comps[0].address
    est = TOOLS.estimate_from_comps(res.comps_id, exclusions=[{"address": drop, "reason": "atypical lot"}])
    assert drop not in [ca.address for ca in est.per_comp]   # dropped from the calc
    assert len(est.per_comp) < len(res.comps)
    path = TOOLS.render_from_estimate(est.estimate_id, out_dir=str(tmp_path))
    html = open(path, encoding="utf-8").read()
    assert "Excluded" in html and "atypical lot" in html     # surfaced in the report


def test_estimate_from_comps_unknown_id_raises_clear_error():
    with pytest.raises(ValueError) as exc:
        TOOLS.estimate_from_comps("comps_deadbeef")
    msg = str(exc.value).lower()
    assert "find_comps" in msg or "not found" in msg


def test_render_from_estimate_unknown_id_raises_clear_error(tmp_path):
    with pytest.raises(ValueError) as exc:
        TOOLS.render_from_estimate("est_deadbeef", out_dir=str(tmp_path))
    msg = str(exc.value).lower()
    assert "estimate" in msg and ("re-run" in msg or "rerun" in msg or "not found" in msg)


def _report_payload():
    from datetime import date
    from mcp_server.models import Subject, Comp, AdjustmentRules, ReportComp, ReportPayload
    from mcp_server.estimate import reconcile
    s = Subject(address="138 Cranberry Place SE", resolved_address="138 Cranberry Place SE",
                lat=51.0, lng=-114.0, sqft=1416, year_built=2007, beds=3, baths=3, garage=1)
    comps = [Comp(address=a, lat=51.0, lng=-114.0, sold_price=p, sold_date=date(2026, 4, 1),
                  sqft=sq, year_built=2007, beds=3, baths=3, garage=2, distance_km=0.2)
             for a, p, sq in [("71 Cranberry", 536_500, 1429), ("78 Cranberry", 560_000, 1425)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 10))
    return ReportPayload(subject=s, comps=[ReportComp(comp=c) for c in comps], estimate=est,
                         confidence_reasoning="ok", as_of=date(2026, 6, 10))


def test_render_report_default_dir_is_cwd_independent(tmp_path, monkeypatch):
    # Claude Desktop launches the stdio server from a non-writable CWD, so the DEFAULT output
    # dir must be absolute (env/home), never CWD-relative — else makedirs("reports") fails.
    import os
    from datetime import date
    from mcp_server.server import Tools
    out = tmp_path / "out"
    cwd = tmp_path / "cwd"; cwd.mkdir()
    monkeypatch.setenv("KV_COMP_REPORTS_DIR", str(out))
    monkeypatch.chdir(cwd)
    tools = Tools(source=None, as_of=date(2026, 6, 10))
    path = tools.render_report(_report_payload())          # NO out_dir -> uses default
    assert os.path.isabs(path) and os.path.exists(path)
    assert os.path.commonpath([path, str(out)]) == str(out)   # written under the env dir
    assert not (cwd / "reports").exists()                      # NOT CWD-relative (the bug)


def test_render_report_falls_back_to_tempdir_when_primary_unwritable(tmp_path, monkeypatch):
    import os
    from datetime import date
    from mcp_server.server import Tools
    blocker = tmp_path / "afile"; blocker.write_text("x")  # a FILE, not a dir
    monkeypatch.setenv("KV_COMP_REPORTS_DIR", str(blocker / "sub"))  # makedirs under a file -> OSError
    tools = Tools(source=None, as_of=date(2026, 6, 10))
    path = tools.render_report(_report_payload())          # NO out_dir
    assert os.path.exists(path)
    assert os.path.basename(os.path.dirname(path)) == "kv-comp-reports"
    assert str(blocker) not in path                        # did not use the broken primary
