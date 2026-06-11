from datetime import date
from mcp_server.models import Subject, Comp, AdjustmentRules, ReportComp, ReportPayload
from mcp_server.estimate import reconcile
from mcp_server.report import render_report_html, slug


def _payload():
    s = Subject(address="138 Cranberry Place SE", lat=51.0, lng=-114.0, sqft=1416,
                year_built=2007, beds=3, baths=3, garage=1, community="Cranston",
                property_type="detached",
                provenance={"sqft": "honestdoor", "year_built": "honestdoor"})
    comps = [Comp(address=a, lat=51.0, lng=-114.0, sold_price=p, sold_date=date(2026, 4, 1),
                  sqft=sq, year_built=2007, beds=3, baths=3, garage=g, distance_km=d,
                  include_reason="same community")
             for a, p, sq, g, d in [("71 Cranberry Pl", 536_500, 1429, 2, 0.1),
                                    ("78 Cranberry Cl", 560_000, 1425, 2, 0.3),
                                    ("420 Cranberry Cir", 535_000, 1356, 2, 0.2),
                                    ("389 Cranberry Cir", 558_500, 1358, 2, 0.2)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 10))
    rcomps = [ReportComp(comp=c, kept=True) for c in comps]
    rcomps.append(ReportComp(comp=Comp(address="56 Cranbrook Landing", lat=51.0, lng=-114.0,
                  sold_price=1_173_000, sold_date=date(2026, 4, 1), sqft=1540),
                  kept=False, exclude_reason="lakefront outlier"))
    return ReportPayload(subject=s, comps=rcomps, estimate=est,
                         confidence_reasoning="Tight same-street cluster.",
                         target_warnings=["Subject's own sale appears in the pool."],
                         verify_next=["Confirm basement development."], as_of=date(2026, 6, 10))


def test_slug():
    assert slug("138 Cranberry Place SE") == "138-cranberry-place-se"


def test_render_has_all_sections_and_value():
    html = render_report_html(_payload())
    for token in ["138 Cranberry Place SE", "Baseline", "Confidence", "Comparable",
                  "Adjustment", "Disclosure", "Not in this number", "What I'd verify next"]:
        assert token in html


def test_render_orders_target_warnings_before_project_warnings():
    html = render_report_html(_payload())
    assert "Subject&#x27;s own sale appears in the pool." in html or \
           "Subject's own sale appears in the pool." in html
    assert html.index("own sale appears") < html.index("No location")


def test_render_is_self_contained_no_external_refs():
    html = render_report_html(_payload())
    assert "src=" not in html and "http://" not in html and "https://" not in html
    assert "<details" in html  # interactive tiles present


def test_render_shows_grouping_evidence_in_tiles():
    html = render_report_html(_payload())
    assert "Δ" in html  # arithmetic detail rendered
    assert "larger half" in html  # aggregate line rendered (grouping evidence present)


def test_render_shows_pair_traces_in_tiles():
    # Comps share beds/baths/garage but sqft spans >=8%, so SIZE derives via matched pairs,
    # exercising the pair-trace table (comp_a/comp_b/arithmetic/Implies) in the tile.
    from mcp_server.models import Subject, Comp, AdjustmentRules, ReportComp, ReportPayload
    from mcp_server.estimate import reconcile
    s = Subject(address="S", lat=51.0, lng=-114.0, sqft=1800, year_built=2010,
                beds=3, baths=2, garage=2)
    comps = [Comp(address=a, lat=51.0, lng=-114.0, sold_price=p, sold_date=date(2026, 4, 1),
                  sqft=sq, year_built=2010, beds=3, baths=2, garage=2, distance_km=0.2)
             for a, p, sq in [("Aaa St", 690_000, 1700), ("Bbb St", 760_000, 2000),
                              ("Ccc St", 700_000, 1720), ("Ddd St", 770_000, 2010)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 1))
    size = next(c for c in est.coefficients if c.factor == "size")
    assert size.method == "matched_pair" and size.pairs  # precondition: real pairs exist
    html = render_report_html(ReportPayload(
        subject=s, comps=[ReportComp(comp=c) for c in comps], estimate=est,
        confidence_reasoning="ok", as_of=date(2026, 6, 1)))
    assert "Implies" in html                       # pair-trace table header rendered
    assert "Aaa St" in html                        # a comp address appears in a trace row
    assert "median of" in html                     # matched-pair aggregate line rendered
    assert "Δ" in html                             # arithmetic detail rendered


def test_render_excluded_reason_present():
    assert "lakefront outlier" in render_report_html(_payload())


def test_render_collapses_comps_beyond_ten():
    from datetime import date
    p = _payload()
    base = p.comps[0].comp
    # pad to 12 kept comps so the "show more" collapse triggers
    extra = [ReportComp(comp=base.model_copy(update={"address": f"extra {i}", "distance_km": 1.0 + i}))
             for i in range(9)]
    p.comps = [c for c in p.comps if c.kept] + extra + [c for c in p.comps if not c.kept]
    html = render_report_html(p)
    assert "more comps" in html  # collapse summary present
