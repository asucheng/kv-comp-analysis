# tests/test_honestdoor.py
from datetime import date
import httpx
import pytest
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.honestdoor import HonestDoorCompSource, listing_to_comp


def test_honestdoor_is_a_compsource():
    assert issubclass(HonestDoorCompSource, CompSource)


def test_listing_to_comp_maps_sold_sale():
    row = {"type": "SALE", "soldPrice": "670888.00", "soldDate": "2026-01-12T07:55:12.000Z",
           "address": {"streetNumber": None, "streetName": "432 56 AVENUE SW",
                       "city": "Calgary", "neighborhood": "Windsor Park"},
           "property": {"livingArea": 1312, "bedroomsTotal": 3, "bathroomsTotal": 3.1,
                        "yearBuilt": 1993, "location": {"lat": 51.0034744, "lon": -114.0733492}}}
    c = listing_to_comp(row)
    assert c is not None
    assert c.sold_price == 670888.0 and c.sqft == 1312.0
    assert c.sold_date == date(2026, 1, 12) and c.lat == 51.0034744
    assert c.price_per_sqft == round(670888.0 / 1312, 2)


def test_listing_to_comp_skips_unusable_rows():
    # not a SALE
    assert listing_to_comp({"type": "LEASE", "soldPrice": "2000", "soldDate": "2026-01-01T00:00:00Z",
                            "property": {"livingArea": 800, "location": {"lat": 51, "lon": -114}}}) is None
    # no sold price (active/unsold)
    assert listing_to_comp({"type": "SALE", "soldPrice": None, "soldDate": None,
                            "property": {"livingArea": 800, "location": {"lat": 51, "lon": -114}}}) is None
    # no living area (can't compute $/sqft)
    assert listing_to_comp({"type": "SALE", "soldPrice": "500000", "soldDate": "2026-01-01T00:00:00Z",
                            "property": {"livingArea": None, "location": {"lat": 51, "lon": -114}}}) is None


def test_get_property_returns_empty_record_for_address():
    rec = HonestDoorCompSource().get_property("123 Main St, Calgary")
    assert isinstance(rec, PropertyRecord)
    assert rec.address == "123 Main St, Calgary" and rec.sqft is None


def test_recent_sales_uses_injected_client_and_bbox_query():
    payload = {"data": {"getListings2": [
        {"type": "SALE", "soldPrice": "670888.00", "soldDate": "2026-01-12T07:55:12.000Z",
         "address": {"streetNumber": None, "streetName": "432 56 AVENUE SW", "city": "Calgary",
                     "neighborhood": "Windsor Park"},
         "property": {"livingArea": 1312, "bedroomsTotal": 3, "bathroomsTotal": 3.1,
                      "yearBuilt": 1993, "location": {"lat": 51.0034744, "lon": -114.0733492}}},
        {"type": "SALE", "soldPrice": None, "soldDate": None,
         "address": {"streetName": "skip me", "city": "Calgary", "neighborhood": "x"},
         "property": {"livingArea": None, "location": {"lat": 51, "lon": -114}}}]}}
    captured = {}

    def handler(request):
        import json as _json
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HonestDoorCompSource(client=client)
    comps = src.recent_sales(lat=51.03, lng=-114.06, radius_km=3.0,
                             lookback_months=12, as_of=date(2026, 6, 1))
    assert len(comps) == 1 and comps[0].sold_price == 670888.0
    # the request carried a bbox filter with topLeft/bottomRight
    f = captured["body"]["variables"]["filter"]["bbox"]
    assert "topLeft" in f and "bottomRight" in f and len(f["topLeft"]) == 2


@pytest.mark.live
def test_live_recent_sales_returns_real_calgary_comps():
    """Real network call. Skips if the endpoint is unreachable."""
    src = HonestDoorCompSource()
    try:
        comps = src.recent_sales(lat=51.0324, lng=-114.0619, radius_km=3.0,
                                 lookback_months=12, as_of=date(2026, 6, 1))
    except Exception as e:  # network/endpoint issues should not fail the suite
        pytest.skip(f"HonestDoor endpoint unreachable: {e}")
    assert comps, "expected at least one real sold comp near downtown Calgary"
    c = comps[0]
    assert c.sold_price > 0 and c.sqft > 0 and c.price_per_sqft > 0
    print(f"LIVE: {len(comps)} real comps; sample {c.address} ${c.sold_price:,.0f} {c.sqft}sqft")
