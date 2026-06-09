# tests/test_honestdoor.py
import json
from datetime import date
import httpx
import pytest
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.honestdoor import (
    HonestDoorCompSource, listing_to_comp, _slug_to_address, _SQM_TO_SQFT)


def _multisearch_client(items, calls=None):
    """Mock GraphQL client: answers getMultiSearch with `items` as ranked
    properties[].item nodes."""
    def handler(request):
        if calls is not None:
            calls.append(json.loads(request.content)["variables"]["filter"]["query"])
        return httpx.Response(200, json={"data": {"getMultiSearch": {
            "properties": [{"item": it} for it in items]}}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_honestdoor_is_a_compsource():
    assert issubclass(HonestDoorCompSource, CompSource)


def test_slug_to_address_renders_readably_and_trims_suffix():
    assert _slug_to_address("122-auburn-bay-heights-se-calgary-ab") == \
        "122 Auburn Bay Heights SE Calgary AB"
    # random suffix after the province is dropped
    assert _slug_to_address("5687-yew-street-vancouver-bc-phflv") == \
        "5687 Yew Street Vancouver BC"


def test_listing_to_comp_maps_sold_sale():
    row = {"type": "SALE", "soldPrice": "670888.00", "soldDate": "2026-01-12T07:55:12.000Z",
           "address": {"streetNumber": None, "streetName": "432 56 AVENUE SW",
                       "city": "Calgary", "neighborhood": "Windsor Park"},
           "property": {"livingArea": 1312, "bedroomsTotal": 3, "bathroomsTotal": 3.1,
                        "garageSpaces": 2, "yearBuilt": 1993,
                        "location": {"lat": 51.0034744, "lon": -114.0733492}}}
    c = listing_to_comp(row)
    assert c is not None
    assert c.sold_price == 670888.0 and c.sqft == 1312.0
    assert c.baths == 3.1 and c.garage == 2
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


def _item(slug, **kw):
    node = {"slug": slug, "livingArea": None, "bedroomsTotal": None, "bathroomsTotal": None,
            "garageSpaces": None, "yearBuilt": None, "lotSizeArea": None,
            "neighbourhoodName": None, "predictedValue": None, "location": None}
    node.update(kw)
    return node


def test_search_subject_maps_ranked_results():
    items = [
        _item("122-auburn-bay-heights-se-calgary-ab", livingArea=1450, bedroomsTotal=2,
              bathroomsTotal=2.1, garageSpaces=2, yearBuilt=2006, lotSizeArea=270.9959123,
              neighbourhoodName="Auburn Bay", predictedValue=537100,
              location={"lat": 50.8849599, "lon": -113.964597}),
        _item("122-auburn-bay-close-se-calgary-ab", livingArea=1961),
    ]
    calls: list[str] = []
    recs = HonestDoorCompSource(client=_multisearch_client(items, calls)).search_subject(
        "122 Auburn Bay Heights SE, Calgary")
    assert calls == ["122 Auburn Bay Heights SE, Calgary"]   # searched by free text
    assert len(recs) == 2
    top = recs[0]
    assert top.slug == "122-auburn-bay-heights-se-calgary-ab"
    assert top.resolved_address == "122 Auburn Bay Heights SE Calgary AB"
    assert top.sqft == 1450 and top.beds == 2 and top.baths == 2.1 and top.garage == 2
    assert top.year_built == 2006 and top.community == "Auburn Bay" and top.hd_estimate == 537100
    assert top.lat == 50.8849599 and top.lng == -113.964597
    assert top.lot_sf == round(270.9959123 * _SQM_TO_SQFT)


def test_get_property_returns_top_search_hit():
    items = [_item("122-auburn-bay-heights-se-calgary-ab", livingArea=1450),
             _item("122-auburn-bay-close-se-calgary-ab", livingArea=1961)]
    rec = HonestDoorCompSource(client=_multisearch_client(items)).get_property("122 Auburn Bay Heights SE")
    assert rec.slug == "122-auburn-bay-heights-se-calgary-ab" and rec.sqft == 1450


def test_get_property_empty_record_when_search_returns_nothing():
    rec = HonestDoorCompSource(client=_multisearch_client([])).get_property("999 Nowhere St")
    assert isinstance(rec, PropertyRecord)
    assert rec.address == "999 Nowhere St" and rec.sqft is None and rec.slug is None


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


@pytest.mark.live
def test_live_get_property_resolves_subject_from_address():
    """Real network call: a raw address resolves to real attributes via slug."""
    src = HonestDoorCompSource()
    try:
        rec = src.get_property("122 Auburn Bay Heights SE, Auburn Bay, Calgary, AB T3M 0A7")
    except Exception as e:  # network/endpoint issues should not fail the suite
        pytest.skip(f"HonestDoor endpoint unreachable: {e}")
    assert rec.sqft and rec.sqft > 0, "expected real livingArea from HonestDoor"
    assert rec.lat and rec.lng, "expected coordinates from the property record"
    print(f"LIVE subject: {rec.sqft}sqft beds {rec.beds} baths {rec.baths} "
          f"yr {rec.year_built} avm {rec.hd_estimate}")
