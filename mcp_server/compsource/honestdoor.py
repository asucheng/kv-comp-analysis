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
    "query($filter: Listings2ExtendedFilterInput, $take: Int, $skip: Int, $order: Listing2OrderInput){ "
    "getListings2(take: $take, skip: $skip, filter: $filter, order: $order){ "
    "soldPrice soldDate status type "
    "address { streetNumber streetName city neighborhood } "
    "details { numGarageSpaces numBedrooms numBedroomsPlus numBathrooms numBathroomsPlus propertyType } "
    "condominium { parkingType } "
    "property { livingArea bedroomsTotal bedroomsTotalEst bathroomsTotal bathroomsTotalEst "
    "garageSpaces yearBuilt location { lat lon } } } }"
)
_TAKE = 300                # rows per page (server cap); recent_sales paginates past it
_MAX_PAGES = 40           # safety stop (~12k rows) — a 3km×12mo window never approaches this

# Resolve a subject by ADDRESS TEXT via getMultiSearch (the website's own search).
# It's fuzzy and ranked — always returns candidates and never flags an exact match —
# so search_subject returns the ranked list and the *caller* (agent + human) confirms
# the address. `item` is a full Property; lotSizeArea is m², predictedValue is the AVM.
_MULTISEARCH_QUERY = (
    "query($filter: MultiSearchFilterInput!){ "
    "getMultiSearch(filter: $filter){ properties{ item{ "
    "id slug livingArea bedroomsTotal bedroomsTotalEst bathroomsTotal bathroomsTotalEst "
    "garageSpaces yearBuilt "
    "lotSizeArea neighbourhoodName predictedValue location{ lat lon } } } } }"
)

# Fetch a single property's MLS listing(s) by propertyId (newest first) to enrich the
# resolved subject with MLS attributes the public Property entity lacks (garage,
# property type, parking). Returns nothing when the subject has no listing on record.
_LISTING_BY_PROPERTY_QUERY = (
    "query($filter: Listings2ExtendedFilterInput, $take: Int, $order: Listing2OrderInput){ "
    "getListings2(take: $take, filter: $filter, order: $order){ "
    "details { numGarageSpaces numBedrooms numBedroomsPlus numBathrooms numBathroomsPlus propertyType } "
    "condominium { parkingType } } }"
)
_SQM_TO_SQFT = 10.7639


def _coalesce_attr(d: dict[str, Any], exact_key: str, est_key: str):
    """Prefer the confirmed value; fall back to HonestDoor's `*Est` estimate (what the
    website shows) when the exact field is null. Used as the LAST resort for bed/bath
    behind the MLS `details` block (see listing_to_comp); the public `property` entity
    leaves bedroomsTotal/bathroomsTotal null on ~37% of sold records but populates the
    estimate."""
    v = d.get(exact_key)
    return v if v is not None else d.get(est_key)


def _first(*vals):
    """First value that isn't None (keeps a legitimate 0)."""
    for v in vals:
        if v is not None:
            return v
    return None


def _to_num(v):
    """Parse HonestDoor's stringly-typed numbers ('3', '1447.20') -> float, else None."""
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _mls_beds(details: dict[str, Any]):
    """Total bedrooms = above-grade + below-grade (numBedrooms + numBedroomsPlus)."""
    base = _to_num(details.get("numBedrooms"))
    return None if base is None else base + (_to_num(details.get("numBedroomsPlus")) or 0)


def _mls_baths(details: dict[str, Any]):
    """HonestDoor's X.Y bath convention = full.half. MLS `numBathrooms` is the TOTAL bath
    count and `numBathroomsPlus` is how many of those are half-baths, so full = total - half
    (e.g. 3 total / 1 half -> 2 full + 1 half -> 2.1; verified vs HonestDoor's bathroomsTotal)."""
    total = _to_num(details.get("numBathrooms"))
    if total is None:
        return None
    half = _to_num(details.get("numBathroomsPlus")) or 0
    return round(max(total - half, 0) + half / 10, 1)


_GARAGE_WORDS = {"single": 1, "double": 2, "triple": 3, "quadruple": 4, "quad": 4}


def _garage_from_parking(parking_type: Optional[str]) -> Optional[int]:
    """Extract a garage stall count from a descriptive parkingType (e.g.
    'Double Garage Detached' -> 2). Returns None when there's no '<count> garage' phrase
    (driveway/off-street/underground parking carries no countable garage)."""
    if not parking_type:
        return None
    m = re.search(r"(single|double|triple|quadruple|quad)\s+garage", parking_type.lower())
    return _GARAGE_WORDS[m.group(1)] if m else None


def _map_property_type(pt: Optional[str]):
    """Map MLS propertyType text to our PropertyType. Check 'semi' before 'detach'
    because 'Semi Detached (Half Duplex)' contains 'detached'."""
    if not pt:
        return None
    p = pt.lower()
    if "semi" in p:
        return "semi"
    if "row" in p or "town" in p:
        return "townhouse"
    if "apart" in p or "condo" in p:
        return "condo"
    if "detach" in p:
        return "detached"
    return "other"


_PROVINCES = {"ab", "bc", "sk", "mb", "on", "qc", "ns", "nb", "nl", "pe", "nt", "yt", "nu"}
_DIRECTIONS = {"se", "sw", "ne", "nw", "n", "s", "e", "w"}


def _slug_to_address(slug: str) -> str:
    """Render a HonestDoor slug as a readable address for the agent/user to confirm,
    e.g. '122-auburn-bay-heights-se-calgary-ab' -> '122 Auburn Bay Heights SE Calgary AB',
    '5687-yew-street-vancouver-bc-phflv' -> '5687 Yew Street Vancouver BC'. Slugs are
    '<street>-<city>-<prov>' with an optional random suffix after the province; we trim
    at the province and upper-case province/direction tokens."""
    toks = slug.split("-")
    prov_idx = max((i for i, t in enumerate(toks) if t in _PROVINCES), default=None)
    if prov_idx is not None:
        toks = toks[: prov_idx + 1]
    return " ".join(t.upper() if t in _PROVINCES or t in _DIRECTIONS else t.capitalize()
                    for t in toks)


def multisearch_item_to_record(item: dict[str, Any]) -> PropertyRecord:
    """Map a getMultiSearch `properties[].item` (a Property) to a PropertyRecord,
    carrying its slug and a readable resolved_address (lotSizeArea m² -> lot_sf)."""
    loc = item.get("location") or {}
    lot = item.get("lotSizeArea")
    slug = item.get("slug")
    return PropertyRecord(
        address=_slug_to_address(slug), slug=slug, property_id=item.get("id"),
        resolved_address=_slug_to_address(slug),
        community=item.get("neighbourhoodName"),
        lat=loc.get("lat"), lng=loc.get("lon"),
        sqft=item.get("livingArea"),
        year_built=item.get("yearBuilt"),
        beds=_coalesce_attr(item, "bedroomsTotal", "bedroomsTotalEst"),
        baths=_coalesce_attr(item, "bathroomsTotal", "bathroomsTotalEst"),
        garage=item.get("garageSpaces"),
        lot_sf=round(lot * _SQM_TO_SQFT) if lot else None,
        hd_estimate=item.get("predictedValue"),
    )


def _parse_iso_date(s: str) -> date:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def _months_before(d: date, months: int) -> date:
    """The date `months` whole months before `d`, clamping the day for short months."""
    import calendar
    m = d.month - 1 - months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


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
    # Prefer the richer MLS `details`/`condominium` blocks (near-complete coverage);
    # fall back to the sparse public `property` entity (+ its *Est estimates).
    details = row.get("details") or {}
    parking_type = (row.get("condominium") or {}).get("parkingType")
    return Comp(
        address=street, lat=loc["lat"], lng=loc["lon"],
        sold_price=float(row["soldPrice"]),
        sold_date=_parse_iso_date(row["soldDate"]),
        sqft=float(prop["livingArea"]),
        beds=_first(_mls_beds(details), _coalesce_attr(prop, "bedroomsTotal", "bedroomsTotalEst")),
        baths=_first(_mls_baths(details), _coalesce_attr(prop, "bathroomsTotal", "bathroomsTotalEst")),
        garage=_first(_to_num(details.get("numGarageSpaces")),
                      _garage_from_parking(parking_type), prop.get("garageSpaces")),
        parking_type=parking_type,
        year_built=prop.get("yearBuilt"),
        property_type=_map_property_type(details.get("propertyType")) or "detached",
    )


class HonestDoorCompSource(CompSource):
    """Live HonestDoor public data via GraphQL. Inject `client` for tests.

    VERIFIED LIVE (2026-06-07): endpoint reachable, unauthenticated, no Turnstile.
    `recent_sales` enumerates sold listings inside a bbox around the subject via
    getListings2. Each Listing2 node carries the MLS `details` block (numGarageSpaces,
    numBedrooms/Plus, numBathrooms/Plus, propertyType) and `condominium.parkingType` —
    near-complete coverage — plus the sparser public `property` entity used as fallback.
    Only structured attributes are extracted (no photos/agents/descriptions).

    `search_subject` resolves a subject from free address text via getMultiSearch —
    the same nationwide search the website uses. It is fuzzy and ranked and always
    returns candidates, so it never asserts an exact match; the caller (agent + a
    human-approve gate) confirms the resolved address before valuing.
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

    def search_subject(self, address: str) -> list[PropertyRecord]:
        # getMultiSearch returns up to ~5 fuzzy, ranked candidates (best first) and
        # never flags an exact match — so we return the ranked list and let the
        # caller confirm. Empty list => nothing matched at all.
        data = self._query(_MULTISEARCH_QUERY, {"filter": {"query": address}})
        props = (data.get("getMultiSearch") or {}).get("properties") or []
        return [multisearch_item_to_record(p["item"]) for p in props
                if (p.get("item") or {}).get("slug")]

    def enrich_subject(self, record: PropertyRecord) -> PropertyRecord:
        """Fill the resolved subject's MLS attributes (garage, property type, parking,
        and more-reliable bed/bath) from its own listing, fetched by propertyId. The
        public `getMultiSearch` Property is sparse on these; the listing is not. No-op
        when the subject has no propertyId or no listing on record."""
        if not record.property_id:
            return record
        data = self._query(_LISTING_BY_PROPERTY_QUERY, {
            "filter": {"propertyId": record.property_id}, "take": 1,
            "order": {"soldDate": "desc"}})
        rows = data.get("getListings2") or []
        if not rows:
            return record
        details = rows[0].get("details") or {}
        parking_type = (rows[0].get("condominium") or {}).get("parkingType")
        garage = _first(_to_num(details.get("numGarageSpaces")),
                        _garage_from_parking(parking_type), record.garage)
        return record.model_copy(update={
            "beds": _first(_mls_beds(details), record.beds),
            "baths": _first(_mls_baths(details), record.baths),
            "garage": int(garage) if garage is not None else None,
            "parking_type": parking_type or record.parking_type,
            "property_type": _map_property_type(details.get("propertyType")) or record.property_type,
        })

    def recent_sales(self, *, lat: float, lng: float, radius_km: float,
                     lookback_months: int, as_of: date) -> list[Comp]:
        # Enumerate the COMPLETE pool inside the bbox within the recency window:
        # the API caps each page at ~_TAKE non-spatially, so we bound the set with a
        # server-side soldDate window, sort newest-first for stable paging, then walk
        # `skip` until a short page signals the window is exhausted. This avoids the
        # old single-`take` grab that dropped near/recent comps spatially-blindly.
        top_left, bottom_right = bbox(lat, lng, radius_km)
        cutoff = _months_before(as_of, lookback_months)
        filt = {"bbox": {"topLeft": top_left, "bottomRight": bottom_right},
                "soldDate": {"gte": cutoff.isoformat()}}
        order = {"soldDate": "desc"}
        out: list[Comp] = []
        for page in range(_MAX_PAGES):
            data = self._query(_LISTINGS_QUERY, {
                "filter": filt, "take": _TAKE, "skip": page * _TAKE, "order": order})
            rows = data.get("getListings2") or []
            out.extend(c for c in (listing_to_comp(r) for r in rows) if c is not None)
            if len(rows) < _TAKE:        # last (partial) page — window exhausted
                break
        return out
