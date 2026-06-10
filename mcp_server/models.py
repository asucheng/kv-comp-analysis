from __future__ import annotations
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field, computed_field

PropertyType = Literal["detached", "semi", "townhouse", "condo", "other"]
Confidence = Literal["high", "medium", "low"]
AdjMethod = Literal["matched_pair", "grouping", "regression", "cost_convention", "none"]
SourceType = Literal["article-method", "our-judgment"]


class Subject(BaseModel):
    address: str
    community: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    sqft: Optional[float] = None
    year_built: Optional[int] = None
    beds: Optional[float] = None
    baths: Optional[float] = None
    garage: Optional[int] = None     # garage spaces (HonestDoor garageSpaces; often unknown)
    lot_sf: Optional[float] = None
    property_type: Optional[PropertyType] = None
    hd_estimate: Optional[float] = None
    # field name -> "user" | "honestdoor" | "geocoded" | "missing"
    provenance: dict[str, str] = Field(default_factory=dict)
    # address the data source matched (top hit) + other near matches; the agent
    # confirms `resolved_address` against the user's input before valuing.
    resolved_address: Optional[str] = None
    match_candidates: list[str] = Field(default_factory=list)


class Comp(BaseModel):
    address: str
    lat: float
    lng: float
    sold_price: float
    sold_date: date
    sqft: float
    beds: Optional[float] = None
    baths: Optional[float] = None
    garage: Optional[int] = None     # garage spaces (HonestDoor garageSpaces; often unknown)
    year_built: Optional[int] = None
    property_type: Optional[PropertyType] = None
    distance_km: Optional[float] = None
    include_reason: Optional[str] = None

    @computed_field  # type: ignore[misc]
    @property
    def price_per_sqft(self) -> float:
        return round(self.sold_price / self.sqft, 2)


class Criteria(BaseModel):
    radius_km: float = 3.0
    size_pct: float = 0.20
    lookback_months: int = 6
    age_years: int = 10
    # Secondary exact-match constraints. beds/baths/garage are strict-by-default and
    # null-safe (a missing value never drops a comp); the ladder relaxes them when too
    # few comps qualify. match_type stays off: subject property_type is frequently
    # unknown and the type filter is not null-safe, so defaulting it on would wrongly
    # drop every comp.
    match_type: bool = False
    match_beds: bool = True
    match_baths: bool = True
    match_garage: bool = True
    min_comps: int = 4


class Relaxation(BaseModel):
    step: str                          # which dimension, e.g. "lookback_months" or "match_garage"
    from_: float | bool = Field(alias="from")   # bool when relaxing an exact-match toggle
    to: float | bool
    model_config = {"populate_by_name": True}


class FindCompsResult(BaseModel):
    comps: list[Comp]
    candidates_considered: int
    relaxations: list[Relaxation] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class AdjustmentRules(BaseModel):
    """Config only — no adjustment magnitudes (those are derived from the comps)."""
    trend_clamp: float = 0.02     # max |monthly time trend|
    min_comps: int = 4
    outlier_iqr: float = 1.5      # IQR multiplier if drop_outliers is on
    drop_outliers: bool = False   # median blend tolerates outliers; off by default


class Overrides(BaseModel):
    """Human-supplied coefficients that replace a derived one (inspect-then-override)."""
    time_pct_per_month: Optional[float] = None
    marginal_ppsf: Optional[float] = None
    bed_value: Optional[float] = None
    bath_value: Optional[float] = None
    garage_value: Optional[float] = None


class Adjustment(BaseModel):
    factor: str                       # "time" | "size" | "beds" | "baths" | "garage"
    method_used: AdjMethod
    source_type: SourceType
    value_pct: Optional[float] = None     # percentage adjustments (time)
    value_dollar: Optional[float] = None  # dollar adjustments (size/features)
    evidence: str
    confidence: Confidence
    rationale: str


class Disclosure(BaseModel):
    """A Tier-2 (filtered-not-adjusted) caveat: imbalance + likely direction of bias."""
    factor: str                       # "age" | "location" | "transactional"
    skew: str
    direction: str                    # "understate" | "overstate" | "unknown"
    caveat: str
    source_type: SourceType = "our-judgment"


class CompAdjustment(BaseModel):
    address: str
    raw_price: float
    raw_ppsf: float
    adjustments: list[Adjustment]
    adjusted_price: float             # this comp's indication of subject value
    adjusted_ppsf: float


class Estimate(BaseModel):
    point: float
    low: float
    high: float
    confidence: Confidence
    per_comp: list[CompAdjustment]
    disclosures: list[Disclosure] = Field(default_factory=list)
    method_notes: list[str] = Field(default_factory=list)


class CrossCheck(BaseModel):
    hd_avm: Optional[float] = None
    assessed_value: Optional[float] = None
    vs_avm_pct: Optional[float] = None
    vs_assessment_pct: Optional[float] = None
    verdict: str
    notes: list[str] = Field(default_factory=list)
