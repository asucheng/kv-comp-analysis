from datetime import date
import httpx
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.honestdoor import HonestDoorCompSource, parse_property, parse_sales


def test_honestdoor_is_a_compsource():
    assert issubclass(HonestDoorCompSource, CompSource)


def test_parse_property_maps_fields():
    raw = {"community": "Roxboro", "latitude": 51.025, "longitude": -114.073,
           "squareFootage": 1200, "yearBuilt": 1930, "bedrooms": 2, "bathrooms": 3,
           "lotSize": 5998, "avmValue": 957400, "assessedValue": 900000}
    rec = parse_property("1431 6 St NW", raw)
    assert isinstance(rec, PropertyRecord)
    assert rec.community == "Roxboro" and rec.sqft == 1200 and rec.year_built == 1930
    assert rec.hd_estimate == 957400


def test_parse_sales_filters_to_real_sales_only():
    rows = [
        {"address": "3028 1 St SW", "soldPrice": 1801000, "soldDate": "2026-01-16",
         "squareFootage": 2532, "latitude": 51.02, "longitude": -114.08,
         "bedrooms": 3, "bathrooms": 3, "yearBuilt": 1982},
        {"address": "no-price", "soldPrice": None, "soldDate": None,
         "squareFootage": 2000, "latitude": 51.02, "longitude": -114.08},
    ]
    comps = parse_sales(rows)
    assert [c.address for c in comps] == ["3028 1 St SW"]
    assert comps[0].sold_date == date(2026, 1, 16)


def test_recent_sales_uses_injected_client(monkeypatch):
    payload = {"data": {"recentlySold": [
        {"address": "3028 1 St SW", "soldPrice": 1801000, "soldDate": "2026-01-16",
         "squareFootage": 2532, "latitude": 51.02, "longitude": -114.08,
         "bedrooms": 3, "bathrooms": 3, "yearBuilt": 1982}]}}

    def handler(request):
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HonestDoorCompSource(client=client)
    comps = src.recent_sales("Roxboro", lookback_months=12, as_of=date(2026, 6, 1))
    assert len(comps) == 1 and comps[0].sold_price == 1801000
