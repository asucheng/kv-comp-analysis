# mcp_server/compsource/honestdoor.py
from __future__ import annotations
from datetime import date, datetime
from typing import Any, Optional
import httpx
from mcp_server.models import Comp
from mcp_server.compsource.base import CompSource, PropertyRecord

GRAPHQL_URL = "https://core-backend.honestdoor.com/v2/graphql"
_HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

# Verified-live schema (2026-06-07). Introspection is disabled on the Apollo
# server; field names below were confirmed via error-suggestion probing + live
# queries against getProperties.
_PROPERTIES_QUERY = (
    "query($filter: PropertyFilterInput){ getProperties(filter: $filter){ "
    "fullAddress yearBuilt livingArea bedroomsTotal bathroomsTotal "
    "closePrice closeDate taxAssessedValue predictedValue location { lat lon } } }"
)


def _parse_iso_date(s: str) -> date:
    # closeDate looks like "2026-04-09T00:00:00.000Z"
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def property_to_record(address: str, raw: dict[str, Any]) -> PropertyRecord:
    """Map a Property node to a PropertyRecord (subject attributes)."""
    loc = raw.get("location") or {}
    return PropertyRecord(
        address=raw.get("fullAddress") or address,
        lat=loc.get("lat"), lng=loc.get("lon"),
        sqft=raw.get("livingArea"), year_built=raw.get("yearBuilt"),
        beds=raw.get("bedroomsTotal"), baths=raw.get("bathroomsTotal"),
        property_type="detached",
        hd_estimate=raw.get("predictedValue"),
        assessed_value=raw.get("taxAssessedValue"),
    )


def parse_sales(rows: list[dict[str, Any]]) -> list[Comp]:
    """Map Property nodes to Comps, keeping only usable real sales.

    A Property is usable as a comp only if it has a real sale (closePrice +
    closeDate), a living area (for $/sqft), and coordinates. The bulk feed is
    sparse, so most rows are skipped — that is expected and honest.
    """
    comps: list[Comp] = []
    for r in rows:
        loc = r.get("location") or {}
        if not (r.get("closePrice") and r.get("closeDate")
                and r.get("livingArea") and loc.get("lat") is not None
                and loc.get("lon") is not None):
            continue
        comps.append(Comp(
            address=r.get("fullAddress") or "(address withheld)",
            lat=loc["lat"], lng=loc["lon"],
            sold_price=float(r["closePrice"]),
            sold_date=_parse_iso_date(r["closeDate"]),
            sqft=float(r["livingArea"]),
            beds=r.get("bedroomsTotal"), baths=r.get("bathroomsTotal"),
            year_built=r.get("yearBuilt"), property_type="detached",
        ))
    return comps


class HonestDoorCompSource(CompSource):
    """Live HonestDoor public data via GraphQL. Inject `client` for tests.

    VERIFIED SCHEMA (2026-06-07): endpoint reachable, no Turnstile on the API;
    introspection disabled. Real query: getProperties(filter:{neighbourhoodName}).
    Property carries closePrice/closeDate/livingArea/yearBuilt/bedroomsTotal/
    bathroomsTotal/predictedValue/taxAssessedValue/location{lat,lon}.

    REAL-BUT-PARTIAL — synthetic source is the demo default because:
      1. Attribute sparsity: livingArea/yearBuilt/beds/baths are NULL for ~90% of
         bulk records; parse_sales skips rows missing closePrice/closeDate/
         livingArea/location.
      2. getProperty is slug-only — no address search — so get_property() below
         cannot resolve a raw address against the public API.
      3. neighbourhoodName is not geo-scoped (e.g. "Roxboro" returns Moncton, NB);
         rely on the caller's haversine radius filter to drop far matches.
    """

    def __init__(self, client: Optional[httpx.Client] = None):
        self._client = client or httpx.Client(headers=_HEADERS, timeout=20)

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(GRAPHQL_URL, json={"query": query, "variables": variables})
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"HonestDoor GraphQL error: {body['errors']}")
        return body.get("data", {})

    def get_property(self, address: str) -> PropertyRecord:
        raise NotImplementedError(
            "HonestDoor's public GraphQL exposes property lookup by slug only "
            "(no address search). Resolve the subject via SyntheticCompSource or "
            "user-provided overrides. See module docstring."
        )

    def recent_sales(self, community: str, *, lookback_months: int, as_of: date) -> list[Comp]:
        data = self._query(_PROPERTIES_QUERY, {"filter": {"neighbourhoodName": community}})
        return parse_sales(data.get("getProperties") or [])
