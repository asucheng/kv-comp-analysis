from datetime import date
from mcp_server.models import Subject, Comp, Criteria
from mcp_server.comps import months_between, filter_and_rank

AS_OF = date(2026, 6, 1)


def _subject(baths=2, garage=2):
    return Subject(address="S", lat=51.05, lng=-114.08, sqft=2000,
                   year_built=1985, property_type="detached", beds=3,
                   baths=baths, garage=garage)


def _comp(addr, lat, lng, price, d, sqft, yb=1985, ptype="detached", beds=3,
          baths=2, garage=None):
    return Comp(address=addr, lat=lat, lng=lng, sold_price=price, sold_date=d,
                sqft=sqft, year_built=yb, property_type=ptype, beds=beds,
                baths=baths, garage=garage)


def test_months_between():
    assert months_between(date(2026, 1, 1), AS_OF) == 5
    assert months_between(date(2025, 6, 1), AS_OF) == 12


def test_filter_keeps_qualifying_comp():
    s = _subject()
    good = _comp("good", 51.051, -114.081, 820_000, date(2026, 3, 1), 2050)
    kept, flags = filter_and_rank(s, [good], Criteria(), as_of=AS_OF)
    assert [c.address for c in kept] == ["good"]
    assert kept[0].distance_km is not None and kept[0].include_reason


def test_filter_drops_on_each_rule():
    s = _subject()
    too_far = _comp("far", 52.0, -114.0, 800_000, date(2026, 3, 1), 2000)
    too_big = _comp("big", 51.051, -114.081, 800_000, date(2026, 3, 1), 2600)  # +30%
    too_old_sale = _comp("stale", 51.051, -114.081, 800_000, date(2024, 1, 1), 2000)
    too_old_age = _comp("aged", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, yb=1960)
    kept, _ = filter_and_rank(s, [too_far, too_big, too_old_sale, too_old_age],
                              Criteria(), as_of=AS_OF)
    assert kept == []


def test_secondary_filters_type_and_beds():
    s = _subject()
    wrong_type = _comp("condo", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, ptype="condo")
    kept, _ = filter_and_rank(s, [wrong_type], Criteria(match_type=True), as_of=AS_OF)
    assert kept == []


def test_match_baths_drops_only_known_mismatch():
    s = _subject(baths=2)
    same = _comp("same", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, baths=2)
    diff = _comp("diff", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, baths=3)
    unknown = _comp("unk", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, baths=None)
    kept, _ = filter_and_rank(s, [same, diff, unknown], Criteria(match_baths=True), as_of=AS_OF)
    assert {c.address for c in kept} == {"same", "unk"}  # diff dropped; unknown kept


def test_match_garage_is_null_safe():
    s = _subject(garage=2)
    same = _comp("same", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, garage=2)
    diff = _comp("diff", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, garage=1)
    unknown = _comp("unk", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, garage=None)
    kept, _ = filter_and_rank(s, [same, diff, unknown], Criteria(match_garage=True), as_of=AS_OF)
    # only the known-different comp is dropped; unknown garage is never silently excluded
    assert {c.address for c in kept} == {"same", "unk"}


def test_ranking_orders_most_similar_first():
    s = _subject()
    near = _comp("near", 51.051, -114.081, 800_000, date(2026, 5, 1), 2010)
    farish = _comp("farish", 51.07, -114.07, 800_000, date(2026, 1, 1), 2300)
    kept, _ = filter_and_rank(s, [farish, near], Criteria(), as_of=AS_OF)
    assert [c.address for c in kept] == ["near", "farish"]


def test_ranking_handles_subject_without_year_built():
    s = Subject(address="S", lat=51.05, lng=-114.08, sqft=2000, property_type="detached")
    c = _comp("c", 51.051, -114.081, 800_000, date(2026, 5, 1), 2010)  # has year_built=1985
    kept, _ = filter_and_rank(s, [c], Criteria(), as_of=AS_OF)
    assert [x.address for x in kept] == ["c"]


# append to tests/test_comps.py
from mcp_server.comps import find_with_ladder
from mcp_server.models import FindCompsResult


def test_ladder_not_triggered_when_enough():
    s = _subject()
    cands = [_comp(f"c{i}", 51.051, -114.081, 800_000 + i, date(2026, 3, 1), 2000 + i)
             for i in range(4)]
    res = find_with_ladder(s, cands, Criteria(min_comps=4), as_of=AS_OF)
    assert isinstance(res, FindCompsResult)
    assert len(res.comps) == 4
    assert res.relaxations == []


def test_ladder_relaxes_time_first_then_records():
    s = _subject()
    # all sold 9 months ago -> excluded at the 6mo default, included once lookback relaxes to 12
    cands = [_comp(f"c{i}", 51.051, -114.081, 800_000, date(2025, 9, 1), 2000 + i)
             for i in range(4)]
    res = find_with_ladder(s, cands, Criteria(min_comps=4), as_of=AS_OF)
    assert len(res.comps) == 4
    assert res.relaxations[0].step == "lookback_months"
    assert res.relaxations[0].to == 12
    assert any("relaxed" in f.lower() for f in res.flags)


def test_time_never_relaxes_beyond_12_months():
    s = _subject()
    # sold 13 months ago -> never eligible; the ladder must not reach past 12 months
    cands = [_comp(f"c{i}", 51.051, -114.081, 800_000, date(2025, 5, 1), 2000 + i)
             for i in range(4)]
    res = find_with_ladder(s, cands, Criteria(min_comps=4), as_of=AS_OF)
    assert res.comps == []
    assert all(not (r.step == "lookback_months" and r.to > 12) for r in res.relaxations)


def test_match_garage_skipped_with_flag_when_subject_unknown():
    s = _subject(garage=None)
    cands = [_comp(f"c{i}", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000 + i, garage=i % 3)
             for i in range(4)]
    res = find_with_ladder(s, cands, Criteria(min_comps=4, match_garage=True), as_of=AS_OF)
    assert len(res.comps) == 4  # nothing dropped: the constraint can't apply
    assert any("garage match requested" in f and "skipped" in f for f in res.flags)


def test_ladder_exhausts_and_returns_what_it_found():
    s = _subject()
    res = find_with_ladder(s, [], Criteria(min_comps=4), as_of=AS_OF)
    assert res.comps == []
    assert any("insufficient" in f.lower() for f in res.flags)


def test_radius_is_a_hard_limit_never_widened():
    s = _subject()
    # all comps sit ~4 km out — outside Sam's 3 km hard limit
    far = [_comp(f"f{i}", 51.05 + 0.036, -114.08, 800_000, date(2026, 3, 1), 2000)
           for i in range(6)]
    res = find_with_ladder(s, far, Criteria(min_comps=4), as_of=AS_OF)
    assert res.comps == []
    assert all(r.step != "radius_km" for r in res.relaxations)
    assert any("insufficient" in f.lower() for f in res.flags)


def test_size_is_a_hard_limit_never_widened():
    s = _subject()   # 2000 sqft
    big = [_comp(f"b{i}", 51.051, -114.081, 800_000, date(2026, 3, 1), 2700)  # +35%
           for i in range(6)]
    res = find_with_ladder(s, big, Criteria(min_comps=4), as_of=AS_OF)
    assert res.comps == []
    assert all(r.step != "size_pct" for r in res.relaxations)


def test_age_is_a_hard_limit_never_widened():
    s = _subject()   # year_built 1985
    old = [_comp(f"o{i}", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, yb=1960)  # 25 yr
           for i in range(6)]
    res = find_with_ladder(s, old, Criteria(min_comps=4), as_of=AS_OF)
    assert res.comps == []
    assert all(r.step != "age_years" for r in res.relaxations)


def test_ladder_relaxes_garage_toggle_when_short():
    s = _subject(garage=2)
    # match_garage is off by default; enable it here to exercise the toggle-relaxation
    # path. The comps qualify on every hard rule but differ on garage (known mismatch) —
    # only a toggle relaxation can recover them.
    cands = [_comp(f"c{i}", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000 + i, garage=1)
             for i in range(4)]
    res = find_with_ladder(s, cands, Criteria(min_comps=4, match_garage=True), as_of=AS_OF)
    assert len(res.comps) == 4
    assert any(r.step == "match_garage" and r.to is False for r in res.relaxations)


def test_filter_does_not_mutate_input_comps():
    s = _subject()
    original = _comp("c", 51.051, -114.081, 800_000, date(2026, 3, 1), 2010)
    kept, _ = filter_and_rank(s, [original], Criteria(), as_of=AS_OF)
    assert original.distance_km is None and original.include_reason is None
    assert kept[0].distance_km is not None  # the returned copy IS annotated
