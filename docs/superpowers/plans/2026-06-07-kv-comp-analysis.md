# KV Comp-Analysis Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Python MCP server + a `comp-analysis` Skill that, given a residential subject in Calgary, finds comparable recent sales (KV/Sam's house rules), adjusts them, and produces a transparent underwriter-style value estimate.

**Architecture:** Four read-only MCP tools (`get_subject`, `find_comps`, `estimate_value`, `cross_check`) expose deterministic mechanics; a Skill carries orchestration + judgment + an expandable playbooks layer. Pure logic (filtering, widening ladder, adjustment grid) is network-free and unit-tested; a pluggable `CompSource` abstracts data access (HonestDoor public GraphQL primary, synthetic fallback).

**Tech Stack:** Python 3.11+, FastMCP 3.x (stdio transport — local, no hosting), Pydantic v2, httpx (GraphQL), pytest. Playwright is an optional fallback adapter.

Reference spec: `docs/superpowers/specs/2026-06-06-kv-comp-analysis-design.md`

---

## File structure

```
pyproject.toml                     deps + pytest config + console entry point
README.md                          value prop, data honesty, accuracy number, install/demo
mcp_server/
  __init__.py
  models.py                        Pydantic models (the shared vocabulary)
  geo.py                           haversine distance (pure)
  comps.py                         Sam's-5 filter + ranking + widening ladder (pure)
  estimate.py                      adjustment grid + reconciliation + confidence (pure)
  compsource/
    __init__.py
    base.py                        CompSource ABC + PropertyRecord
    synthetic.py                   SyntheticCompSource (seeded; fallback + test data)
    honestdoor.py                  HonestDoorCompSource (GraphQL + Playwright fallback)
  server.py                        FastMCP server wiring the 4 tools
eval/
  backtest.py                      hold-one-out accuracy harness
tests/
  test_models.py  test_geo.py  test_comps.py  test_estimate.py
  test_synthetic.py  test_honestdoor.py  test_server.py  test_backtest.py
skill/
  comp-analysis/
    SKILL.md
    references/methodology.md
    references/house-rules.md
    playbooks/README.md
    playbooks/acreage-with-outbuildings.md
```

---

## Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `mcp_server/__init__.py` (empty)
- Create: `mcp_server/compsource/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `.gitignore`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "kv-comp-analysis"
version = "0.1.0"
description = "Residential comp-analysis MCP server for KV Capital (Calgary)"
requires-python = ">=3.11"
dependencies = [
    "fastmcp>=3.0",
    "pydantic>=2.6",
    "httpx>=0.27",
]

[project.optional-dependencies]
browser = ["playwright>=1.44"]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]

[project.scripts]
kv-comp-analysis = "mcp_server.server:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
*.egg-info/
.env
```

- [ ] **Step 3: Create empty package files**

Create `mcp_server/__init__.py`, `mcp_server/compsource/__init__.py`, `tests/__init__.py` as empty files.

- [ ] **Step 4: Init git, install, verify**

Run:
```bash
cd /home/allen/Documents/KV_hackathon
git init
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```
Expected: pip installs cleanly; `pytest` reports "no tests ran" (exit 5) — acceptable at this stage.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore mcp_server/ tests/ docs/
git commit -m "chore: scaffold kv-comp-analysis project + commit design spec"
```

---

## Task 2: Data models

**Files:**
- Create: `mcp_server/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from datetime import date
from mcp_server.models import Subject, Comp, Criteria, AdjustmentRules


def test_subject_defaults_and_provenance():
    s = Subject(address="123 Maple Dr, Calgary, AB")
    assert s.community is None
    assert s.provenance == {}


def test_comp_computes_price_per_sqft():
    c = Comp(address="1 A St", lat=51.0, lng=-114.0, sold_price=800_000,
             sold_date=date(2026, 1, 16), sqft=2000)
    assert c.price_per_sqft == 400.0


def test_criteria_defaults_match_sams_rules():
    c = Criteria()
    assert (c.radius_km, c.size_pct, c.lookback_months, c.age_years, c.min_comps) \
        == (3.0, 0.20, 12, 10, 4)


def test_adjustment_rules_defaults():
    r = AdjustmentRules()
    assert r.age_rate == 0.005
    assert r.size_elast == 0.20
    assert r.trend_clamp == 0.02
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.models'`

- [ ] **Step 3: Write minimal implementation**

```python
# mcp_server/models.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/models.py tests/test_models.py
git commit -m "feat: add pydantic models for comp analysis"
```

---

## Task 3: Geo distance

**Files:**
- Create: `mcp_server/geo.py`
- Test: `tests/test_geo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_geo.py
from mcp_server.geo import haversine_km


def test_haversine_zero_distance():
    assert haversine_km(51.05, -114.07, 51.05, -114.07) == 0.0


def test_haversine_known_distance():
    # Calgary downtown to Calgary airport ~ 12-15 km
    d = haversine_km(51.045, -114.057, 51.131, -114.010)
    assert 9 < d < 16
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_geo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.geo'`

- [ ] **Step 3: Write minimal implementation**

```python
# mcp_server/geo.py
from __future__ import annotations
import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a)), 3)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_geo.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/geo.py tests/test_geo.py
git commit -m "feat: add haversine distance helper"
```

---

## Task 4: Comp filtering + ranking (Sam's 5)

**Files:**
- Create: `mcp_server/comps.py`
- Test: `tests/test_comps.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_comps.py
from datetime import date
from mcp_server.models import Subject, Comp, Criteria
from mcp_server.comps import months_between, filter_and_rank

AS_OF = date(2026, 6, 1)


def _subject():
    return Subject(address="S", lat=51.05, lng=-114.08, sqft=2000,
                   year_built=1985, property_type="detached", beds=3)


def _comp(addr, lat, lng, price, d, sqft, yb=1985, ptype="detached", beds=3):
    return Comp(address=addr, lat=lat, lng=lng, sold_price=price, sold_date=d,
                sqft=sqft, year_built=yb, property_type=ptype, beds=beds)


def test_months_between():
    assert months_between(date(2026, 1, 1), AS_OF) == 5
    assert months_between(date(2025, 6, 1), AS_OF) == 12


def test_filter_keeps_qualifying_comp():
    s = _subject()
    good = _comp("good", 51.051, -114.081, 820_000, date(2026, 3, 1), 2050)
    kept, flags = filter_and_rank(s, [good], Criteria(), as_of=AS_OF)
    assert [c.address for c in kept] == ["good"]
    assert kept[0].distance_km is not None and kept[0].include_reason


def test_filter_drops_on_each_rule():
    s = _subject()
    too_far = _comp("far", 52.0, -114.0, 800_000, date(2026, 3, 1), 2000)
    too_big = _comp("big", 51.051, -114.081, 800_000, date(2026, 3, 1), 2600)  # +30%
    too_old_sale = _comp("stale", 51.051, -114.081, 800_000, date(2024, 1, 1), 2000)
    too_old_age = _comp("aged", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, yb=1960)
    kept, _ = filter_and_rank(s, [too_far, too_big, too_old_sale, too_old_age],
                              Criteria(), as_of=AS_OF)
    assert kept == []


def test_secondary_filters_type_and_beds():
    s = _subject()
    wrong_type = _comp("condo", 51.051, -114.081, 800_000, date(2026, 3, 1), 2000, ptype="condo")
    kept, _ = filter_and_rank(s, [wrong_type], Criteria(match_type=True), as_of=AS_OF)
    assert kept == []


def test_ranking_orders_most_similar_first():
    s = _subject()
    near = _comp("near", 51.051, -114.081, 800_000, date(2026, 5, 1), 2010)
    farish = _comp("farish", 51.07, -114.05, 800_000, date(2026, 1, 1), 2300)
    kept, _ = filter_and_rank(s, [farish, near], Criteria(), as_of=AS_OF)
    assert [c.address for c in kept] == ["near", "farish"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_comps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.comps'`

- [ ] **Step 3: Write minimal implementation**

```python
# mcp_server/comps.py
from __future__ import annotations
from datetime import date
from mcp_server.models import Subject, Comp, Criteria
from mcp_server.geo import haversine_km


def months_between(earlier: date, as_of: date) -> int:
    """Whole months from `earlier` to `as_of` (negative if earlier is in the future)."""
    return (as_of.year - earlier.year) * 12 + (as_of.month - earlier.month)


def _similarity_score(subject: Subject, c: Comp, as_of: date) -> float:
    """Lower = more similar. Composite over distance, size, age, recency."""
    dist = c.distance_km if c.distance_km is not None else 0.0
    size_diff = abs(c.sqft - subject.sqft) / subject.sqft
    age_diff = abs((c.year_built or subject.year_built) - subject.year_built)
    months = max(months_between(c.sold_date, as_of), 0)
    return dist / 10 + size_diff + age_diff / 20 + months / 24


def filter_and_rank(
    subject: Subject, candidates: list[Comp], criteria: Criteria, *, as_of: date
) -> tuple[list[Comp], list[str]]:
    """Apply Sam's 5 (+secondary) filters, annotate, and rank by similarity."""
    flags: list[str] = []
    kept: list[Comp] = []
    for c in candidates:
        dist = haversine_km(subject.lat, subject.lng, c.lat, c.lng)
        if dist > criteria.radius_km:
            continue
        size_diff = abs(c.sqft - subject.sqft) / subject.sqft
        if size_diff > criteria.size_pct:
            continue
        months = months_between(c.sold_date, as_of)
        if months < 0 or months > criteria.lookback_months:
            continue
        age_diff = None
        if subject.year_built and c.year_built:
            age_diff = abs(c.year_built - subject.year_built)
            if age_diff > criteria.age_years:
                continue
        if criteria.match_type and c.property_type != subject.property_type:
            continue
        if criteria.match_beds and c.beds != subject.beds:
            continue
        c.distance_km = dist
        c.include_reason = (
            f"{dist:.1f} km, {size_diff * 100:+.0f}% size, {months} mo ago"
            + (f", Δage {age_diff} yr" if age_diff is not None else "")
        )
        kept.append(c)
    kept.sort(key=lambda c: _similarity_score(subject, c, as_of))
    return kept, flags
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_comps.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/comps.py tests/test_comps.py
git commit -m "feat: add Sam's-5 comp filtering and ranking"
```

---

## Task 5: Widening ladder

**Files:**
- Modify: `mcp_server/comps.py`
- Test: `tests/test_comps.py` (add cases)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_comps.py
from mcp_server.comps import find_with_ladder
from mcp_server.models import FindCompsResult


def test_ladder_not_triggered_when_enough():
    s = _subject()
    cands = [_comp(f"c{i}", 51.051, -114.081, 800_000 + i, date(2026, 3, 1), 2000 + i)
             for i in range(4)]
    res = find_with_ladder(s, cands, Criteria(min_comps=4), as_of=AS_OF)
    assert isinstance(res, FindCompsResult)
    assert len(res.comps) == 4
    assert res.relaxations == []


def test_ladder_relaxes_time_first_then_records():
    s = _subject()
    # all sold 15 months ago -> excluded at 12mo, included once lookback relaxes to 18
    cands = [_comp(f"c{i}", 51.051, -114.081, 800_000, date(2025, 3, 1), 2000 + i)
             for i in range(4)]
    res = find_with_ladder(s, cands, Criteria(min_comps=4), as_of=AS_OF)
    assert len(res.comps) == 4
    assert res.relaxations[0].step == "lookback_months"
    assert res.relaxations[0].to == 18
    assert any("relaxed" in f.lower() for f in res.flags)


def test_ladder_exhausts_and_returns_what_it_found():
    s = _subject()
    res = find_with_ladder(s, [], Criteria(min_comps=4), as_of=AS_OF)
    assert res.comps == []
    assert any("insufficient" in f.lower() for f in res.flags)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_comps.py -v`
Expected: FAIL with `ImportError: cannot import name 'find_with_ladder'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to mcp_server/comps.py
from mcp_server.models import Relaxation, FindCompsResult

# Ordered widening ladder: (dimension, new_value). Applied cumulatively.
LADDER: list[tuple[str, float]] = [
    ("lookback_months", 18), ("lookback_months", 24),
    ("radius_km", 5.0), ("radius_km", 8.0),
    ("size_pct", 0.30), ("size_pct", 0.40),
    ("age_years", 20), ("age_years", 30),
]


def find_with_ladder(
    subject: Subject, candidates: list[Comp], criteria: Criteria, *, as_of: date
) -> FindCompsResult:
    """Filter with Sam's 5; if under min_comps, relax one ladder step at a time."""
    current = criteria.model_copy()
    relaxations: list[Relaxation] = []
    flags: list[str] = []

    kept, _ = filter_and_rank(subject, candidates, current, as_of=as_of)
    ladder = iter(LADDER)
    while len(kept) < criteria.min_comps:
        step = next(ladder, None)
        if step is None:
            flags.append(
                f"Insufficient comps: found {len(kept)} of {criteria.min_comps} "
                "after exhausting the widening ladder."
            )
            break
        dim, new_val = step
        old_val = getattr(current, dim)
        if new_val <= old_val:
            continue
        setattr(current, dim, new_val)
        relaxations.append(Relaxation(step=dim, **{"from": old_val, "to": new_val}))
        flags.append(f"Relaxed {dim}: {old_val} -> {new_val}")
        kept, _ = filter_and_rank(subject, candidates, current, as_of=as_of)

    return FindCompsResult(
        comps=kept,
        candidates_considered=len(candidates),
        relaxations=relaxations,
        flags=flags,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_comps.py -v`
Expected: PASS (8 tests total)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/comps.py tests/test_comps.py
git commit -m "feat: add deterministic widening ladder"
```

---

## Task 6: Adjustment grid — trend + per-comp adjustment

**Files:**
- Create: `mcp_server/estimate.py`
- Test: `tests/test_estimate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_estimate.py
from datetime import date
from mcp_server.models import Subject, Comp, AdjustmentRules
from mcp_server.estimate import estimate_trend, adjust_comp

AS_OF = date(2026, 6, 1)


def _subject(sqft=2000, yb=1985):
    return Subject(address="S", lat=51.05, lng=-114.08, sqft=sqft, year_built=yb)


def _comp(price, sqft, yb, d=date(2026, 5, 1)):
    return Comp(address="c", lat=51.05, lng=-114.08, sold_price=price,
                sold_date=d, sqft=sqft, year_built=yb)


def test_trend_zero_with_few_comps():
    assert estimate_trend([_comp(800_000, 2000, 1985)], AdjustmentRules(), as_of=AS_OF) == 0.0


def test_trend_is_clamped():
    comps = [_comp(700_000, 2000, 1985, date(2025, 6, 1)),
             _comp(750_000, 2000, 1985, date(2025, 9, 1)),
             _comp(820_000, 2000, 1985, date(2026, 1, 1)),
             _comp(900_000, 2000, 1985, date(2026, 5, 1))]
    t = estimate_trend(comps, AdjustmentRules(), as_of=AS_OF)
    assert -0.02 <= t <= 0.02


def test_adjust_comp_age_premium_for_newer_subject():
    s = _subject(sqft=2000, yb=1990)
    c = _comp(800_000, 2000, yb=1980)  # comp 10 yrs older -> subject newer -> upward age adj
    ca = adjust_comp(s, c, AdjustmentRules(), trend=0.0, as_of=AS_OF)
    age_adj = next(a for a in ca.adjustments if a.factor == "age")
    assert age_adj.pct > 0
    assert ca.adjusted_ppsf > ca.raw_ppsf


def test_adjust_comp_size_larger_comp_adjusts_up():
    s = _subject(sqft=2000, yb=1985)
    c = _comp(880_000, 2200, yb=1985)  # comp 10% larger -> lower $/sqft -> adjust up
    ca = adjust_comp(s, c, AdjustmentRules(), trend=0.0, as_of=AS_OF)
    size_adj = next(a for a in ca.adjustments if a.factor == "size")
    assert size_adj.pct > 0


def test_adjusted_price_uses_subject_sqft():
    s = _subject(sqft=2000, yb=1985)
    c = _comp(800_000, 2000, yb=1985)
    ca = adjust_comp(s, c, AdjustmentRules(), trend=0.0, as_of=AS_OF)
    assert ca.adjusted_price == round(ca.adjusted_ppsf * s.sqft, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_estimate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.estimate'`

- [ ] **Step 3: Write minimal implementation**

```python
# mcp_server/estimate.py
from __future__ import annotations
from datetime import date
from statistics import mean
from mcp_server.models import (
    Subject, Comp, AdjustmentRules, Adjustment, CompAdjustment,
)
from mcp_server.comps import months_between


def estimate_trend(comps: list[Comp], rules: AdjustmentRules, *, as_of: date) -> float:
    """Monthly $/sqft trend via least-squares slope of ppsf vs months-old.
    Returns 0.0 if < 4 comps; clamped to ±rules.trend_clamp."""
    if len(comps) < 4:
        return 0.0
    xs = [-months_between(c.sold_date, as_of) for c in comps]  # more recent = larger x
    ys = [c.price_per_sqft for c in comps]
    mx, my = mean(xs), mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    monthly = slope / my if my else 0.0  # fractional change per month
    return round(max(-rules.trend_clamp, min(rules.trend_clamp, monthly)), 5)


def adjust_comp(
    subject: Subject, comp: Comp, rules: AdjustmentRules, *, trend: float, as_of: date
) -> CompAdjustment:
    """Adjust one comp's $/sqft to subject-equivalent via time/age/size line items.
    Pure: `as_of` is passed in so there is no hidden global state."""
    raw_ppsf = comp.price_per_sqft
    months_old = max(months_between(comp.sold_date, as_of), 0)
    adjustments: list[Adjustment] = []

    # Time: bring the sale to "today" using the market trend.
    time_pct = trend * months_old
    adjustments.append(Adjustment(
        factor="time", pct=round(time_pct, 5),
        rationale=f"{months_old} mo old @ {trend*100:.2f}%/mo market trend"))

    # Age: newer subject than comp -> upward; rate per year of difference.
    age_pct = (rules.age_rate * (subject.year_built - comp.year_built)
               if (subject.year_built and comp.year_built) else 0.0)
    adjustments.append(Adjustment(
        factor="age", pct=round(age_pct, 5),
        rationale=f"age diff {(subject.year_built or 0) - (comp.year_built or 0)} yr"))

    # Size: larger comp has lower $/sqft -> adjust toward (smaller) subject.
    size_gap = (comp.sqft - subject.sqft) / subject.sqft
    size_pct = rules.size_elast * size_gap
    adjustments.append(Adjustment(
        factor="size", pct=round(size_pct, 5),
        rationale=f"size gap {size_gap*100:+.0f}%"))

    multiplier = 1.0
    for a in adjustments:
        multiplier *= (1 + a.pct)
    adjusted_ppsf = round(raw_ppsf * multiplier, 2)
    return CompAdjustment(
        address=comp.address,
        raw_price=comp.sold_price,
        raw_ppsf=raw_ppsf,
        adjustments=adjustments,
        adjusted_ppsf=adjusted_ppsf,
        adjusted_price=round(adjusted_ppsf * subject.sqft, 0),
        weight=0.0,  # filled in during reconciliation
    )
```

> The test calls in Step 1 already pass `as_of=AS_OF` to `adjust_comp(...)`, matching this signature.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_estimate.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/estimate.py tests/test_estimate.py
git commit -m "feat: add market-trend estimation and per-comp adjustment grid"
```

---

## Task 7: Reconciliation, outliers, weighting, confidence

**Files:**
- Modify: `mcp_server/estimate.py`
- Test: `tests/test_estimate.py` (add cases)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_estimate.py
from mcp_server.estimate import remove_outliers, comp_weight, reconcile
from mcp_server.models import Estimate


def test_remove_outliers_drops_extreme():
    vals = [400, 410, 420, 430, 1000]
    kept_idx = remove_outliers(vals, iqr_mult=1.5)
    assert 4 not in kept_idx and set(kept_idx) == {0, 1, 2, 3}


def test_comp_weight_closer_comp_weighs_more():
    s = _subject()
    near = _comp(800_000, 2000, 1985); near.distance_km = 0.5
    far = _comp(800_000, 2000, 1985); far.distance_km = 2.5
    wn = comp_weight(s, near, AdjustmentRules(), as_of=AS_OF)
    wf = comp_weight(s, far, AdjustmentRules(), as_of=AS_OF)
    assert wn > wf


def test_reconcile_produces_estimate_with_range_and_confidence():
    s = _subject(sqft=2000, yb=1985)
    comps = [_comp(800_000, 2000, 1985), _comp(810_000, 2010, 1986),
             _comp(795_000, 1990, 1984), _comp(805_000, 2005, 1985),
             _comp(800_000, 2000, 1985), _comp(812_000, 2015, 1987)]
    for c in comps:
        c.distance_km = 0.6
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF, ladder_depth=0)
    assert isinstance(est, Estimate)
    assert est.low <= est.point <= est.high
    assert est.confidence == "high"      # >=6 comps, tight dispersion, no widening
    assert len(est.per_comp) >= 4


def test_reconcile_low_confidence_when_sparse():
    s = _subject()
    comps = [_comp(800_000, 2000, 1985), _comp(900_000, 2000, 1985)]
    for c in comps:
        c.distance_km = 0.6
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF, ladder_depth=0)
    assert est.confidence == "low"       # < 4 comps
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_estimate.py -v`
Expected: FAIL with `ImportError: cannot import name 'reconcile'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to mcp_server/estimate.py
from statistics import median, pstdev, quantiles
from mcp_server.models import Estimate, Confidence


def remove_outliers(values: list[float], *, iqr_mult: float = 1.5) -> list[int]:
    """Return indices of values within median ± iqr_mult*IQR. No-op if < 4 values."""
    if len(values) < 4:
        return list(range(len(values)))
    q1, _, q3 = quantiles(values, n=4)
    iqr = q3 - q1
    lo, hi = median(values) - iqr_mult * iqr, median(values) + iqr_mult * iqr
    return [i for i, v in enumerate(values) if lo <= v <= hi]


def comp_weight(subject: Subject, comp: Comp, rules: AdjustmentRules, *, as_of: date) -> float:
    dist = comp.distance_km if comp.distance_km is not None else 0.0
    size_pct = abs(comp.sqft - subject.sqft) / subject.sqft
    age_diff = abs((comp.year_built or subject.year_built) - subject.year_built)
    months = max(months_between(comp.sold_date, as_of), 0)
    denom = (1 + rules.weight_a * dist + rules.weight_b * size_pct
             + rules.weight_c * age_diff + rules.weight_d * months)
    return round(1 / denom, 4)


def _confidence(n: int, cov: float, ladder_depth: int) -> Confidence:
    if n < 4 or cov > 0.20 or ladder_depth >= 3:
        return "low"
    if n >= 6 and cov <= 0.10 and ladder_depth == 0:
        return "high"
    return "medium"


def reconcile(
    subject: Subject, comps: list[Comp], rules: AdjustmentRules, *,
    as_of: date, ladder_depth: int = 0,
) -> Estimate:
    notes: list[str] = []
    trend = estimate_trend(comps, rules, as_of=as_of)
    notes.append(f"Market trend applied: {trend*100:.2f}%/mo")
    adjusted = [adjust_comp(subject, c, rules, trend=trend, as_of=as_of) for c in comps]

    kept_idx = remove_outliers([ca.adjusted_ppsf for ca in adjusted],
                               iqr_mult=rules.outlier_iqr)
    if len(kept_idx) < len(adjusted):
        notes.append(f"Dropped {len(adjusted) - len(kept_idx)} outlier comp(s)")
    kept = [adjusted[i] for i in kept_idx]
    kept_comps = [comps[i] for i in kept_idx]

    for ca, c in zip(kept, kept_comps):
        ca.weight = comp_weight(subject, c, rules, as_of=as_of)

    wsum = sum(ca.weight for ca in kept) or 1.0
    reconciled_ppsf = sum(ca.adjusted_ppsf * ca.weight for ca in kept) / wsum
    point = round(reconciled_ppsf * subject.sqft, 0)

    ppsf_vals = sorted(ca.adjusted_ppsf for ca in kept)
    if len(ppsf_vals) >= 4:
        q1, _, q3 = quantiles(ppsf_vals, n=4)
    else:
        q1, q3 = ppsf_vals[0], ppsf_vals[-1]
    low, high = round(q1 * subject.sqft, 0), round(q3 * subject.sqft, 0)

    m = mean(ppsf_vals)
    cov = (pstdev(ppsf_vals) / m) if (len(ppsf_vals) > 1 and m) else 0.0
    conf = _confidence(len(kept), cov, ladder_depth)
    notes.append(f"{len(kept)} comps, $/sqft CoV {cov:.2f}, ladder depth {ladder_depth}")

    return Estimate(point=point, low=low, high=high, confidence=conf,
                    per_comp=kept, method_notes=notes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_estimate.py -v`
Expected: PASS (9 tests total)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/estimate.py tests/test_estimate.py
git commit -m "feat: add reconciliation, outlier removal, weighting, confidence"
```

---

## Task 8: CompSource interface + synthetic source

**Files:**
- Create: `mcp_server/compsource/base.py`
- Create: `mcp_server/compsource/synthetic.py`
- Test: `tests/test_synthetic.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synthetic.py
from datetime import date
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.synthetic import SyntheticCompSource


def test_synthetic_is_a_compsource():
    assert issubclass(SyntheticCompSource, CompSource)


def test_get_property_returns_record_with_attrs():
    src = SyntheticCompSource(seed=42)
    rec = src.get_property("123 Maple Dr, Roxboro, Calgary, AB")
    assert isinstance(rec, PropertyRecord)
    assert rec.community and rec.lat and rec.sqft and rec.year_built


def test_get_property_is_deterministic():
    a = SyntheticCompSource(seed=42).get_property("123 Maple Dr")
    b = SyntheticCompSource(seed=42).get_property("123 Maple Dr")
    assert a.model_dump() == b.model_dump()


def test_recent_sales_returns_comps_in_community():
    src = SyntheticCompSource(seed=42)
    comps = src.recent_sales("Roxboro", lookback_months=12, as_of=date(2026, 6, 1))
    assert len(comps) >= 8
    assert all(c.sold_price > 0 and c.sqft > 0 for c in comps)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_synthetic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.compsource.base'`

- [ ] **Step 3: Write the base interface**

```python
# mcp_server/compsource/base.py
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
```

- [ ] **Step 4: Write the synthetic source**

```python
# mcp_server/compsource/synthetic.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_synthetic.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add mcp_server/compsource/ tests/test_synthetic.py
git commit -m "feat: add CompSource interface and synthetic source"
```

---

## Task 9: HonestDoor source (GraphQL + fallback)

**Files:**
- Create: `mcp_server/compsource/honestdoor.py`
- Test: `tests/test_honestdoor.py`

**Context:** HonestDoor data loads from `https://core-backend.honestdoor.com/v2/graphql`. Cloudflare Turnstile may block direct calls — **de-risk this first** by running the spike in Step 1. Tests mock the transport so they never hit the network. If the live spike is blocked, the synthetic source remains the demo default and this adapter is documented as best-effort.

- [ ] **Step 1: De-risk spike (manual, not a test)**

Run:
```bash
curl -s -X POST "https://core-backend.honestdoor.com/v2/graphql" \
  -H "Content-Type: application/json" \
  -A "Mozilla/5.0" \
  -d '{"query":"{ __typename }"}' | head -c 400
```
Expected: a JSON response (`{"data":{"__typename":"Query"}}`) means GraphQL is reachable → proceed with the GraphQL adapter. A Cloudflare/Turnstile HTML challenge means direct access is blocked → mark the GraphQL path best-effort and rely on the Playwright fallback / synthetic source. Record the result in the adapter docstring.

- [ ] **Step 2: Write the failing test (mocked transport)**

```python
# tests/test_honestdoor.py
from datetime import date
import httpx
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.honestdoor import HonestDoorCompSource, parse_property, parse_sales


def test_honestdoor_is_a_compsource():
    assert issubclass(HonestDoorCompSource, CompSource)


def test_parse_property_maps_fields():
    raw = {"community": "Roxboro", "latitude": 51.025, "longitude": -114.073,
           "squareFootage": 1200, "yearBuilt": 1930, "bedrooms": 2, "bathrooms": 3,
           "lotSize": 5998, "avmValue": 957400, "assessedValue": 900000}
    rec = parse_property("1431 6 St NW", raw)
    assert isinstance(rec, PropertyRecord)
    assert rec.community == "Roxboro" and rec.sqft == 1200 and rec.year_built == 1930
    assert rec.hd_estimate == 957400


def test_parse_sales_filters_to_real_sales_only():
    rows = [
        {"address": "3028 1 St SW", "soldPrice": 1801000, "soldDate": "2026-01-16",
         "squareFootage": 2532, "latitude": 51.02, "longitude": -114.08,
         "bedrooms": 3, "bathrooms": 3, "yearBuilt": 1982},
        {"address": "no-price", "soldPrice": None, "soldDate": None,
         "squareFootage": 2000, "latitude": 51.02, "longitude": -114.08},
    ]
    comps = parse_sales(rows)
    assert [c.address for c in comps] == ["3028 1 St SW"]
    assert comps[0].sold_date == date(2026, 1, 16)


def test_recent_sales_uses_injected_client(monkeypatch):
    payload = {"data": {"recentlySold": [
        {"address": "3028 1 St SW", "soldPrice": 1801000, "soldDate": "2026-01-16",
         "squareFootage": 2532, "latitude": 51.02, "longitude": -114.08,
         "bedrooms": 3, "bathrooms": 3, "yearBuilt": 1982}]}}

    def handler(request):
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    src = HonestDoorCompSource(client=client)
    comps = src.recent_sales("Roxboro", lookback_months=12, as_of=date(2026, 6, 1))
    assert len(comps) == 1 and comps[0].sold_price == 1801000
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_honestdoor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.compsource.honestdoor'`

- [ ] **Step 4: Write minimal implementation**

```python
# mcp_server/compsource/honestdoor.py
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
    """Live HonestDoor public data via GraphQL. Inject `client` for tests."""

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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_honestdoor.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add mcp_server/compsource/honestdoor.py tests/test_honestdoor.py
git commit -m "feat: add HonestDoor GraphQL CompSource (mock-tested)"
```

---

## Task 10: FastMCP server — the 4 tools

**Files:**
- Create: `mcp_server/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server.py
from datetime import date
from mcp_server.server import build_tools
from mcp_server.compsource.synthetic import SyntheticCompSource
from mcp_server.models import Subject, Estimate, CrossCheck, FindCompsResult

TOOLS = build_tools(source=SyntheticCompSource(seed=42), as_of=date(2026, 6, 1))


def test_get_subject_fills_attrs_and_provenance():
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides={"sqft": 2100})
    assert isinstance(s, Subject)
    assert s.sqft == 2100 and s.provenance["sqft"] == "user"
    assert s.community and s.provenance["community"] == "honestdoor"


def test_get_subject_marks_missing():
    s = TOOLS.get_subject("Unknown Rd")
    assert "year_built" in s.provenance


def test_find_comps_returns_filtered_result():
    s = TOOLS.get_subject("123 Maple Dr, Calgary")
    res = TOOLS.find_comps(s)
    assert isinstance(res, FindCompsResult)
    assert all(c.distance_km is not None for c in res.comps)


def test_estimate_value_runs_on_found_comps():
    s = TOOLS.get_subject("123 Maple Dr, Calgary")
    res = TOOLS.find_comps(s)
    est = TOOLS.estimate_value(s, res.comps, ladder_depth=len(res.relaxations))
    assert isinstance(est, Estimate)
    assert est.low <= est.point <= est.high


def test_cross_check_compares_to_avm():
    s = TOOLS.get_subject("123 Maple Dr, Calgary")
    est = TOOLS.estimate_value(s, TOOLS.find_comps(s).comps)
    cc = TOOLS.cross_check(s, est.point)
    assert isinstance(cc, CrossCheck)
    assert cc.verdict
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.server'`

- [ ] **Step 3: Write minimal implementation**

```python
# mcp_server/server.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Optional
from mcp_server.models import (
    Subject, FindCompsResult, Estimate, CrossCheck, Criteria, AdjustmentRules,
)
from mcp_server.compsource.base import CompSource
from mcp_server.compsource.synthetic import SyntheticCompSource
from mcp_server.comps import find_with_ladder
from mcp_server.estimate import reconcile

_SUBJECT_FIELDS = ["community", "lat", "lng", "sqft", "year_built",
                   "beds", "baths", "lot_sf", "property_type"]


@dataclass
class Tools:
    """Plain callables holding the business logic — wrapped by FastMCP below
    and reused directly in tests (no transport needed)."""
    source: CompSource
    as_of: date

    def get_subject(self, address: str, overrides: Optional[dict] = None) -> Subject:
        overrides = overrides or {}
        rec = self.source.get_property(address)
        data = {"address": address}
        provenance: dict[str, str] = {}
        for f in _SUBJECT_FIELDS:
            if f in overrides and overrides[f] is not None:
                data[f] = overrides[f]; provenance[f] = "user"
            elif getattr(rec, f, None) is not None:
                data[f] = getattr(rec, f); provenance[f] = "honestdoor"
            else:
                provenance[f] = "missing"
        data["hd_estimate"] = rec.hd_estimate
        data["provenance"] = provenance
        return Subject(**data)

    def find_comps(self, subject: Subject, criteria: Optional[Criteria] = None) -> FindCompsResult:
        criteria = criteria or Criteria()
        candidates = self.source.recent_sales(
            subject.community, lookback_months=criteria.lookback_months, as_of=self.as_of)
        return find_with_ladder(subject, candidates, criteria, as_of=self.as_of)

    def estimate_value(self, subject: Subject, comps: list, *,
                       rules: Optional[AdjustmentRules] = None, ladder_depth: int = 0) -> Estimate:
        return reconcile(subject, comps, rules or AdjustmentRules(),
                         as_of=self.as_of, ladder_depth=ladder_depth)

    def cross_check(self, subject: Subject, estimate_point: float) -> CrossCheck:
        rec = self.source.get_property(subject.address)
        notes, vs_avm, vs_assess = [], None, None
        if rec.hd_estimate:
            vs_avm = round((estimate_point - rec.hd_estimate) / rec.hd_estimate * 100, 1)
            notes.append(f"{vs_avm:+.1f}% vs HonestDoor AVM (estimate, not a sale)")
        if rec.assessed_value:
            vs_assess = round((estimate_point - rec.assessed_value) / rec.assessed_value * 100, 1)
            notes.append(f"{vs_assess:+.1f}% vs municipal assessment")
        worst = max((abs(v) for v in (vs_avm, vs_assess) if v is not None), default=0.0)
        verdict = "consistent" if worst <= 10 else ("review" if worst <= 20 else "divergent")
        return CrossCheck(hd_avm=rec.hd_estimate, assessed_value=rec.assessed_value,
                          vs_avm_pct=vs_avm, vs_assessment_pct=vs_assess,
                          verdict=verdict, notes=notes)


def build_tools(source: Optional[CompSource] = None, as_of: Optional[date] = None) -> Tools:
    return Tools(source=source or SyntheticCompSource(), as_of=as_of or date.today())


def main() -> None:
    """Console entry point: register the tools with FastMCP over stdio."""
    from fastmcp import FastMCP
    tools = build_tools(source=SyntheticCompSource())  # swap to HonestDoorCompSource() when live
    mcp = FastMCP("kv-comp-analysis")

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True})
    def get_subject(address: str, overrides: Optional[dict] = None) -> dict:
        """Resolve a residential subject property: auto-fill attributes from the data
        source and mark each field's provenance (user|honestdoor|missing). If you only
        have an address, call this first. Returns subject attributes, not a valuation."""
        return tools.get_subject(address, overrides).model_dump()

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True})
    def find_comps(subject: dict, criteria: Optional[dict] = None) -> dict:
        """Find comparable recent sales near a subject and filter/rank by KV's house
        rules (radius, size, recency, age; ranked by similarity). Applies a widening
        ladder if too few. Takes the subject object from get_subject."""
        crit = Criteria(**criteria) if criteria else Criteria()
        return tools.find_comps(Subject(**subject), crit).model_dump()

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False})
    def estimate_value(subject: dict, comps: list, rules: Optional[dict] = None,
                       ladder_depth: int = 0) -> dict:
        """Estimate the subject's value from comps via the adjustment grid + weighted
        reconciliation. Pure computation, no network. Takes comps from find_comps."""
        r = AdjustmentRules(**rules) if rules else AdjustmentRules()
        from mcp_server.models import Comp
        cs = [Comp(**c) for c in comps]
        return tools.estimate_value(Subject(**subject), cs, rules=r, ladder_depth=ladder_depth).model_dump()

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True})
    def cross_check(subject: dict, estimate_point: float) -> dict:
        """Sanity-check an estimate against the HonestDoor AVM and municipal assessment.
        Returns deltas and a verdict (consistent|review|divergent)."""
        return tools.cross_check(Subject(**subject), estimate_point).model_dump()

    mcp.run()  # stdio transport — local, no hosting


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_server.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/server.py tests/test_server.py
git commit -m "feat: wire the 4 MCP tools over a CompSource"
```

---

## Task 11: Hold-one-out eval harness

**Files:**
- Create: `eval/__init__.py` (empty)
- Create: `eval/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest.py
from datetime import date
from mcp_server.compsource.synthetic import SyntheticCompSource
from eval.backtest import hold_one_out


def test_hold_one_out_reports_median_error():
    result = hold_one_out(SyntheticCompSource(seed=7), community="Roxboro",
                          as_of=date(2026, 6, 1))
    assert result.n >= 4
    assert 0 <= result.median_abs_pct_error < 60
    assert len(result.per_property) == result.n
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.backtest'`

- [ ] **Step 3: Write minimal implementation**

```python
# eval/backtest.py
from __future__ import annotations
from datetime import date
from statistics import median
from pydantic import BaseModel
from mcp_server.models import Subject, Criteria, AdjustmentRules
from mcp_server.compsource.base import CompSource
from mcp_server.comps import find_with_ladder
from mcp_server.estimate import reconcile


class PropertyError(BaseModel):
    address: str
    actual: float
    predicted: float
    abs_pct_error: float


class BacktestResult(BaseModel):
    n: int
    median_abs_pct_error: float
    per_property: list[PropertyError]


def hold_one_out(source: CompSource, *, community: str, as_of: date) -> BacktestResult:
    """For each real sale, hide it, predict from the others, compare to actual."""
    sales = source.recent_sales(community, lookback_months=12, as_of=as_of)
    rows: list[PropertyError] = []
    for i, target in enumerate(sales):
        others = [c for j, c in enumerate(sales) if j != i]
        subject = Subject(address=target.address, community=community,
                          lat=target.lat, lng=target.lng, sqft=target.sqft,
                          year_built=target.year_built, property_type=target.property_type)
        found = find_with_ladder(subject, others, Criteria(), as_of=as_of)
        if len(found.comps) < Criteria().min_comps:
            continue
        est = reconcile(subject, found.comps, AdjustmentRules(),
                        as_of=as_of, ladder_depth=len(found.relaxations))
        err = abs(est.point - target.sold_price) / target.sold_price * 100
        rows.append(PropertyError(address=target.address, actual=target.sold_price,
                                  predicted=est.point, abs_pct_error=round(err, 1)))
    med = round(median([r.abs_pct_error for r in rows]), 1) if rows else 0.0
    return BacktestResult(n=len(rows), median_abs_pct_error=med, per_property=rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add eval/ tests/test_backtest.py
git commit -m "feat: add hold-one-out backtest harness"
```

---

## Task 12: Skill — SKILL.md

**Files:**
- Create: `skill/comp-analysis/SKILL.md`

- [ ] **Step 1: Write `SKILL.md`**

```markdown
---
name: comp-analysis
description: Use when valuing a residential property via comparable recent sales
  (comp analysis / CMA) in the Alberta market (Calgary). Finds comps per KV's house
  rules, adjusts them, and produces a transparent underwriter-style value estimate.
  Orchestrates the kv-comp-analysis MCP tools.
---

# Comp Analysis — Senior Underwriter Methodology

You are a senior residential underwriter walking a colleague through your file:
transparent about which comps you chose, why, and what you adjusted. You assist at
every level — a novice gets a defensible estimate out of the box; an expert gets higher
accuracy by supplying better inputs, overriding criteria, or teaching you new methods.
You surface judgment; you never hide it behind a number.

## Workflow

1. **`get_subject(address, overrides)`** — resolve the subject. Pass any attributes the
   user gave you as `overrides`. Inspect `provenance`: anything marked `missing` that is
   essential (sqft, year_built, location, property_type) — **ask the user** rather than
   guess. New builds often aren't in any dataset; the user is the source of truth.
2. **`find_comps(subject, criteria)`** — defaults are KV's house rules (3 km, ±20% size,
   12 mo, ±10 yr). Review `comps`, `relaxations`, and `flags`.
3. **Curate** — if a comp's `$/sqft` is a clear outlier or it looks non-arm's-length, say
   so and exclude it before estimating. Prefer closer, more recent, more similar comps.
4. **`estimate_value(subject, comps, ladder_depth)`** — pass `ladder_depth = len(relaxations)`.
5. **`cross_check(subject, estimate.point)`** — compare to the HonestDoor AVM and the
   municipal assessment. Material divergence → investigate and explain; don't trust blindly.
6. **Present the file** (format below).

## Judgment rules

- **Widening ladder:** `find_comps` relaxes one step at a time in KV's order
  (time → radius → size → age) when comps are sparse. Report each relaxation as a caveat,
  and if it had to widen far, lower your confidence and say why. If still insufficient,
  state that plainly — do not manufacture comps.
- **Confidence:** trust the rubric returned by `estimate_value` (high/medium/low from comp
  count, $/sqft dispersion, ladder depth). Explain what drove it.
- **Honesty:** the HonestDoor headline price is an AVM **estimate, not a sale** — only the
  Sold History is a real transaction. Never present an AVM as a comp. Flag attributes tagged
  "Estimate". State data limits (≈180-day window; per-community search).

## Output — "the file"

1. **Subject** — address + key attributes, noting which were user-provided vs looked up.
2. **Comps** — a table: address, sold price, sold date, sqft, $/sqft, distance, why included.
3. **Adjustment grid** — per comp: raw $/sqft → time/age/size adjustments → adjusted $/sqft.
4. **Conclusion** — point value + range + confidence, and the one-paragraph "why".
5. **Cross-check** — vs AVM and assessment, with your read.
6. **What I'd verify next** — what an experienced underwriter would check before signing off.

## Extending this skill (playbooks)

See `references/methodology.md` for the full adjustment method and `references/house-rules.md`
for KV's criteria and when to override them. When an underwriter teaches you a better method
for a situation, follow `playbooks/README.md` to capture it as a reusable playbook. Before
each analysis, scan `playbooks/` for a play whose `when:` matches the subject; if one fits,
apply it and tell the user you did.
```

- [ ] **Step 2: Verify frontmatter is valid**

Run:
```bash
head -8 skill/comp-analysis/SKILL.md
python -c "import yaml,re,sys; t=open('skill/comp-analysis/SKILL.md').read(); m=re.match(r'---\n(.*?)\n---', t, re.S); d=yaml.safe_load(m.group(1)); assert d['name']=='comp-analysis' and d['description']; print('frontmatter OK')"
```
Expected: prints `frontmatter OK` (install `pyyaml` if needed: `pip install pyyaml`).

- [ ] **Step 3: Commit**

```bash
git add skill/comp-analysis/SKILL.md
git commit -m "feat: add comp-analysis SKILL.md"
```

---

## Task 13: Skill — reference files

**Files:**
- Create: `skill/comp-analysis/references/methodology.md`
- Create: `skill/comp-analysis/references/house-rules.md`

- [ ] **Step 1: Write `references/methodology.md`**

```markdown
# Methodology — Sales Comparison Approach (defined method)

The deterministic math lives in `estimate_value`; this documents it so you can explain
each number and know what an expert can override (via `rules`).

For each comp:
1. **Raw $/sqft** = sold_price / sqft.
2. **Adjust to subject-equivalent** (multiplicative):
   - **Time:** ×(1 + trend × months_old). `trend` is fit from the comp set (0 if < 4 comps;
     clamped ±2%/mo).
   - **Age:** ×(1 + age_rate × (subject_year − comp_year)). Default age_rate 0.5%/yr
     (newer = premium).
   - **Size:** ×(1 + size_elast × (comp_sqft − subject_sqft)/subject_sqft). Default 0.20
     (larger homes have lower $/sqft, so a larger comp adjusts upward toward the subject).
3. **Drop outliers** beyond median ± 1.5·IQR of adjusted $/sqft.
4. **Reconcile** weighted by similarity: w = 1/(1 + 0.5·dist_km + 2·|size%| + 0.05·|ageΔ| +
   0.1·months_old). Value = (weighted-average adjusted $/sqft) × subject sqft.
5. **Range** = 25th–75th percentile of adjusted $/sqft × subject sqft.
6. **Confidence:** high = ≥6 comps ∧ CoV ≤ 0.10 ∧ no widening; low = <4 comps ∨ CoV > 0.20 ∨
   ≥3 relaxations; medium otherwise.

These coefficients are defaults. An expert may pass a `rules` object to `estimate_value`
(e.g. a higher `size_elast` for luxury areas) or encode it in a playbook.
```

- [ ] **Step 2: Write `references/house-rules.md`**

```markdown
# KV / Sam's house rules

Default comp-selection criteria (the `criteria` defaults in `find_comps`):

- **Radius:** within 3 km of the subject.
- **Size:** within ±20% of subject sqft.
- **Recency:** sold within the last 6–12 months.
- **Price/sqft:** the primary normalizer and ranking metric.
- **Age:** within ±10 years of the subject.

**Widening ladder** when comps are sparse — relax one step at a time, in this order, logging
each: time (12→18→24 mo) → radius (3→5→8 km) → size (±20→30→40%) → age (±10→20→30 yr).

**When to override:** unique/luxury/rural subjects may justify different bands or weighting.
Prefer fixing inputs first (correct sqft/year), then overriding `criteria`/`rules`, then
capturing a playbook if the override recurs.
```

- [ ] **Step 3: Commit**

```bash
git add skill/comp-analysis/references/
git commit -m "feat: add methodology and house-rules references"
```

---

## Task 14: Skill — playbooks scaffold

**Files:**
- Create: `skill/comp-analysis/playbooks/README.md`
- Create: `skill/comp-analysis/playbooks/acreage-with-outbuildings.md`

- [ ] **Step 1: Write `playbooks/README.md`**

```markdown
# Playbooks — capturing underwriter expertise

Each playbook is a small, reusable method an underwriter taught the agent. The baseline
(`../references/`) is the floor; playbooks raise the ceiling.

## How matching works
Before an analysis, scan this folder and read each play's `when:` line. If one matches the
subject, apply its method and tell the user you used it.

## Capture loop — "make my way into a skill"
When the user departs from the baseline, gets a better result, and asks you to remember it:
1. **Reflect** — what differed from the baseline, and why.
2. **Generalize** — into a reusable method (not a transcript of this one property).
3. **Confirm** — draft the play, show it, get approval/edits. Never save silently.
4. **Write** — `playbooks/<kebab-name>.md` using the template below.
5. **Apply** — on future runs whose subject matches `when:`.

Default to a new playbook in THIS skill (expand). Only propose a separate skill if the
method is a genuinely different domain (e.g. commercial).

## Template
\`\`\`markdown
---
name: <kebab-name>
when: <trigger conditions that make this play apply>
author: <underwriter>   date: <YYYY-MM-DD>   status: personal | shared
validated: "<optional hold-one-out result>"
---
Trigger:   <what situation this addresses>
Method:    1. <step> (which tool params to change, e.g. criteria/rules)
           2. <step>
Rationale: <why this beats the baseline here>
\`\`\`
```

- [ ] **Step 2: Write the example play `playbooks/acreage-with-outbuildings.md`**

```markdown
---
name: acreage-with-outbuildings
when: subject is an acreage / non-standard rural lot, or has significant outbuildings
author: example   date: 2026-06-07   status: shared
validated: "illustrative example — not yet backtested"
---
Trigger:   community-boundary comp search returns poor comps for rural/acreage subjects
Method:    1. widen radius early (criteria.radius_km up to 8) and search along road/river
              corridors rather than relying on a single community
           2. weight lot size and land value more heavily; note outbuildings explicitly
           3. add an outbuilding premium as a line item when reconciling
Rationale: rural value is driven by land + structures the standard $/sqft grid underweights
```

- [ ] **Step 3: Commit**

```bash
git add skill/comp-analysis/playbooks/
git commit -m "feat: add playbooks scaffold and example play"
```

---

## Task 15: README + run instructions

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

````markdown
# KV Capital — Residential Comp-Analysis Agent

A local MCP server + a `comp-analysis` Skill that turns Claude Desktop into a residential
comp-analysis assistant for the Calgary market: given a subject property, it finds
comparable recent sales (KV's house rules), adjusts them, and produces a transparent,
underwriter-style value estimate — and can learn each underwriter's own methods.

## Why this shape
- **Augments an existing workflow** (Claude Desktop) — no new app to adopt.
- **Deterministic tools + judgment Skill:** four neutral read-only MCP tools do the
  mechanics; the Skill carries the underwriter methodology and orchestration.
- **Expandable:** underwriters teach it new methods via playbooks ("make my way into a skill").

## Data & honesty
Alberta sold prices are confidential on MLS, and municipal assessments lack sqft/beds/baths
and are valuations, not sales. The demo sources comps from **HonestDoor public data**
(real sold price/date + attributes), with a **synthetic, real-grounded fallback** for thin
areas / new construction. The HonestDoor headline price is an **AVM estimate, not a sale** —
the agent only treats Sold History as a real transaction. The data source is **pluggable**
(`CompSource`): KV can swap in MLS/DDF, Land Titles, or internal deal records.

## Install (local, no hosting)
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

Register with Claude Desktop (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "kv-comp-analysis": { "command": "kv-comp-analysis" }
  }
}
```
Copy `skill/comp-analysis/` into your Claude Desktop skills directory. Restart Claude Desktop.

## Use
> "Run a comp analysis on 123 Maple Dr, Roxboro, Calgary — it's a 2,000 sqft detached built 1985."

The agent resolves the subject, finds and curates comps, estimates value with an adjustment
grid, cross-checks against the AVM/assessment, and walks you through the file.

## Accuracy
Hold-one-out backtest against real sold prices:
```bash
python -c "from datetime import date; from eval.backtest import hold_one_out; \
from mcp_server.compsource.synthetic import SyntheticCompSource; \
r=hold_one_out(SyntheticCompSource(seed=1), community='Roxboro', as_of=date(2026,6,1)); \
print(f'median abs error {r.median_abs_pct_error}% over {r.n} sales')"
```
(Swap in `HonestDoorCompSource()` for a live real-data number — a representative sample-run figure.)

## Tests
```bash
pytest -q
```

## Scope
v1: residential, Calgary-first. Documented extensions: Edmonton, commercial, real SOLD feeds
via `CompSource`. See `docs/superpowers/specs/2026-06-06-kv-comp-analysis-design.md`.
````

- [ ] **Step 2: Verify the whole suite + README commands**

Run:
```bash
pytest -q
```
Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add README with install, usage, accuracy, scope"
```

---

## Self-review notes (addressed)

- **Spec coverage:** data strategy → Tasks 8–9 + README; 4-tool surface → Task 10; Sam's 5 +
  ladder → Tasks 4–5; adjustment grid §6.1 → Tasks 6–7; Skill + playbooks §6/§6.2 →
  Tasks 12–14; eval §7 → Task 11; repo layout §9 → all; local/free §2 → Task 10 `mcp.run()` +
  README. Open questions §10 carried as Task 9 Step 1 spike + noted defaults (Tasks 6–7).
- **Type consistency:** model/field names (`Subject`, `Comp.price_per_sqft`, `Criteria`,
  `AdjustmentRules`, `FindCompsResult.relaxations`, `Estimate.per_comp`, `CompAdjustment`,
  `CrossCheck`) defined in Task 2 and used unchanged in Tasks 4–11. `find_with_ladder`,
  `filter_and_rank`, `reconcile`, `adjust_comp`, `estimate_trend`, `comp_weight`,
  `remove_outliers`, `build_tools`, `hold_one_out` referenced consistently.
- **Known cleanup baked in:** Task 6 deliberately introduces then removes a module-global
  (`as_of`) — Step 4 makes the function pure. Implementer must complete Step 4 before moving on.

## Execution handoff

See bottom of this message.
```
