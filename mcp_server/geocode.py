from __future__ import annotations
from typing import Optional, Protocol
import httpx


class Geocoder(Protocol):
    """Resolve a free-text address to (lat, lng), or None if not found."""

    def geocode(self, address: str) -> Optional[tuple[float, float]]: ...

# OpenStreetMap Nominatim — free, no API key. Usage policy requires an
# identifying User-Agent and low request volume; comp analysis geocodes one
# subject address per run, so this is well within bounds.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "kv-comp-analysis/0.1 (KV Capital comp-analysis MCP)"}


class NominatimGeocoder:
    """Resolve a street address to (lat, lng) via OSM Nominatim. Inject `client`
    for tests. Biased to Canada since the tool serves the Calgary market."""

    def __init__(self, client: Optional[httpx.Client] = None):
        self._client = client or httpx.Client(headers=_HEADERS, timeout=30)

    def geocode(self, address: str) -> Optional[tuple[float, float]]:
        resp = self._client.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "ca"},
            headers=_HEADERS,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        top = results[0]
        return float(top["lat"]), float(top["lon"])
