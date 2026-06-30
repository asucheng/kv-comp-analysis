from __future__ import annotations
import json
import httpx
import pytest
from mcp_server.geocode import GoogleGeocoder


def _google_ok(lat, lng, status="OK"):
    body = {"status": status, "results": [] if status != "OK"
            else [{"geometry": {"location": {"lat": lat, "lng": lng},
                                "location_type": "ROOFTOP"},
                   "formatted_address": "x"}]}
    return body


def test_google_geocode_returns_lat_lng_and_restricts_to_canada():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_google_ok(51.0447, -114.0719))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = GoogleGeocoder(api_key="test-key", client=client)
    assert geo.geocode("41 Heritage Park Way, Cochrane, AB") == (51.0447, -114.0719)
    assert "maps.googleapis.com/maps/api/geocode/json" in captured["url"]
    assert "key=test-key" in captured["url"]
    assert "components=" in captured["url"] and "country" in captured["url"]  # restricted to CA


def test_google_geocode_pins_province_and_country_via_components():
    # Province augmentation (mirrors KV-Capital-propcomp-ai): restrict to the market's
    # province as well as Canada, so a bare street address can't resolve out-of-region.
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_google_ok(51.0, -114.0))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = GoogleGeocoder(api_key="test-key", client=client)  # default region AB
    geo.geocode("123 Main St, Calgary")
    url = captured["url"]
    assert "administrative_area" in url and "AB" in url  # province pinned
    assert "country" in url and "CA" in url              # still restricted to Canada


def test_google_geocode_region_is_configurable():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_google_ok(43.6, -79.4))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = GoogleGeocoder(api_key="test-key", client=client, region="ON")
    geo.geocode("123 Queen St, Toronto")
    assert "administrative_area" in captured["url"] and "ON" in captured["url"]


def _google_results(*results):
    """Build an OK response from (lat, lng, location_type) tuples."""
    return {"status": "OK", "results": [
        {"geometry": {"location": {"lat": lat, "lng": lng},
                      "location_type": loc_type},
         "formatted_address": "x"}
        for lat, lng, loc_type in results]}


def test_google_geocode_rejects_coarse_approximate_result():
    # APPROXIMATE = a street/city centroid, not a rooftop fix. Accepting it would
    # anchor the comp search on a bogus point; reject -> caller falls back.
    def handler(request):
        return httpx.Response(200, json=_google_results((51.05, -114.07, "APPROXIMATE")))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = GoogleGeocoder(api_key="test-key", client=client)
    assert geo.geocode("Some Vague Place, Calgary, AB") is None


def test_google_geocode_accepts_range_interpolated():
    def handler(request):
        return httpx.Response(200, json=_google_results((51.05, -114.07, "RANGE_INTERPOLATED")))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = GoogleGeocoder(api_key="test-key", client=client)
    assert geo.geocode("123 Real St, Calgary, AB") == (51.05, -114.07)


def test_google_geocode_picks_first_precise_result_over_coarse():
    # If the most-prominent hit is coarse but a later one is a rooftop fix, use it.
    def handler(request):
        return httpx.Response(200, json=_google_results(
            (50.00, -113.00, "APPROXIMATE"),
            (51.05, -114.07, "ROOFTOP")))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = GoogleGeocoder(api_key="test-key", client=client)
    assert geo.geocode("41 Heritage Park Way, Cochrane, AB") == (51.05, -114.07)


def test_google_geocode_returns_none_on_zero_results():
    def handler(request):
        return httpx.Response(200, json=_google_ok(0, 0, status="ZERO_RESULTS"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = GoogleGeocoder(api_key="test-key", client=client)
    assert geo.geocode("nowhere at all") is None


def test_google_geocode_returns_none_without_api_key():
    # No key configured -> no network call, graceful None (server still runs).
    def handler(request):  # pragma: no cover - must not be hit
        raise AssertionError("must not call Google without a key")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = GoogleGeocoder(api_key="", client=client)
    assert geo.geocode("123 Maple Dr") is None


@pytest.mark.live
def test_live_geocode_resolves_calgary_address():
    """Real call to Google Geocoding. Skips without a key or if unreachable."""
    import os
    if not os.environ.get("GOOGLE_MAPS_API_KEY"):
        pytest.skip("GOOGLE_MAPS_API_KEY not set")
    geo = GoogleGeocoder()
    try:
        result = geo.geocode("Calgary City Hall, Calgary, AB")
    except Exception as e:
        pytest.skip(f"Google Geocoding unreachable: {e}")
    assert result is not None
    lat, lng = result
    assert 50.5 < lat < 51.5 and -114.5 < lng < -113.5
    print(f"LIVE geocode: {lat}, {lng}")
