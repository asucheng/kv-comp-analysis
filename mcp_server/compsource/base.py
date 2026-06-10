from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date
from typing import Optional
from pydantic import BaseModel
from mcp_server.models import Comp, PropertyType


class PropertyRecord(BaseModel):
    """Raw attributes for a single property from a data source."""
    address: str
    slug: Optional[str] = None                 # source's canonical id, if any
    property_id: Optional[str] = None          # source's property id (for listing enrichment)
    resolved_address: Optional[str] = None     # readable address the source matched
    community: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    sqft: Optional[float] = None
    year_built: Optional[int] = None
    beds: Optional[float] = None
    baths: Optional[float] = None
    garage: Optional[int] = None     # garage spaces (MLS numGarageSpaces, else parsed from parking_type)
    parking_type: Optional[str] = None  # MLS descriptive parking, e.g. "Double Garage Detached"
    lot_sf: Optional[float] = None
    property_type: Optional[PropertyType] = None
    hd_estimate: Optional[float] = None        # AVM estimate (NOT a sale)
    assessed_value: Optional[float] = None     # municipal assessment, if known


class CompSource(ABC):
    """Pluggable data source. Implementations: HonestDoor, MLS, internal."""

    @abstractmethod
    def search_subject(self, address: str) -> list[PropertyRecord]:
        """Resolve a subject from free address text: ranked candidates, best first.
        Fuzzy sources never assert an exact match, so the caller confirms; an empty
        list means nothing matched at all."""

    def get_property(self, address: str) -> PropertyRecord:
        """Best-match record for an address (the top search hit), or an empty record."""
        recs = self.search_subject(address)
        return recs[0] if recs else PropertyRecord(address=address)

    def enrich_subject(self, record: PropertyRecord) -> PropertyRecord:
        """Optionally fill richer attributes for the chosen subject (e.g. from its own
        listing). Default: no-op. Sources with per-property listing data override this."""
        return record

    @abstractmethod
    def recent_sales(self, *, lat: float, lng: float, radius_km: float,
                     lookback_months: int, as_of: date) -> list[Comp]:
        """Candidate recent sales within `radius_km` of (lat, lng), unfiltered by
        Sam's full criteria (the caller applies the precise radius/size/age filters)."""
