from __future__ import annotations
import os
from typing import Optional, Protocol
import httpx


class Geocoder(Protocol):
    """Resolve a free-text address to (lat, lng), or None if not found."""

    def geocode(self, address: str) -> Optional[tuple[float, float]]: ...


# Google Maps Geocoding API. Unlike HonestDoor's property index (an AVM database
# that only knows already-ingested properties), geocoding resolves ANY valid
# address — including brand-new builds — so it is the authoritative source for the
# subject's coordinates. Mirrors the KV-Capital-propcomp-ai approach. Needs
# GOOGLE_MAPS_API_KEY; returns None (no network call) when unset so the server
# still runs.
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# location_type values precise enough to anchor a comp search: a rooftop fix, or a
# point interpolated between known address points on a road. GEOMETRIC_CENTER and
# APPROXIMATE are street/city centroids — accepting one would silently pin the
# subject (and its comp radius) on a bogus point, so we reject and let the caller
# fall back.
_PRECISE_LOCATION_TYPES = {"ROOFTOP", "RANGE_INTERPOLATED"}


class GoogleGeocoder:
    """Resolve a street address to (lat, lng) via Google Maps Geocoding, restricted
    to Canada. Inject `api_key`/`client` for tests; otherwise the key is read from
    GOOGLE_MAPS_API_KEY."""

    def __init__(self, api_key: Optional[str] = None, client: Optional[httpx.Client] = None,
                 region: str = "AB"):
        self._api_key = api_key if api_key is not None else os.environ.get("GOOGLE_MAPS_API_KEY", "")
        self._client = client or httpx.Client(timeout=30)
        # Pin results to the market's province (and Canada) so a bare street address
        # can't resolve to a same-named street in another region.
        self._components = f"administrative_area:{region}|country:CA" if region else "country:CA"

    def geocode(self, address: str) -> Optional[tuple[float, float]]:
        if not self._api_key:
            return None
        resp = self._client.get(
            GOOGLE_GEOCODE_URL,
            params={"address": address, "key": self._api_key, "components": self._components},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK":
            return None
        for result in data.get("results", []):
            geometry = result["geometry"]
            if geometry.get("location_type") in _PRECISE_LOCATION_TYPES:
                loc = geometry["location"]
                return float(loc["lat"]), float(loc["lng"])
        return None
