from datetime import date
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.synthetic import SyntheticCompSource


def test_synthetic_is_a_compsource():
    assert issubclass(SyntheticCompSource, CompSource)


def test_get_property_returns_record_with_attrs():
    src = SyntheticCompSource(seed=42)
    rec = src.get_property("123 Maple Dr, Roxboro, Calgary, AB")
    assert isinstance(rec, PropertyRecord)
    assert rec.community and rec.lat and rec.sqft and rec.year_built


def test_get_property_is_deterministic():
    a = SyntheticCompSource(seed=42).get_property("123 Maple Dr")
    b = SyntheticCompSource(seed=42).get_property("123 Maple Dr")
    assert a.model_dump() == b.model_dump()


def test_recent_sales_returns_comps_within_radius():
    from mcp_server.geo import haversine_km
    src = SyntheticCompSource(seed=42)
    lat, lng = 51.05, -114.07
    comps = src.recent_sales(lat=lat, lng=lng, radius_km=8.0,
                             lookback_months=12, as_of=date(2026, 6, 1))
    assert len(comps) >= 8
    assert all(c.sold_price > 0 and c.sqft > 0 for c in comps)
    assert all(haversine_km(lat, lng, c.lat, c.lng) <= 3.01 for c in comps)
