# mcp_server/compsource/honestdoor.py
from __future__ import annotations
import re
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

# Resolve a single subject by slug. HonestDoor has no address-text search, but the
# slug is derivable from the address (see _slug_candidates). lotSizeArea is in m²;
# predictedValue is the AVM estimate (NOT a sale).
_PROPERTY_QUERY = (
    "query($filter: PropertyUniqueFilterInput!){ "
    "getProperty(filter: $filter){ "
    "livingArea bedroomsTotal bathroomsTotal yearBuilt "
    "lotSizeArea neighbourhoodName predictedValue "
    "location { lat lon } } }"
)
_SQM_TO_SQFT = 10.7639


def _slugify_address(address: str) -> str:
    """Build HonestDoor's property slug from a street address. Slugs are
    '<street>-<city>-<province>', lowercased and hyphenated, with the
    neighbourhood and postal code omitted, e.g.
    '122 Auburn Bay Heights SE, Auburn Bay, Calgary, AB T3M 0A7'
        -> '122-auburn-bay-heights-se-calgary-ab'.
    v1 is Calgary-only (see scope), so city/province are pinned to Calgary AB; an
    extension to other cities would parse them from the address instead."""
    street = address.split(",")[0]
    street = re.sub(r"\b(calgary|alberta|ab)\b", " ", street, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "-", f"{street} calgary ab".lower()).strip("-")


def _slug_candidates(address: str) -> list[str]:
    """Slug candidates to try in order. The clean slug carries the real data in
    practice; HonestDoor also stores duplicate records under '<num>-r-...' /
    '<num>-v-...' variants that are usually empty shells, kept here only as
    insurance. get_property keeps the first candidate that returns *populated*
    attributes (data presence, not mere slug existence)."""
    clean = _slugify_address(address)
    num, _, rest = clean.partition("-")
    if not rest:
        return [clean]
    return [clean, f"{num}-r-{rest}", f"{num}-v-{rest}"]


def property_to_record(address: str, node: dict[str, Any]) -> PropertyRecord:
    """Map a getProperty node to a PropertyRecord (lotSizeArea m² -> lot_sf)."""
    loc = node.get("location") or {}
    lot = node.get("lotSizeArea")
    return PropertyRecord(
        address=address,
        community=node.get("neighbourhoodName"),
        lat=loc.get("lat"), lng=loc.get("lon"),
        sqft=node.get("livingArea"),
        year_built=node.get("yearBuilt"),
        beds=node.get("bedroomsTotal"),
        baths=node.get("bathroomsTotal"),
        lot_sf=round(lot * _SQM_TO_SQFT) if lot else None,
        hd_estimate=node.get("predictedValue"),
    )


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

    `get_property` resolves the subject by slug: the public API has no address-text
    search, but the slug is derivable from the address (see _slug_candidates), so a
    raw address *does* resolve to real attributes. Only when the property is absent
    from HonestDoor does it return an empty record (caller then asks the user).
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
        # Slug candidate-and-verify: try the clean slug, then duplicate-record
        # variants, and keep the first whose attributes are populated (livingArea
        # present) — the variants are usually empty shells. If none resolve, the
        # property isn't in HonestDoor: return an empty record so get_subject marks
        # fields "missing" and the Skill asks the user.
        for slug in _slug_candidates(address):
            node = self._query(_PROPERTY_QUERY, {"filter": {"slug": slug}}).get("getProperty")
            if node and node.get("livingArea") is not None:
                return property_to_record(address, node)
        return PropertyRecord(address=address)

    def recent_sales(self, *, lat: float, lng: float, radius_km: float,
                     lookback_months: int, as_of: date) -> list[Comp]:
        top_left, bottom_right = bbox(lat, lng, radius_km)
        filt = {"bbox": {"topLeft": top_left, "bottomRight": bottom_right}}
        data = self._query(_LISTINGS_QUERY, {"filter": filt, "take": _TAKE})
        rows = data.get("getListings2") or []
        return [c for c in (listing_to_comp(r) for r in rows) if c is not None]
