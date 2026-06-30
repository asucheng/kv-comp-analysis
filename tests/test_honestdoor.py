# tests/test_honestdoor.py
import json
from datetime import date
import httpx
import pytest
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.honestdoor import (
    HonestDoorCompSource, listing_to_comp, multisearch_item_to_record,
    _slug_to_address, _SQM_TO_SQFT)


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


def _sale_row(street, sold_date_iso, sqft=1300, lat=51.03, lon=-114.06):
    return {"type": "SALE", "soldPrice": "600000.00", "soldDate": sold_date_iso,
            "address": {"streetNumber": None, "streetName": street, "city": "Calgary",
                        "neighborhood": "x"},
            "property": {"livingArea": sqft, "bedroomsTotal": 3, "bathroomsTotal": 2.0,
                         "yearBuilt": 2000, "location": {"lat": lat, "lon": lon}}}


def _paging_client(pages, captured):
    """Mock GraphQL client that returns successive `pages` (each a list of rows)
    for successive getListings2 requests, recording each request's variables."""
    state = {"i": 0}

    def handler(request):
        captured.append(json.loads(request.content)["variables"])
        i = state["i"]; state["i"] += 1
        rows = pages[i] if i < len(pages) else []
        return httpx.Response(200, json={"data": {"getListings2": rows}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_recent_sales_paginates_until_a_short_page():
    from mcp_server.compsource.honestdoor import _TAKE
    full = [_sale_row(f"{i} A St", "2026-03-01T00:00:00Z") for i in range(_TAKE)]
    short = [_sale_row("1 B St", "2026-02-01T00:00:00Z")]
    captured: list[dict] = []
    src = HonestDoorCompSource(client=_paging_client([full, short], captured))
    comps = src.recent_sales(lat=51.03, lng=-114.06, radius_km=3.0,
                             lookback_months=12, as_of=date(2026, 6, 1))
    assert len(comps) == _TAKE + 1           # rows from BOTH pages collected
    assert len(captured) == 2                # a full page triggered a second request
    assert captured[0]["skip"] == 0 and captured[1]["skip"] == _TAKE


def test_recent_sales_requests_recency_window_and_order():
    captured: list[dict] = []
    src = HonestDoorCompSource(client=_paging_client([[]], captured))
    src.recent_sales(lat=51.03, lng=-114.06, radius_km=3.0,
                     lookback_months=6, as_of=date(2026, 6, 1))
    v = captured[0]
    assert "topLeft" in v["filter"]["bbox"] and "bottomRight" in v["filter"]["bbox"]
    assert v["filter"]["soldDate"]["gte"] == "2025-12-01"   # as_of minus 6 months
    assert v["order"] == {"soldDate": "desc"}               # newest-first, stable paging


def test_recent_sales_stops_after_a_single_short_page():
    captured: list[dict] = []
    src = HonestDoorCompSource(client=_paging_client([[_sale_row("1 A St", "2026-03-01T00:00:00Z")]],
                                                     captured))
    comps = src.recent_sales(lat=51.03, lng=-114.06, radius_km=3.0,
                             lookback_months=12, as_of=date(2026, 6, 1))
    assert len(comps) == 1 and len(captured) == 1            # no needless extra request


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


# ---------------------------------------------------------------------------
# Estimated bed/bath fallback (HonestDoor populates *Est when the exact field is
# null — ~37% of sold rows; coalesce exact-first so comps aren't left blank).
# ---------------------------------------------------------------------------
def _row(bed=None, bed_est=None, bath=None, bath_est=None):
    return {"type": "SALE", "soldPrice": "700000", "soldDate": "2026-01-10T00:00:00Z",
            "address": {"streetNumber": "1", "streetName": "Test St"},
            "property": {"livingArea": 1800, "bedroomsTotal": bed, "bedroomsTotalEst": bed_est,
                         "bathroomsTotal": bath, "bathroomsTotalEst": bath_est,
                         "garageSpaces": None, "yearBuilt": 2010,
                         "location": {"lat": 51.0, "lon": -114.0}}}


def test_listing_to_comp_falls_back_to_estimated_bed_bath():
    c = listing_to_comp(_row(bed=None, bed_est=4, bath=None, bath_est=2))
    assert c is not None
    assert c.beds == 4 and c.baths == 2          # recovered from *Est


def test_listing_to_comp_prefers_exact_over_estimate():
    c = listing_to_comp(_row(bed=3, bed_est=5, bath=2.1, bath_est=2))
    assert c.beds == 3 and c.baths == 2.1        # exact wins when present


def test_multisearch_item_falls_back_to_estimated_bed_bath():
    item = {"slug": "1-test-st-calgary-ab", "livingArea": 1800, "yearBuilt": 2010,
            "bedroomsTotal": None, "bedroomsTotalEst": 4,
            "bathroomsTotal": None, "bathroomsTotalEst": 2,
            "garageSpaces": None, "location": {"lat": 51.0, "lon": -114.0}}
    rec = multisearch_item_to_record(item)
    assert rec.beds == 4 and rec.baths == 2

    item_exact = {**item, "bedroomsTotal": 3, "bathroomsTotal": 2.1}
    rec2 = multisearch_item_to_record(item_exact)
    assert rec2.beds == 3 and rec2.baths == 2.1


# ---------------------------------------------------------------------------
# MLS details / parking-type sourcing (option A): the bulk getListings2 node
# also carries MLS `details` + `condominium.parkingType` — far better coverage
# (garage, property type, bed/bath) than the sparse public `property` entity.
# ---------------------------------------------------------------------------
def _mls_row(num_garage=None, parking="Double Garage Detached", ptype="Detached",
             beds="3", beds_plus="0", baths="3", baths_plus="1",
             prop_garage=None, prop_bed=None, prop_bed_est=None):
    return {"type": "SALE", "soldPrice": "700000", "soldDate": "2026-01-10T00:00:00Z",
            "address": {"streetNumber": "1", "streetName": "Test St"},
            "details": {"numGarageSpaces": num_garage, "numBedrooms": beds,
                        "numBedroomsPlus": beds_plus, "numBathrooms": baths,
                        "numBathroomsPlus": baths_plus, "propertyType": ptype},
            "condominium": {"parkingType": parking},
            "property": {"livingArea": 1800, "bedroomsTotal": prop_bed,
                         "bedroomsTotalEst": prop_bed_est, "bathroomsTotal": None,
                         "bathroomsTotalEst": None, "garageSpaces": prop_garage,
                         "yearBuilt": 2010, "location": {"lat": 51.0, "lon": -114.0}}}


def test_listing_to_comp_uses_mls_details():
    c = listing_to_comp(_mls_row(num_garage="2"))
    assert c.garage == 2                       # from details.numGarageSpaces
    assert c.beds == 3                         # numBedrooms 3 + numBedroomsPlus 0
    assert c.baths == 2.1                      # 3 total - 1 half = 2 full + 1 half -> 2.1
    assert c.property_type == "detached"
    assert c.parking_type == "Double Garage Detached"


def test_mls_bath_convention_total_minus_half():
    # numBathrooms is the TOTAL count; numBathroomsPlus is how many are half-baths.
    assert listing_to_comp(_mls_row(baths="2", baths_plus="0")).baths == 2.0   # 2 full
    assert listing_to_comp(_mls_row(baths="4", baths_plus="1")).baths == 3.1   # 3 full + 1 half
    assert listing_to_comp(_mls_row(baths="3", baths_plus="1")).baths == 2.1   # 2 full + 1 half


def test_garage_inference_from_parking_type():
    g = lambda pt: listing_to_comp(_mls_row(num_garage=None, parking=pt, prop_garage=None)).garage
    assert g("Single Garage") == 1
    assert g("Triple Garage Attached") == 3
    assert g("Double Garage Attached,Driveway") == 2     # count wins even with a driveway listed
    assert g("Off Street") == 0                          # surface parking -> known no garage
    assert g("Gravel Driveway,Off Street,Parking Pad") == 0
    assert g("Underground") == 1                         # covered dedicated stall (condos) counts
    assert g("Heated Underground,Stall") == 1
    assert g("Attached Garage") is None                  # has a garage, count unknown
    assert g("") is None                                 # no info -> unknown


def test_property_type_mapping_variants():
    assert listing_to_comp(_mls_row(ptype="Semi Detached (Half Duplex)")).property_type == "semi"
    assert listing_to_comp(_mls_row(ptype="Row/Townhouse")).property_type == "townhouse"
    assert listing_to_comp(_mls_row(ptype="Apartment")).property_type == "condo"
    assert listing_to_comp(_mls_row(ptype="Detached")).property_type == "detached"


def test_falls_back_to_property_when_no_mls_details():
    row = {"type": "SALE", "soldPrice": "700000", "soldDate": "2026-01-10T00:00:00Z",
           "address": {"streetNumber": "1", "streetName": "Test St"},
           "property": {"livingArea": 1800, "bedroomsTotal": None, "bedroomsTotalEst": 4,
                        "bathroomsTotal": None, "bathroomsTotalEst": 2, "garageSpaces": 3,
                        "yearBuilt": 2010, "location": {"lat": 51.0, "lon": -114.0}}}
    c = listing_to_comp(row)
    assert c.beds == 4 and c.baths == 2 and c.garage == 3   # *Est + property fallback
    assert c.parking_type is None


def test_listing_to_comp_maps_style_basement_neighbourhood():
    row = {"type": "SALE", "soldPrice": "550000", "soldDate": "2026-03-01T00:00:00Z",
           "address": {"streetNumber": "12", "streetName": "Elm St",
                       "city": "Calgary", "neighborhood": "Windsor Park"},
           "details": {"propertyType": "Detached", "style": "2-Storey",
                       "basement1": "Fin W/O", "basement2": "Sep Entrance"},
           "property": {"livingArea": "1500", "yearBuilt": 2005,
                        "location": {"lat": 51.0, "lon": -114.0}}}
    c = listing_to_comp(row)
    assert c.style == "2-Storey"
    assert c.basement == "Fin W/O — Sep Entrance"
    assert c.community == "Windsor Park"


def test_listing_to_comp_basement_and_neighbourhood_edge_cases():
    row = {"type": "SALE", "soldPrice": "500000", "soldDate": "2026-03-01T00:00:00Z",
           "address": {"streetNumber": "9", "streetName": "Oak Rd", "neighborhood": ""},
           "details": {"basement1": "None"},   # "None" is a real value (no basement)
           "property": {"livingArea": "1200", "location": {"lat": 51.0, "lon": -114.0}}}
    c = listing_to_comp(row)
    assert c.basement == "None"     # preserved, not coerced
    assert c.community is None       # empty string -> None
    assert c.style is None


# ---------------------------------------------------------------------------
# Subject MLS enrichment: resolve the subject's own MLS listing by propertyId
# so its garage/property_type/parking/bed-bath come from MLS, not the sparse entity.
# ---------------------------------------------------------------------------
def _listing_client(detail_rows):
    def handler(request):
        return httpx.Response(200, json={"data": {"getListings2": detail_rows}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_multisearch_item_carries_property_id():
    rec = multisearch_item_to_record(_item("1-test-st-calgary-ab", id="prop123", livingArea=1800))
    assert rec.property_id == "prop123"


def test_enrich_subject_fills_from_mls_listing():
    rec = PropertyRecord(address="X", property_id="prop123", beds=3, garage=None, property_type=None)
    rows = [{"details": {"numGarageSpaces": "2", "numBedrooms": "3", "numBedroomsPlus": "0",
                         "numBathrooms": "3", "numBathroomsPlus": "1", "propertyType": "Detached"},
             "condominium": {"parkingType": "Double Garage Detached"}}]
    out = HonestDoorCompSource(client=_listing_client(rows)).enrich_subject(rec)
    assert out.garage == 2 and out.property_type == "detached"
    assert out.parking_type == "Double Garage Detached" and out.baths == 2.1  # 3 total - 1 half


def test_enrich_subject_noop_without_property_id():
    rec = PropertyRecord(address="X", garage=None)
    out = HonestDoorCompSource(client=_listing_client([])).enrich_subject(rec)
    assert out.garage is None and out.parking_type is None


def test_enrich_subject_noop_when_no_listing():
    rec = PropertyRecord(address="X", property_id="p", garage=None, property_type=None)
    out = HonestDoorCompSource(client=_listing_client([])).enrich_subject(rec)
    assert out.garage is None and out.property_type is None


def test_base_compsource_enrich_subject_is_identity():
    from tests.stubs import StubCompSource
    rec = PropertyRecord(address="X", garage=None)
    assert StubCompSource().enrich_subject(rec) is rec
