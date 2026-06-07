# mcp_server/compsource/honestdoor.py
from __future__ import annotations
from datetime import date, datetime
from typing import Any, Optional
import httpx
from mcp_server.models import Comp
from mcp_server.geo import bbox
from mcp_server.compsource.base import CompSource, PropertyRecord

GRAPHQL_URL = "https://core-backend.honestdoor.com/v2/graphql"
_HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

# Verified-live schema (2026-06-07). Introspection is disabled; field names were
# confirmed via error-suggestion probing + live queries. Comps are enumerated by
# a geographic bounding box (the API has no radius filter); the caller's haversine
# filter then trims to the precise radius.
_LISTINGS_QUERY = (
    "query($filter: Listings2ExtendedFilterInput, $take: Int){ "
    "getListings2(take: $take, filter: $filter){ "
    "soldPrice soldDate status type "
    "address { streetNumber streetName city neighborhood } "
    "property { livingArea bedroomsTotal bathroomsTotal yearBuilt location { lat lon } } } }"
)
_TAKE = 300


def _parse_iso_date(s: str) -> date:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def listing_to_comp(row: dict[str, Any]) -> Optional[Comp]:
    """Map a Listing2 node to a Comp, or None if it isn't a usable sold sale.

    Keep only real SALE transactions that have a sold price+date, a living area
    (for $/sqft), and coordinates. The bbox returns active/rental/incomplete rows
    too; those are dropped here."""
    if (row.get("type") or "SALE") != "SALE":
        return None
    prop = row.get("property") or {}
    loc = prop.get("location") or {}
    if not (row.get("soldPrice") and row.get("soldDate") and prop.get("livingArea")
            and loc.get("lat") is not None and loc.get("lon") is not None):
        return None
    addr = row.get("address") or {}
    street = " ".join(
        str(x) for x in (addr.get("streetNumber"), addr.get("streetName")) if x
    ) or "(address withheld)"
    return Comp(
        address=street, lat=loc["lat"], lng=loc["lon"],
        sold_price=float(row["soldPrice"]),
        sold_date=_parse_iso_date(row["soldDate"]),
        sqft=float(prop["livingArea"]),
        beds=prop.get("bedroomsTotal"), baths=prop.get("bathroomsTotal"),
        year_built=prop.get("yearBuilt"), property_type="detached",
    )


class HonestDoorCompSource(CompSource):
    """Live HonestDoor public data via GraphQL. Inject `client` for tests.

    VERIFIED LIVE (2026-06-07): endpoint reachable, unauthenticated, no Turnstile.
    `recent_sales` enumerates sold listings inside a bbox around the subject via
    getListings2, joining `property` for livingArea/beds/baths/yearBuilt/location.

    LIMITATION: the public API has no address->record lookup (getProperty is
    slug-only), so `get_property` cannot resolve a raw address; subject attributes
    must come from user-provided overrides (or a geocoder for coordinates). It
    returns an empty record so the caller marks fields "missing" and asks the user.
    Use politely (low volume); attribute the source.
    """

    def __init__(self, client: Optional[httpx.Client] = None):
        self._client = client or httpx.Client(headers=_HEADERS, timeout=30)

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(GRAPHQL_URL, json={"query": query, "variables": variables})
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"HonestDoor GraphQL error: {body['errors']}")
        return body.get("data", {})

    def get_property(self, address: str) -> PropertyRecord:
        # Public API has no address search (getProperty is slug-only). Return an
        # empty record so get_subject marks fields "missing" and the Skill asks
        # the user. See class docstring.
        return PropertyRecord(address=address)

    def recent_sales(self, *, lat: float, lng: float, radius_km: float,
                     lookback_months: int, as_of: date) -> list[Comp]:
        top_left, bottom_right = bbox(lat, lng, radius_km)
        filt = {"bbox": {"topLeft": top_left, "bottomRight": bottom_right}}
        data = self._query(_LISTINGS_QUERY, {"filter": filt, "take": _TAKE})
        rows = data.get("getListings2") or []
        return [c for c in (listing_to_comp(r) for r in rows) if c is not None]
