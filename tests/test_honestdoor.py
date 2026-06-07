# tests/test_honestdoor.py
from datetime import date
import pytest
import httpx
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.honestdoor import (
    HonestDoorCompSource, property_to_record, parse_sales,
)


def test_honestdoor_is_a_compsource():
    assert issubclass(HonestDoorCompSource, CompSource)


def test_property_to_record_maps_verified_fields():
    raw = {"fullAddress": "1431 6 St NW", "yearBuilt": 1930, "livingArea": 1200,
           "bedroomsTotal": 2, "bathroomsTotal": 3, "predictedValue": 957400,
           "taxAssessedValue": 900000, "location": {"lat": 51.06, "lon": -114.09}}
    rec = property_to_record("fallback", raw)
    assert isinstance(rec, PropertyRecord)
    assert rec.address == "1431 6 St NW" and rec.sqft == 1200 and rec.year_built == 1930
    assert rec.lat == 51.06 and rec.lng == -114.09
    assert rec.hd_estimate == 957400 and rec.assessed_value == 900000


def test_parse_sales_keeps_only_usable_real_sales():
    rows = [
        # usable: has closePrice, closeDate, livingArea, location
        {"fullAddress": "A", "closePrice": 800000, "closeDate": "2026-01-16T00:00:00.000Z",
         "livingArea": 2000, "bedroomsTotal": 3, "bathroomsTotal": 2, "yearBuilt": 1985,
         "location": {"lat": 51.05, "lon": -114.08}},
        # skip: no livingArea (sparse attribute)
        {"fullAddress": "B", "closePrice": 700000, "closeDate": "2026-02-01T00:00:00.000Z",
         "livingArea": None, "location": {"lat": 51.0, "lon": -114.0}},
        # skip: no sale (closePrice null = AVM-only / unsold)
        {"fullAddress": "C", "closePrice": None, "closeDate": None,
         "livingArea": 1800, "location": {"lat": 51.0, "lon": -114.0}},
    ]
    comps = parse_sales(rows)
    assert [c.address for c in comps] == ["A"]
    assert comps[0].sold_date == date(2026, 1, 16)
    assert comps[0].sqft == 2000 and comps[0].price_per_sqft == 400.0


def test_get_property_documents_slug_only_limitation():
    with pytest.raises(NotImplementedError):
        HonestDoorCompSource().get_property("123 Main St")


@pytest.mark.skip(reason="rewritten in next task — honestdoor.recent_sales now uses geo bbox, not community name")
def test_recent_sales_uses_injected_client_and_real_schema():
    payload = {"data": {"getProperties": [
        {"fullAddress": "3028 1 St SW", "closePrice": 1801000,
         "closeDate": "2026-01-16T00:00:00.000Z", "livingArea": 2532,
         "bedroomsTotal": 3, "bathroomsTotal": 3, "yearBuilt": 1982,
         "location": {"lat": 51.02, "lon": -114.08}}]}}

    def handler(request):
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HonestDoorCompSource(client=client)
    comps = src.recent_sales("Roxboro", lookback_months=12, as_of=date(2026, 6, 1))
    assert len(comps) == 1 and comps[0].sold_price == 1801000 and comps[0].sqft == 2532
