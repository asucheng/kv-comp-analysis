from __future__ import annotations
from datetime import date, datetime
from typing import Any, Optional
import httpx
from mcp_server.models import Comp
from mcp_server.compsource.base import CompSource, PropertyRecord

GRAPHQL_URL = "https://core-backend.honestdoor.com/v2/graphql"
_HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

# NOTE: exact GraphQL query strings + field names must be confirmed against the
# live API during the Step 1 spike (the schema below is the integration target).
_PROPERTY_QUERY = "query($address:String!){ property(address:$address){ \
community latitude longitude squareFootage yearBuilt bedrooms bathrooms \
lotSize avmValue assessedValue } }"
_SALES_QUERY = "query($community:String!,$months:Int!){ recentlySold(\
community:$community, months:$months){ address soldPrice soldDate squareFootage \
latitude longitude bedrooms bathrooms yearBuilt } }"


def parse_property(address: str, raw: dict[str, Any]) -> PropertyRecord:
    return PropertyRecord(
        address=address, community=raw.get("community"),
        lat=raw.get("latitude"), lng=raw.get("longitude"),
        sqft=raw.get("squareFootage"), year_built=raw.get("yearBuilt"),
        beds=raw.get("bedrooms"), baths=raw.get("bathrooms"),
        lot_sf=raw.get("lotSize"), property_type="detached",
        hd_estimate=raw.get("avmValue"), assessed_value=raw.get("assessedValue"),
    )


def parse_sales(rows: list[dict[str, Any]]) -> list[Comp]:
    comps: list[Comp] = []
    for r in rows:
        if not r.get("soldPrice") or not r.get("soldDate"):
            continue  # skip AVM-only / unsold records — REAL sales only
        comps.append(Comp(
            address=r["address"], lat=r["latitude"], lng=r["longitude"],
            sold_price=float(r["soldPrice"]),
            sold_date=datetime.strptime(r["soldDate"], "%Y-%m-%d").date(),
            sqft=float(r["squareFootage"]), beds=r.get("bedrooms"),
            baths=r.get("bathrooms"), year_built=r.get("yearBuilt"),
            property_type="detached",
        ))
    return comps


class HonestDoorCompSource(CompSource):
    """Live HonestDoor public data via GraphQL. Inject `client` for tests.

    SPIKE RESULT (2026-06-07):
    curl -s -X POST "https://core-backend.honestdoor.com/v2/graphql" \
         -H "Content-Type: application/json" -A "Mozilla/5.0" \
         -d '{"query":"{ __typename }"}' | head -c 400
    -> {"data":{"__typename":"Query"}}

    Interpretation: GraphQL endpoint is directly reachable — no Cloudflare/Turnstile
    block. The introspection-style __typename query succeeded with HTTP 200 and a
    valid GraphQL response. However, the specific query field names (_PROPERTY_QUERY,
    _SALES_QUERY) are assumed from page-probe analysis and have NOT been verified
    against the live schema. Integration tests must confirm actual field availability.
    """

    def __init__(self, client: Optional[httpx.Client] = None):
        self._client = client or httpx.Client(headers=_HEADERS, timeout=20)

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(GRAPHQL_URL, json={"query": query, "variables": variables})
        resp.raise_for_status()
        return resp.json().get("data", {})

    def get_property(self, address: str) -> PropertyRecord:
        data = self._query(_PROPERTY_QUERY, {"address": address})
        return parse_property(address, data.get("property") or {})

    def recent_sales(self, community: str, *, lookback_months: int, as_of: date) -> list[Comp]:
        data = self._query(_SALES_QUERY, {"community": community, "months": lookback_months})
        return parse_sales(data.get("recentlySold") or [])
