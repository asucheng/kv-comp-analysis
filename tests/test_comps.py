from datetime import date
from mcp_server.models import Subject, Comp, Criteria
from mcp_server.comps import months_between, filter_and_rank

AS_OF = date(2026, 6, 1)


def _subject():
    return Subject(address="S", lat=51.05, lng=-114.08, sqft=2000,
                   year_built=1985, property_type="detached", beds=3)


def _comp(addr, lat, lng, price, d, sqft, yb=1985, ptype="detached", beds=3):
    return Comp(address=addr, lat=lat, lng=lng, sold_price=price, sold_date=d,
                sqft=sqft, year_built=yb, property_type=ptype, beds=beds)


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
