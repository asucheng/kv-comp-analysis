from __future__ import annotations
from datetime import date
from typing import Literal, Optional
from pydantic import BaseModel, Field, computed_field

PropertyType = Literal["detached", "semi", "townhouse", "condo", "other"]
Confidence = Literal["high", "medium", "low"]
AdjMethod = Literal["matched_pair", "grouping", "regression", "cost_convention", "none"]
SourceType = Literal["article-method", "our-judgment"]
Direction = Literal["understate", "overstate", "unknown"]


class Subject(BaseModel):
    address: str
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
    garage: Optional[int] = None     # garage spaces (MLS numGarageSpaces, else parsed from parking_type)
    parking_type: Optional[str] = None  # MLS descriptive parking, e.g. "Double Garage Detached"
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
    # Secondary exact-match constraints — all OFF by default. Comp SELECTION uses only
    # Sam's 5 (radius/size/recency/age/$-per-sqft); bed/bath/garage differences are
    # handled by the adjustment engine in estimate_value (matched-pair -> grouping ->
    # regression), NOT by filtering — and matching them out would strip the variation
    # the engine needs to derive their value. Toggles remain available to switch on
    # per-case (null-safe for beds/baths/garage); the ladder can relax them if enabled.
    match_type: bool = False
    match_beds: bool = False
    match_baths: bool = False
    match_garage: bool = False
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


class PairTrace(BaseModel):
    comp_a: str
    comp_b: str
    detail: str          # human arithmetic, e.g. "Δ$46,355 over 167 sqft"
    value: float         # per-unit value this pair implies (pct for time, $ otherwise)


class CoefficientTrace(BaseModel):
    factor: str                       # time | size | beds | baths | garage
    method: AdjMethod
    source_type: SourceType
    value: float                      # pct for time, $ otherwise
    is_pct: bool
    confidence: Confidence
    equation: str                     # general formula used
    pairs: list[PairTrace] = Field(default_factory=list)
    groups: Optional[dict] = None     # populated when method == grouping
    regression: Optional[dict] = None # populated when method == regression
    aggregate: str                    # e.g. "median of 3 pairs = $19,580"
    summary: str                      # = existing evidence string (fallback)


class Disclosure(BaseModel):
    """A Tier-2 (filtered-not-adjusted) caveat: imbalance + likely direction of bias."""
    factor: str                       # "age" | "location" | "transactional"
    skew: str
    direction: Direction
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
    coefficients: list[CoefficientTrace] = Field(default_factory=list)
    # Server-issued handle for this result. render_report takes this id instead of the
    # whole estimate, so the model never re-emits the (large) object back through itself.
    estimate_id: Optional[str] = None


class CrossCheck(BaseModel):
    hd_avm: Optional[float] = None
    assessed_value: Optional[float] = None
    vs_avm_pct: Optional[float] = None
    vs_assessment_pct: Optional[float] = None
    verdict: str
    notes: list[str] = Field(default_factory=list)


class ReportComp(BaseModel):
    comp: Comp
    kept: bool = True
    exclude_reason: Optional[str] = None


class ReportPayload(BaseModel):
    subject: Subject
    comps: list[ReportComp]
    estimate: Estimate
    confidence_reasoning: str = ""
    target_warnings: list[str] = Field(default_factory=list)
    verify_next: list[str] = Field(default_factory=list)
    as_of: date
