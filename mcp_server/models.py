from __future__ import annotations
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field, computed_field

PropertyType = Literal["detached", "semi", "townhouse", "condo", "other"]
Confidence = Literal["high", "medium", "low"]


class Subject(BaseModel):
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
    hd_estimate: Optional[float] = None
    # field name -> "user" | "honestdoor" | "missing"
    provenance: dict[str, str] = Field(default_factory=dict)


class Comp(BaseModel):
    address: str
    lat: float
    lng: float
    sold_price: float
    sold_date: date
    sqft: float
    beds: Optional[float] = None
    baths: Optional[float] = None
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
    lookback_months: int = 12
    age_years: int = 10
    match_type: bool = False
    match_beds: bool = False
    min_comps: int = 4


class Relaxation(BaseModel):
    step: str           # which dimension, e.g. "lookback_months"
    from_: float = Field(alias="from")
    to: float
    model_config = {"populate_by_name": True}


class FindCompsResult(BaseModel):
    comps: list[Comp]
    candidates_considered: int
    relaxations: list[Relaxation] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class AdjustmentRules(BaseModel):
    age_rate: float = 0.005      # per year of age difference (newer = premium)
    size_elast: float = 0.20     # per unit of fractional size difference
    trend_clamp: float = 0.02    # max |monthly trend|
    weight_a: float = 0.5        # distance_km coefficient
    weight_b: float = 2.0        # |size%| coefficient
    weight_c: float = 0.05       # |ageΔ years| coefficient
    weight_d: float = 0.1        # months-old coefficient
    outlier_iqr: float = 1.5
    min_comps: int = 4


class Adjustment(BaseModel):
    factor: str          # "time" | "age" | "size"
    pct: float           # multiplicative effect, e.g. +0.015
    rationale: str


class CompAdjustment(BaseModel):
    address: str
    raw_price: float
    raw_ppsf: float
    adjustments: list[Adjustment]
    adjusted_ppsf: float        # comp's subject-equivalent $/sqft
    adjusted_price: float       # adjusted_ppsf * subject.sqft (this comp's indication of subject value)
    weight: float


class Estimate(BaseModel):
    point: float
    low: float
    high: float
    confidence: Confidence
    per_comp: list[CompAdjustment]
    method_notes: list[str] = Field(default_factory=list)


class CrossCheck(BaseModel):
    hd_avm: Optional[float] = None
    assessed_value: Optional[float] = None
    vs_avm_pct: Optional[float] = None
    vs_assessment_pct: Optional[float] = None
    verdict: str
    notes: list[str] = Field(default_factory=list)
