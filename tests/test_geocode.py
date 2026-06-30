from __future__ import annotations
import json
import httpx
import pytest
from mcp_server.geocode import NominatimGeocoder, GoogleGeocoder


def test_geocode_returns_lat_lng_from_first_result():
    payload = [{"lat": "51.0447331", "lon": "-114.0718831",
                "display_name": "Calgary, Alberta, Canada"}]
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = NominatimGeocoder(client=client)
    result = geo.geocode("123 Maple Dr, Calgary, AB")
    assert result == (51.0447331, -114.0718831)
    # query the public Nominatim search endpoint with the address
    assert "nominatim.openstreetmap.org/search" in captured["url"]
    assert "Maple" in captured["url"]


def test_geocode_returns_none_when_no_match():
    def handler(request):
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    geo = NominatimGeocoder(client=client)
    assert geo.geocode("nowhere at all") is None


def test_geocode_sends_identifying_user_agent():
    # Nominatim's usage policy requires an identifying User-Agent.
    captured = {}

    def handler(request):
        captured["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    NominatimGeocoder(client=client).geocode("x")
    assert captured["ua"] and "kv-comp-analysis" in captured["ua"].lower()


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
    assert "components=country" in captured["url"]  # restricted to CA


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
    """Real network call to Nominatim. Skips if unreachable."""
    geo = NominatimGeocoder()
    try:
        result = geo.geocode("Calgary City Hall, Calgary, AB")
    except Exception as e:
        pytest.skip(f"Nominatim unreachable: {e}")
    assert result is not None
    lat, lng = result
    assert 50.5 < lat < 51.5 and -114.5 < lng < -113.5
    print(f"LIVE geocode: {lat}, {lng}")
