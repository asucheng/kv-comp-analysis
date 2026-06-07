from __future__ import annotations
import hashlib
import random
from datetime import date, timedelta
from mcp_server.models import Comp
from mcp_server.compsource.base import CompSource, PropertyRecord

# Real-ish Calgary community anchors: (lat, lng, base $/sqft, typical year)
_COMMUNITIES = {
    "Roxboro": (51.025, -114.073, 800, 1955),
    "Legacy": (50.879, -114.044, 380, 2015),
    "Charleswood": (51.094, -114.110, 520, 1965),
    "Evanston": (51.176, -114.108, 360, 2012),
}
_DEFAULT = (51.045, -114.057, 450, 1980)


def _seed_from(text: str, seed: int) -> int:
    h = hashlib.sha256(f"{seed}:{text}".encode()).hexdigest()
    return int(h[:8], 16)


class SyntheticCompSource(CompSource):
    """Deterministic, real-grounded synthetic data — fallback + test fixtures."""

    def __init__(self, seed: int = 0):
        self.seed = seed

    def _anchor(self, community: str | None):
        return _COMMUNITIES.get(community or "", _DEFAULT)

    def get_property(self, address: str) -> PropertyRecord:
        rng = random.Random(_seed_from(address, self.seed))
        community = rng.choice(list(_COMMUNITIES))
        lat, lng, ppsf, yr = self._anchor(community)
        sqft = rng.randint(1400, 2600)
        return PropertyRecord(
            address=address, community=community,
            lat=round(lat + rng.uniform(-0.01, 0.01), 6),
            lng=round(lng + rng.uniform(-0.01, 0.01), 6),
            sqft=sqft, year_built=yr + rng.randint(-15, 15),
            beds=rng.choice([2, 3, 4]), baths=rng.choice([2, 3]),
            lot_sf=rng.randint(3000, 7000), property_type="detached",
            hd_estimate=round(sqft * ppsf * rng.uniform(0.97, 1.03), -2),
            assessed_value=round(sqft * ppsf * rng.uniform(0.92, 1.0), -2),
        )

    def recent_sales(self, community: str, *, lookback_months: int, as_of: date) -> list[Comp]:
        rng = random.Random(_seed_from(community, self.seed) ^ 0xC0FFEE)
        lat, lng, ppsf, yr = self._anchor(community)
        comps: list[Comp] = []
        for i in range(rng.randint(10, 16)):
            sqft = rng.randint(1400, 2600)
            unit_ppsf = ppsf * rng.uniform(0.85, 1.15)
            days_ago = rng.randint(5, lookback_months * 30)
            comps.append(Comp(
                address=f"{100+i} Synthetic Ave, {community}",
                lat=round(lat + rng.uniform(-0.015, 0.015), 6),
                lng=round(lng + rng.uniform(-0.015, 0.015), 6),
                sold_price=round(sqft * unit_ppsf, -2),
                sold_date=as_of - timedelta(days=days_ago),
                sqft=sqft, beds=rng.choice([2, 3, 4]), baths=rng.choice([2, 3]),
                year_built=yr + rng.randint(-15, 15), property_type="detached",
            ))
        return comps
