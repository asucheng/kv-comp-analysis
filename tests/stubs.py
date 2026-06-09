"""Offline test doubles. These replace the deleted synthetic source's
fixture role — deterministic, no network — so pipeline/backtest tests stay
hermetic. They are test-only and never shipped in the package."""
from __future__ import annotations
from datetime import date, timedelta
from typing import Optional
from mcp_server.models import Comp
from mcp_server.compsource.base import CompSource, PropertyRecord


class StubGeocoder:
    """Deterministic geocoder returning a fixed point."""

    def __init__(self, latlng: tuple[float, float] = (51.05, -114.07)):
        self._latlng = latlng

    def geocode(self, address: str) -> Optional[tuple[float, float]]:
        return self._latlng


class StubCompSource(CompSource):
    """Mirrors HonestDoor: a fuzzy subject search (returns the injected ranked
    `matches`, or nothing) plus a tight cluster of nearby sold comps that survive
    the default criteria around a ~1800 sqft, ~2000-built detached subject."""

    def __init__(self, n: int = 12, matches: Optional[list[PropertyRecord]] = None):
        self._n = n
        self._matches = matches or []

    def search_subject(self, address: str) -> list[PropertyRecord]:
        return list(self._matches)  # [] => like HonestDoor with no match

    def recent_sales(self, *, lat: float, lng: float, radius_km: float,
                     lookback_months: int, as_of: date) -> list[Comp]:
        comps: list[Comp] = []
        for i in range(self._n):
            dlat = ((i % 4) - 1.5) * 0.004      # within ~0.7 km
            dlng = ((i // 4) - 1.0) * 0.004
            sqft = 1700 + (i % 4) * 60          # 1700..1880 (±20% of 1800)
            ppsf = 500 * (1 + ((i % 5) - 2) * 0.03)  # 0.94..1.06 x base
            comps.append(Comp(
                address=f"{100 + i} Test St",
                lat=round(lat + dlat, 6), lng=round(lng + dlng, 6),
                sold_price=round(sqft * ppsf, -2),
                sold_date=as_of - timedelta(days=30 + i * 15),
                sqft=float(sqft), beds=3, baths=2.0,
                year_built=1998 + (i % 6), property_type="detached",
            ))
        return comps
