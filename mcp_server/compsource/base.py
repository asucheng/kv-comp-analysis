from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import date
from typing import Optional
from pydantic import BaseModel
from mcp_server.models import Comp, PropertyType


class PropertyRecord(BaseModel):
    """Raw attributes for a single property from a data source."""
    address: str
    community: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    sqft: Optional[float] = None
    year_built: Optional[int] = None
    beds: Optional[float] = None
    baths: Optional[float] = None
    lot_sf: Optional[float] = None
    property_type: Optional[PropertyType] = None
    hd_estimate: Optional[float] = None        # AVM estimate (NOT a sale)
    assessed_value: Optional[float] = None     # municipal assessment, if known


class CompSource(ABC):
    """Pluggable data source. Implementations: synthetic, HonestDoor, MLS, internal."""

    @abstractmethod
    def get_property(self, address: str) -> PropertyRecord:
        """Resolve a single subject property's attributes."""

    @abstractmethod
    def recent_sales(self, community: str, *, lookback_months: int, as_of: date) -> list[Comp]:
        """Candidate recent sales in a community (unfiltered by Sam's 5)."""
