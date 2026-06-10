# Article-Backed Adjustment Methodology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the invented adjustment constants and reconciliation weights with a market-derived, article-backed adjustment engine (paired-sales / grouping / regression hierarchy), a two-tier framework, a median blend, and a structured transparency payload.

**Architecture:** A new internal `mcp_server/derivation.py` module derives per-dimension coefficients from the comp set (each returning value + method + evidence + confidence). `estimate.py` orchestrates: derive set-level coefficients (time → size → beds → baths → garage, netting each out), apply them per comp, then blend by **median**. Tier-2 dimensions (age, distance/location) are filtered-not-adjusted and emit `Disclosure` caveats. The `estimate_value` MCP tool stays one intent, gaining structured output and an `overrides` parameter (no new tool — per the group-by-intent rule).

**Tech Stack:** Python 3.14, Pydantic v2, FastMCP (stdio), pytest. Spec: `docs/superpowers/specs/2026-06-10-adjustment-methodology-design.md`.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `mcp_server/models.py` | Pydantic types | Modify: restructure `Adjustment`; add `Disclosure`, `Overrides`; trim `AdjustmentRules`; drop `weight`/`pct` from `CompAdjustment`/`Adjustment`; add `disclosures` to `Estimate` |
| `mcp_server/derivation.py` | Coefficient derivation (the math hierarchy) | **Create** |
| `mcp_server/estimate.py` | Apply coefficients per comp + reconcile by median | Rewrite `adjust_comp`→`apply_adjustments`; rewrite `reconcile`; delete `estimate_trend`, `comp_weight` |
| `mcp_server/server.py` | MCP tool wiring | Modify `estimate_value` tool + `Tools.estimate_value` (overrides, structured output, annotations) |
| `skill/comp-analysis/references/methodology.md` | Method docs | Rewrite |
| `skill/comp-analysis/references/house-rules.md` | Criteria docs | Modify (adjusted vs bracketed) |
| `skill/comp-analysis/SKILL.md` | Workflow + output + judgment | Modify |
| `tests/test_models.py` | Model tests | Rewrite affected assertions |
| `tests/test_derivation.py` | Derivation tests | **Create** |
| `tests/test_estimate.py` | Apply + reconcile tests | Rewrite |
| `tests/test_server.py` | Tool tests | Modify estimate_value cases |

**Interface contract (locked here; every later task must match these exact names):**

```python
# models.py
AdjMethod = Literal["matched_pair", "grouping", "regression", "cost_convention", "none"]
SourceType = Literal["article-method", "our-judgment"]

# derivation.py
@dataclass
class Derivation:
    value: float          # %/month (time) | $/sqft (size) | $/unit (feature)
    method: AdjMethod
    source_type: SourceType
    evidence: str
    confidence: Confidence

def linreg_slope(xs: list[float], ys: list[float]) -> float | None
def derive_time_trend(comps, *, as_of, clamp) -> Derivation
def derive_marginal_ppsf(subject, comps, prices: list[float]) -> Derivation
def derive_feature_unit(subject, comps, residuals: list[float], factor: str) -> Derivation
def compute_disclosures(subject, comps) -> list[Disclosure]

# estimate.py
@dataclass
class DerivedSet:
    time: Derivation; size: Derivation; beds: Derivation; baths: Derivation; garage: Derivation
def feat_dollar(subj_count, comp_count, per_unit: float) -> float
def apply_adjustments(subject, comp, derived: DerivedSet, *, as_of) -> CompAdjustment
def reconcile(subject, comps, rules, *, as_of, ladder_depth=0, overrides=None) -> Estimate
```

---

## Task 1: Restructure the model types

**Files:**
- Modify: `mcp_server/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_models.py` and replace `test_adjustment_rules_defaults`:

```python
def test_adjustment_rules_trimmed_to_config_only():
    from mcp_server.models import AdjustmentRules
    r = AdjustmentRules()
    assert (r.trend_clamp, r.min_comps, r.outlier_iqr, r.drop_outliers) == (0.02, 4, 1.5, False)
    # invented constants are gone
    assert not hasattr(r, "age_rate")
    assert not hasattr(r, "size_elast")
    assert not hasattr(r, "weight_a")


def test_adjustment_payload_shape():
    from mcp_server.models import Adjustment
    a = Adjustment(factor="size", method_used="grouping", source_type="article-method",
                   value_dollar=-10000.0, evidence="8 comps, grouped", confidence="medium",
                   rationale="200 sqft larger x $50/sqft")
    assert a.value_pct is None and a.value_dollar == -10000.0


def test_disclosure_shape():
    from mcp_server.models import Disclosure
    d = Disclosure(factor="age", skew="comps avg 5 yr older", direction="understate",
                   caveat="older set may understate a newer subject")
    assert d.source_type == "our-judgment"


def test_overrides_all_optional():
    from mcp_server.models import Overrides
    o = Overrides()
    assert o.marginal_ppsf is None and o.garage_value is None
    assert Overrides(marginal_ppsf=50.0).marginal_ppsf == 50.0
```

Also delete the obsolete assertion `assert r.age_rate == 0.005` / `r.size_elast == 0.20` lines in the old `test_adjustment_rules_defaults` (replaced above).

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_models.py -q`
Expected: FAIL (`Adjustment` has no `method_used`, `Disclosure`/`Overrides` undefined, `AdjustmentRules` still has `age_rate`).

- [ ] **Step 3: Edit `mcp_server/models.py`**

Add near the top type aliases (after `Confidence = Literal[...]`):

```python
AdjMethod = Literal["matched_pair", "grouping", "regression", "cost_convention", "none"]
SourceType = Literal["article-method", "our-judgment"]
```

Replace the `AdjustmentRules`, `Adjustment`, and `CompAdjustment` classes with:

```python
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
```

In `Estimate`, add the disclosures field:

```python
class Estimate(BaseModel):
    point: float
    low: float
    high: float
    confidence: Confidence
    per_comp: list[CompAdjustment]
    disclosures: list[Disclosure] = Field(default_factory=list)
    method_notes: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_models.py -q`
Expected: PASS. (Other test files will fail to import — fixed in later tasks; that's expected.)

- [ ] **Step 5: Commit**

```bash
git add mcp_server/models.py tests/test_models.py
git commit -m "feat(models): article-backed adjustment payload + Disclosure/Overrides; trim AdjustmentRules"
```

---

## Task 2: Derivation helpers + time trend (grouping → regression)

**Files:**
- Create: `mcp_server/derivation.py`
- Test: `tests/test_derivation.py` (create)

- [ ] **Step 1: Write the failing tests** — create `tests/test_derivation.py`:

```python
from datetime import date
from mcp_server.models import Subject, Comp
from mcp_server.derivation import linreg_slope, derive_time_trend

AS_OF = date(2026, 6, 1)


def _comp(price, sqft=2000, d=date(2026, 5, 1), beds=3, baths=2, garage=2, yb=1985, addr="c"):
    return Comp(address=addr, lat=51.05, lng=-114.08, sold_price=price, sold_date=d,
                sqft=sqft, year_built=yb, beds=beds, baths=baths, garage=garage)


def test_linreg_slope_basic():
    assert linreg_slope([0, 1, 2, 3], [0, 2, 4, 6]) == 2.0
    assert linreg_slope([1, 1, 1], [1, 2, 3]) is None  # zero x-variance


def test_time_trend_grouping_detects_rising_market():
    # recent 3 (0-1 mo) ~ $430/sqft; older 3 (4-6 mo) ~ $405/sqft -> positive trend
    recent = [_comp(860_000, d=date(2026, 6, 1)), _comp(850_000, d=date(2026, 5, 1)),
              _comp(840_000, d=date(2026, 5, 1))]
    older = [_comp(800_000, d=date(2025, 12, 1)), _comp(820_000, d=date(2026, 1, 1)),
             _comp(810_000, d=date(2026, 2, 1))]
    dv = derive_time_trend(recent + older, as_of=AS_OF, clamp=0.02)
    assert dv.method in ("grouping", "regression")
    assert dv.value > 0
    assert -0.02 <= dv.value <= 0.02


def test_time_trend_none_when_too_few():
    dv = derive_time_trend([_comp(800_000)], as_of=AS_OF, clamp=0.02)
    assert dv.method == "none" and dv.value == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_derivation.py -q`
Expected: FAIL (module `mcp_server.derivation` does not exist).

- [ ] **Step 3: Create `mcp_server/derivation.py`**

`Derivation` is a dataclass **local to this module** (not a Pydantic model) — do not import it from `models`. Note `mean` is imported (the regression rung uses it):

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from statistics import median, mean
from typing import Optional
from mcp_server.models import Subject, Comp, Disclosure, AdjMethod, SourceType, Confidence
from mcp_server.comps import months_between


@dataclass
class Derivation:
    value: float
    method: AdjMethod
    source_type: SourceType
    evidence: str
    confidence: Confidence


def _none(reason: str) -> Derivation:
    return Derivation(0.0, "none", "our-judgment", reason, "low")


def linreg_slope(xs: list[float], ys: list[float]) -> Optional[float]:
    """Least-squares slope dy/dx, or None if x has zero variance / <2 points."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    my = sum(ys) / n
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def _clamp(v: float, c: float) -> float:
    return max(-c, min(c, v))


def derive_time_trend(comps: list[Comp], *, as_of: date, clamp: float) -> Derivation:
    """Market-conditions %/month. Grouping of sales (primary) -> regression (small-N fallback)."""
    if len(comps) < 4:
        return _none("fewer than 4 comps; market trend not estimated")
    months = [max(months_between(c.sold_date, as_of), 0) for c in comps]
    ppsf = [c.price_per_sqft for c in comps]

    # Grouping: split at median months into recent vs older; need >=2 each and a time gap.
    cut = median(months)
    recent = [(m, p) for m, p in zip(months, ppsf) if m <= cut]
    older = [(m, p) for m, p in zip(months, ppsf) if m > cut]
    if len(recent) >= 2 and len(older) >= 2:
        rm, rp = median([m for m, _ in recent]), median([p for _, p in recent])
        om, op = median([m for m, _ in older]), median([p for _, p in older])
        gap = om - rm
        if gap > 0 and op > 0:
            per_month = _clamp(((rp - op) / op) / gap, clamp)
            ev = (f"recent comps median ${rp:.0f}/sqft (~{rm:.0f} mo) vs older "
                  f"${op:.0f}/sqft (~{om:.0f} mo) over {gap:.0f} mo")
            return Derivation(round(per_month, 5), "grouping", "article-method", ev, "medium")

    # Regression fallback: slope of ppsf vs months-ago, normalized to a fraction.
    slope = linreg_slope([-m for m in months], ppsf)   # more-recent = larger x
    if slope is None:
        return _none("no time variation across comps")
    my = mean(ppsf)
    per_month = _clamp(slope / my if my else 0.0, clamp)
    return Derivation(round(per_month, 5), "regression", "article-method",
                      f"least-squares over {len(comps)} comps (small-N fallback)", "low")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_derivation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/derivation.py tests/test_derivation.py
git commit -m "feat(derivation): time-trend via grouping-of-sales with regression fallback"
```

---

## Task 3: Size — marginal $/sqft (pair → grouping → regression)

**Files:**
- Modify: `mcp_server/derivation.py`
- Test: `tests/test_derivation.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
from mcp_server.derivation import derive_marginal_ppsf


def _subject(sqft=1800, beds=3, baths=2, garage=2, yb=1985):
    return Subject(address="S", lat=51.05, lng=-114.08, sqft=sqft, year_built=yb,
                   beds=beds, baths=baths, garage=garage)


def test_marginal_ppsf_grouping_recovers_rate():
    s = _subject(sqft=1800)
    # larger homes cost a bit more in total but less per sqft -> ~ $50/sqft marginal
    comps = [_comp(700_000, sqft=1800), _comp(705_000, sqft=1800),
             _comp(710_000, sqft=2000), _comp(715_000, sqft=2000)]
    prices = [c.sold_price for c in comps]
    dv = derive_marginal_ppsf(s, comps, prices)
    assert dv.method in ("matched_pair", "grouping", "regression")
    assert 20 <= dv.value <= 120     # sane marginal rate, well below avg ppsf (~$380)


def test_marginal_ppsf_none_without_size_spread():
    s = _subject(sqft=1800)
    comps = [_comp(700_000, sqft=1800), _comp(702_000, sqft=1800)]
    dv = derive_marginal_ppsf(s, comps, [c.sold_price for c in comps])
    assert dv.method == "none" and dv.value == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_derivation.py::test_marginal_ppsf_grouping_recovers_rate -q`
Expected: FAIL (`derive_marginal_ppsf` undefined).

- [ ] **Step 3: Add to `mcp_server/derivation.py`**

```python
def _matched_pair_ppsf(subject: Subject, comps: list[Comp], prices: list[float]) -> Optional[Derivation]:
    """Two comps alike except sqft (>=8% apart, same beds/baths/garage) -> Δprice/Δsqft."""
    n = len(comps)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = comps[i], comps[j]
            dsqft = a.sqft - b.sqft
            if a.sqft == 0 or abs(dsqft) / a.sqft < 0.08:
                continue
            if (a.beds, a.baths, a.garage) != (b.beds, b.baths, b.garage):
                continue
            rate = (prices[i] - prices[j]) / dsqft
            if 0 < rate < 1000:
                return Derivation(round(rate, 2), "matched_pair", "article-method",
                                  f"pair {a.address}/{b.address}: Δ${prices[i]-prices[j]:.0f} "
                                  f"over {dsqft:.0f} sqft", "high")
    return None


def derive_marginal_ppsf(subject: Subject, comps: list[Comp], prices: list[float]) -> Derivation:
    """$ per extra sqft (GLA). matched pair -> grouping (larger vs smaller half) -> regression."""
    if len(comps) < 2:
        return _none("need >=2 comps to derive a size rate")
    pair = _matched_pair_ppsf(subject, comps, prices)
    if pair:
        return pair

    sqfts = [c.sqft for c in comps]
    cut = median(sqfts)
    large = [(s, p) for s, p in zip(sqfts, prices) if s > cut]
    small = [(s, p) for s, p in zip(sqfts, prices) if s <= cut]
    if large and small:
        ls, lp = median([s for s, _ in large]), median([p for _, p in large])
        ss, sp = median([s for s, _ in small]), median([p for _, p in small])
        dsqft = ls - ss
        if dsqft > 0:
            rate = (lp - sp) / dsqft
            if 0 < rate < 1000:
                return Derivation(round(rate, 2), "grouping", "article-method",
                                  f"larger half median ${lp:.0f}@{ls:.0f}sqft vs smaller "
                                  f"${sp:.0f}@{ss:.0f}sqft", "medium")

    slope = linreg_slope(sqfts, prices)
    if slope is not None and 0 < slope < 1000:
        return Derivation(round(slope, 2), "regression", "article-method",
                          f"slope of price~sqft over {len(comps)} comps", "low")
    return _none("no usable size spread; size not adjusted")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_derivation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/derivation.py tests/test_derivation.py
git commit -m "feat(derivation): marginal \$/sqft via matched-pair/grouping/regression"
```

---

## Task 4: Features — per-unit value for beds/baths/garage

**Files:**
- Modify: `mcp_server/derivation.py`
- Test: `tests/test_derivation.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
from mcp_server.derivation import derive_feature_unit


def test_feature_unit_garage_grouping():
    s = _subject(garage=2)
    # 2-car comps ~ $15k above 1-car comps (residuals already size/time-netted)
    comps = [_comp(700_000, garage=1), _comp(702_000, garage=1),
             _comp(716_000, garage=2), _comp(718_000, garage=2)]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "garage")
    assert dv.method in ("matched_pair", "grouping", "regression")
    assert 8000 <= dv.value <= 25000


def test_feature_unit_none_without_variation():
    s = _subject(baths=2)
    comps = [_comp(700_000, baths=2), _comp(705_000, baths=2)]
    dv = derive_feature_unit(s, comps, [c.sold_price for c in comps], "baths")
    assert dv.method == "none" and dv.value == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_derivation.py::test_feature_unit_garage_grouping -q`
Expected: FAIL (`derive_feature_unit` undefined).

- [ ] **Step 3: Add to `mcp_server/derivation.py`**

```python
def derive_feature_unit(subject: Subject, comps: list[Comp],
                        residuals: list[float], factor: str) -> Derivation:
    """$ per unit of `factor` (beds|baths|garage), on the size/time-netted residual.
    Grouping: comps with above-median count vs at-or-below, per unit of count gap.
    Regression fallback: slope of residual ~ count. Null-safe: only known counts used."""
    known = [(getattr(c, factor), r) for c, r in zip(comps, residuals)
             if getattr(c, factor) is not None]
    counts = sorted({k for k, _ in known})
    if len(known) < 2 or len(counts) < 2:
        return _none(f"no {factor} variation across comps; not adjusted")

    cut = median([k for k, _ in known])
    hi = [(k, r) for k, r in known if k > cut]
    lo = [(k, r) for k, r in known if k <= cut]
    if hi and lo:
        hk, hr = median([k for k, _ in hi]), median([r for _, r in hi])
        lk, lr = median([k for k, _ in lo]), median([r for _, r in lo])
        dcount = hk - lk
        if dcount > 0:
            per_unit = (hr - lr) / dcount
            if per_unit != 0:
                return Derivation(round(per_unit, 2), "grouping", "article-method",
                                  f"{factor}: {hk:g}-count median ${hr:.0f} vs {lk:g}-count "
                                  f"${lr:.0f}", "medium")

    slope = linreg_slope([k for k, _ in known], [r for _, r in known])
    if slope is not None and slope != 0:
        return Derivation(round(slope, 2), "regression", "article-method",
                          f"slope of residual~{factor} over {len(known)} comps", "low")
    return _none(f"{factor} signal too flat; not adjusted")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_derivation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/derivation.py tests/test_derivation.py
git commit -m "feat(derivation): per-unit bed/bath/garage value via grouping on residual"
```

---

## Task 5: Tier-2 disclosures (vintage & location skew)

**Files:**
- Modify: `mcp_server/derivation.py`
- Test: `tests/test_derivation.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
from mcp_server.derivation import compute_disclosures


def test_disclosure_flags_older_comp_skew():
    s = _subject(yb=2015)
    comps = [_comp(700_000, yb=2005), _comp(700_000, yb=2006), _comp(700_000, yb=2007)]
    ds = compute_disclosures(s, comps)
    age = next(d for d in ds if d.factor == "age")
    assert age.direction == "understate"   # comps older -> may understate newer subject


def test_disclosure_quiet_when_balanced():
    s = _subject(yb=2010)
    comps = [_comp(700_000, yb=2009), _comp(700_000, yb=2011), _comp(700_000, yb=2010)]
    ds = compute_disclosures(s, comps)
    age = next((d for d in ds if d.factor == "age"), None)
    assert age is None or age.direction == "unknown"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_derivation.py::test_disclosure_flags_older_comp_skew -q`
Expected: FAIL (`compute_disclosures` undefined).

- [ ] **Step 3: Add to `mcp_server/derivation.py`**

```python
def compute_disclosures(subject: Subject, comps: list[Comp]) -> list[Disclosure]:
    """Tier-2 imbalance caveats: dimensions we filter but don't adjust."""
    out: list[Disclosure] = []

    # Vintage: mean comp year vs subject; >2 yr one-sided gap is worth flagging.
    years = [c.year_built for c in comps if c.year_built is not None]
    if subject.year_built and years:
        avg_gap = subject.year_built - (sum(years) / len(years))  # +ve => comps older
        if abs(avg_gap) >= 2:
            direction = "understate" if avg_gap > 0 else "overstate"
            out.append(Disclosure(
                factor="age",
                skew=f"comps average {abs(avg_gap):.0f} yr {'older' if avg_gap>0 else 'newer'} than subject",
                direction=direction,
                caveat=("Age is controlled by the +/-10yr filter, not adjusted; an "
                        f"{'older' if avg_gap>0 else 'newer'} comp set may {direction} a "
                        "newer subject. Condition/rehab is out of scope.")))
        else:
            out.append(Disclosure(factor="age", skew="comps balanced in vintage",
                                  direction="unknown", caveat="No material vintage skew."))

    # Location: mean distance + directional clustering hint.
    dists = [c.distance_km for c in comps if c.distance_km is not None]
    if dists:
        out.append(Disclosure(
            factor="location", skew=f"comps average {sum(dists)/len(dists):.1f} km away",
            direction="unknown",
            caveat=("Distance is filtered (<=3km), not adjusted, and we lack per-community "
                    "data; if comps sit in a different-value pocket the baseline may be biased.")))
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_derivation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/derivation.py tests/test_derivation.py
git commit -m "feat(derivation): Tier-2 vintage/location skew disclosures"
```

---

## Task 6: Apply coefficients per comp + reconcile by median

**Files:**
- Rewrite: `mcp_server/estimate.py`
- Rewrite: `tests/test_estimate.py`

- [ ] **Step 1: Replace `tests/test_estimate.py` entirely**

```python
from datetime import date
import pytest
from mcp_server.models import Subject, Comp, AdjustmentRules, Overrides, Estimate
from mcp_server.estimate import feat_dollar, apply_adjustments, reconcile
from mcp_server.derivation import (
    Derivation, derive_time_trend, derive_marginal_ppsf, derive_feature_unit,
)
from mcp_server.estimate import DerivedSet

AS_OF = date(2026, 6, 1)


def _subject(sqft=2000, yb=1985, beds=3, baths=2, garage=2):
    return Subject(address="S", lat=51.05, lng=-114.08, sqft=sqft, year_built=yb,
                   beds=beds, baths=baths, garage=garage)


def _comp(price, sqft=2000, yb=1985, beds=3, baths=2, garage=2, d=date(2026, 5, 1), addr="c"):
    c = Comp(address=addr, lat=51.05, lng=-114.08, sold_price=price, sold_date=d,
             sqft=sqft, year_built=yb, beds=beds, baths=baths, garage=garage)
    c.distance_km = 0.6
    return c


def _flat(v):  # a no-op derivation
    return Derivation(v, "none", "our-judgment", "n/a", "low")


def test_feat_dollar_is_null_safe_and_directional():
    assert feat_dollar(3, 2, 5000) == 5000.0    # subject has more -> add to comp
    assert feat_dollar(2, 3, 5000) == -5000.0
    assert feat_dollar(None, 2, 5000) == 0.0
    assert feat_dollar(3, None, 5000) == 0.0


def test_apply_size_brings_larger_comp_down():
    s = _subject(sqft=2000)
    c = _comp(880_000, sqft=2200)               # 200 sqft larger
    derived = DerivedSet(_flat(0.0), Derivation(50.0, "grouping", "article-method", "x", "medium"),
                         _flat(0.0), _flat(0.0), _flat(0.0))
    ca = apply_adjustments(s, c, derived, as_of=AS_OF)
    size = next(a for a in ca.adjustments if a.factor == "size")
    assert size.value_dollar == -10000.0        # 200 * 50, subtracted
    assert ca.adjusted_price == 870_000.0


def test_reconcile_blends_by_median_and_emits_payload():
    s = _subject(sqft=2000)
    comps = [_comp(800_000), _comp(810_000), _comp(795_000),
             _comp(805_000), _comp(800_000), _comp(812_000)]
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF, ladder_depth=0)
    assert isinstance(est, Estimate)
    assert est.low <= est.point <= est.high
    assert len(est.per_comp) == 6
    assert est.disclosures                       # Tier-2 caveats present
    # no weight field leaked through
    assert not hasattr(est.per_comp[0], "weight")


def test_reconcile_respects_overrides():
    s = _subject(sqft=2000)
    comps = [_comp(800_000, sqft=2200), _comp(802_000, sqft=2200),
             _comp(800_000, sqft=2000), _comp(801_000, sqft=2000)]
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF,
                    overrides=Overrides(marginal_ppsf=100.0))
    size = next(a for a in est.per_comp[0].adjustments if a.factor == "size")
    assert size.source_type == "our-judgment"   # _override re-tags as our-judgment
    # override applied: a 200-sqft-larger comp gets -$20,000
    big = next(ca for ca in est.per_comp if ca.raw_price in (800_000, 802_000) and ca.raw_ppsf < 380)
    assert any(a.factor == "size" and a.value_dollar == -20000.0 for a in big.adjustments)


def test_reconcile_empty_raises():
    with pytest.raises(ValueError):
        reconcile(_subject(), [], AdjustmentRules(), as_of=AS_OF)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_estimate.py -q`
Expected: FAIL (`feat_dollar`/`apply_adjustments`/`DerivedSet` undefined; old `estimate.py` symbols gone).

- [ ] **Step 3: Replace `mcp_server/estimate.py` entirely**

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from statistics import median, mean, pstdev, quantiles
from typing import Optional
from mcp_server.models import (
    Subject, Comp, AdjustmentRules, Overrides, Adjustment, CompAdjustment,
    Disclosure, Estimate, Confidence,
)
from mcp_server.comps import months_between
from mcp_server.derivation import (
    Derivation, derive_time_trend, derive_marginal_ppsf, derive_feature_unit,
    compute_disclosures,
)


@dataclass
class DerivedSet:
    time: Derivation
    size: Derivation
    beds: Derivation
    baths: Derivation
    garage: Derivation


def feat_dollar(subj_count, comp_count, per_unit: float) -> float:
    """Null-safe directional feature dollar: subject richer than comp -> add to comp."""
    if subj_count is None or comp_count is None:
        return 0.0
    return round((subj_count - comp_count) * per_unit, 2)


def _override(dv: Derivation, value) -> Derivation:
    return Derivation(value, dv.method if dv.method != "none" else "grouping",
                      "our-judgment", f"override (was {dv.value})", "medium")


def _adj(factor, method, source, *, pct=None, dollar=None, evidence, conf) -> Adjustment:
    if pct is not None:
        rationale = f"{factor}: {pct*100:+.2f}% ({evidence})"
    else:
        rationale = f"{factor}: ${dollar:+,.0f} ({evidence})"
    return Adjustment(factor=factor, method_used=method, source_type=source,
                      value_pct=pct, value_dollar=dollar, evidence=evidence,
                      confidence=conf, rationale=rationale)


def apply_adjustments(subject: Subject, comp: Comp, derived: DerivedSet, *, as_of: date) -> CompAdjustment:
    """Sequence time -> size -> beds -> baths -> garage on the comp's price."""
    raw_ppsf = comp.price_per_sqft
    months = max(months_between(comp.sold_date, as_of), 0)
    adjustments: list[Adjustment] = []

    time_pct = derived.time.value * months
    p = comp.sold_price * (1 + time_pct)
    adjustments.append(_adj("time", derived.time.method, derived.time.source_type,
                            pct=round(time_pct, 5),
                            evidence=f"{months} mo @ {derived.time.value*100:.2f}%/mo; {derived.time.evidence}",
                            conf=derived.time.confidence))

    size_dollar = -(comp.sqft - subject.sqft) * derived.size.value
    p += size_dollar
    adjustments.append(_adj("size", derived.size.method, derived.size.source_type,
                            dollar=round(size_dollar, 2),
                            evidence=f"{comp.sqft - subject.sqft:+.0f} sqft @ ${derived.size.value:.0f}/sqft; {derived.size.evidence}",
                            conf=derived.size.confidence))

    for factor, dv in (("beds", derived.beds), ("baths", derived.baths), ("garage", derived.garage)):
        d = feat_dollar(getattr(subject, factor), getattr(comp, factor), dv.value)
        p += d
        adjustments.append(_adj(factor, dv.method, dv.source_type, dollar=d,
                                evidence=dv.evidence, conf=dv.confidence))

    adjusted_price = round(p, 0)
    return CompAdjustment(
        address=comp.address, raw_price=comp.sold_price, raw_ppsf=raw_ppsf,
        adjustments=adjustments, adjusted_price=adjusted_price,
        adjusted_ppsf=round(adjusted_price / subject.sqft, 2) if subject.sqft else 0.0,
    )


def remove_outliers(values: list[float], *, iqr_mult: float = 1.5) -> list[int]:
    if len(values) < 4:
        return list(range(len(values)))
    q1, _, q3 = quantiles(values, n=4)
    iqr = q3 - q1
    lo, hi = median(values) - iqr_mult * iqr, median(values) + iqr_mult * iqr
    return [i for i, v in enumerate(values) if lo <= v <= hi]


def _confidence(n: int, cov: float, ladder_depth: int, derived: DerivedSet) -> Confidence:
    base: Confidence
    if n < 4 or cov > 0.20 or ladder_depth >= 3:
        base = "low"
    elif n >= 6 and cov <= 0.10 and ladder_depth == 0:
        base = "high"
    else:
        base = "medium"
    # Method strength: if time or size leaned on regression/none, cap at medium.
    weak = {"regression", "none"}
    if derived.time.method in weak or derived.size.method in weak:
        if base == "high":
            base = "medium"
    return base


def reconcile(subject: Subject, comps: list[Comp], rules: AdjustmentRules, *,
              as_of: date, ladder_depth: int = 0, overrides: Optional[Overrides] = None) -> Estimate:
    if not comps:
        raise ValueError("reconcile requires at least one comp")
    overrides = overrides or Overrides()
    notes: list[str] = []

    # 1. time
    time = derive_time_trend(comps, as_of=as_of, clamp=rules.trend_clamp)
    if overrides.time_pct_per_month is not None:
        time = _override(time, overrides.time_pct_per_month)
    tprices = [c.sold_price * (1 + time.value * max(months_between(c.sold_date, as_of), 0))
               for c in comps]

    # 2. size (on time-adjusted prices)
    size = derive_marginal_ppsf(subject, comps, tprices)
    if overrides.marginal_ppsf is not None:
        size = _override(size, overrides.marginal_ppsf)
    sprices = [tp - (c.sqft - subject.sqft) * size.value for tp, c in zip(tprices, comps)]

    # 3-5. features, each netted out before the next
    resid = list(sprices)
    feats: dict[str, Derivation] = {}
    ov = {"beds": overrides.bed_value, "baths": overrides.bath_value, "garage": overrides.garage_value}
    for factor in ("beds", "baths", "garage"):
        dv = derive_feature_unit(subject, comps, resid, factor)
        if ov[factor] is not None:
            dv = _override(dv, ov[factor])
        feats[factor] = dv
        resid = [r - feat_dollar(getattr(subject, factor), getattr(c, factor), dv.value)
                 for r, c in zip(resid, comps)]

    derived = DerivedSet(time, size, feats["beds"], feats["baths"], feats["garage"])
    notes.append(f"time {time.method} {time.value*100:.2f}%/mo; size {size.method} ${size.value:.0f}/sqft")

    per_comp = [apply_adjustments(subject, c, derived, as_of=as_of) for c in comps]

    prices = [ca.adjusted_price for ca in per_comp]
    if rules.drop_outliers:
        keep = remove_outliers(prices, iqr_mult=rules.outlier_iqr)
        if len(keep) < len(prices):
            notes.append(f"dropped {len(prices)-len(keep)} outlier(s)")
        per_comp = [per_comp[i] for i in keep]
        prices = [prices[i] for i in keep]

    point = round(median(prices), 0)
    if len(prices) >= 4:
        q1, _, q3 = quantiles(sorted(prices), n=4)
    else:
        q1, q3 = min(prices), max(prices)
    low, high = round(min(q1, point), 0), round(max(q3, point), 0)

    ppsf_vals = [ca.adjusted_ppsf for ca in per_comp]
    m = mean(ppsf_vals)
    cov = (pstdev(ppsf_vals) / m) if (len(ppsf_vals) > 1 and m) else 0.0
    conf = _confidence(len(per_comp), cov, ladder_depth, derived)
    notes.append(f"{len(per_comp)} comps, $/sqft CoV {cov:.2f}, ladder depth {ladder_depth}")

    return Estimate(point=point, low=low, high=high, confidence=conf, per_comp=per_comp,
                    disclosures=compute_disclosures(subject, comps), method_notes=notes)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_estimate.py tests/test_derivation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/estimate.py tests/test_estimate.py
git commit -m "feat(estimate): sequenced apply + median reconcile + overrides + disclosures"
```

---

## Task 7: Wire `estimate_value` (overrides + annotations) and fix server tests

**Files:**
- Modify: `mcp_server/server.py:81-85` (`Tools.estimate_value`) and `:137-145` (tool wrapper)
- Modify: `tests/test_server.py`

- [ ] **Step 1: Add the failing server test** — append to `tests/test_server.py` (it already has the module-global `TOOLS` and `SUBJECT_OVERRIDES`; the existing `test_estimate_value_runs_on_found_comps` stays as-is and keeps passing since `overrides` is optional):

```python
def test_estimate_value_payload_and_overrides():
    s = TOOLS.get_subject("123 Maple Dr, Calgary", overrides=SUBJECT_OVERRIDES)
    res = TOOLS.find_comps(s)
    est = TOOLS.estimate_value(s, res.comps, overrides={"marginal_ppsf": 60.0})
    assert est.point > 0
    assert est.per_comp and est.per_comp[0].adjustments
    assert est.disclosures                              # Tier-2 caveats present
    size = next(a for a in est.per_comp[0].adjustments if a.factor == "size")
    assert size.source_type == "our-judgment"           # override re-tags it
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_server.py -q`
Expected: FAIL (`estimate_value` has no `overrides` kwarg).

- [ ] **Step 3: Edit `mcp_server/server.py`**

Replace `Tools.estimate_value` (lines ~81-85) with:

```python
    def estimate_value(self, subject: Subject, comps: list, *,
                       rules: Optional[AdjustmentRules] = None,
                       overrides: Optional[dict] = None,
                       ladder_depth: int = 0) -> Estimate:
        self._require(subject, ["sqft"])
        from mcp_server.models import Overrides
        ov = Overrides(**overrides) if overrides else None
        return reconcile(subject, comps, rules or AdjustmentRules(),
                         as_of=self.as_of, ladder_depth=ladder_depth, overrides=ov)
```

Update the import line `from mcp_server.models import (... AdjustmentRules,)` to also import `Overrides`.

Replace the `estimate_value` MCP tool wrapper (lines ~137-145) with:

```python
    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True,
                           "openWorldHint": False, "title": "Estimate value from comps"})
    def estimate_value(subject: dict, comps: list, rules: Optional[dict] = None,
                       overrides: Optional[dict] = None, ladder_depth: int = 0) -> dict:
        """Estimate the subject's value from comps via market-derived adjustments
        (paired-sales/grouping/regression) blended by median. Pure computation, no
        network. Each adjustment reports its method, evidence and confidence; Tier-2
        dimensions (age, location) come back as `disclosures`, not adjustments. Pass
        `overrides` (e.g. {"garage_value": 10000}) to replace a derived coefficient.
        Takes comps from find_comps; pass the FULL comp set, not a display subset."""
        r = AdjustmentRules(**rules) if rules else AdjustmentRules()
        from mcp_server.models import Comp
        cs = [Comp(**c) for c in comps]
        return tools.estimate_value(Subject(**subject), cs, rules=r,
                                    overrides=overrides, ladder_depth=ladder_depth).model_dump()
```

Add `title` annotations to the other three tool decorators too (e.g. `"title": "Resolve subject"`, `"title": "Find comps"`, `"title": "Cross-check estimate"`).

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_server.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server/server.py tests/test_server.py
git commit -m "feat(server): estimate_value gains overrides param + title annotations"
```

---

## Task 8: Full suite green (verification)

**Files:** none expected. (`eval/backtest.py` already calls `reconcile(subject, comps, AdjustmentRules(), as_of=..., ladder_depth=...)` — backward-compatible, no change. `tests/stubs.py` constructs only `Comp`/`PropertyRecord`, untouched. All retired symbols lived only in `mcp_server/estimate.py` and `mcp_server/models.py`, both rewritten in Tasks 1 & 6.)

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest -q`
Expected: PASS. `tests/test_live_e2e.py` may skip if it needs network — that's fine.

- [ ] **Step 2: If anything unexpectedly references a retired symbol**, migrate it:
- `estimate_trend(...)` → `derive_time_trend(comps, as_of=..., clamp=0.02).value`
- `comp_weight` / `.weight` → removed (median blend has no weights); delete the assertion
- `adjust_comp(...)` → `apply_adjustments(s, c, DerivedSet(...), as_of=...)`
- `AdjustmentRules(age_rate=...)` / `size_elast` → removed; use `Overrides(...)` for a forced coefficient

Re-run `python -m pytest -q` until green.

- [ ] **Step 3: Commit (only if a migration edit was needed)**

```bash
git add tests/ && git commit -m "test: migrate stray references to the derived-adjustment API" || echo "nothing to commit"
```

---

## Task 9: Skill docs — methodology, house-rules, SKILL.md

**Files:**
- Rewrite: `skill/comp-analysis/references/methodology.md`
- Modify: `skill/comp-analysis/references/house-rules.md`
- Modify: `skill/comp-analysis/SKILL.md`

- [ ] **Step 1: Rewrite `skill/comp-analysis/references/methodology.md`**

```markdown
# Methodology — Sales Comparison Approach (article-backed)

Adjustment magnitudes are **derived from the comp set**, not hard-coded. Method names follow
McKissock's appraisal guides:
- Adjustments cheat sheet: https://www.mckissock.com/blog/appraisal/appraisal-adjustments-types-methods-and-cheat-sheet/
- Paired sales analysis: https://www.mckissock.com/blog/appraisal/paired-sales-analysis/

**Attribution discipline:** every line is tagged `article-method` (from the source) or
`our-judgment` (our reasoning). Never attribute our judgment to the source.

## Two tiers
- **Tier 1 — adjusted (dollar magnitude derived from the comps):** time, size, beds, baths, garage.
- **Tier 2 — bracketed (filtered, not adjusted; imbalance disclosed):** age, distance/location.

## Method hierarchy (first supported rung wins; stamped per line)
1. **matched pair** — two comps alike except one feature → cleanest.
2. **grouping of sales** — median of comps with vs without the feature (realistic primary).
3. **regression / least-squares** — slope across comps (small-N fallback).
4. **cost / convention** — cited, "not locally derived" (last resort).
Lower rung → lower confidence.

## Sequence (net each out before the next — prevents double-counting)
transactional (flag-only) → time → size → beds → baths → garage → location (qualitative).

- **Time:** %/month from grouping of recent vs older sales (regression fallback); clamped ±2%/mo.
- **Size (GLA):** `(comp.sqft − subject.sqft) × marginal $/sqft`, marginal rate = Δprice/Δsqft from
  the comps. Marginal $/sqft is below average $/sqft — land + fixed value already counted.
- **Beds/Baths/Garage:** per-unit $ from grouping on the size/time-netted residual; null-safe.
- **Age:** *not adjusted.* Controlled by the ±10yr filter. Buyers price *effective* age (condition),
  which we lack data for → deferred to the rehab markdown (out of scope). `our-judgment`.
- **Location:** *not adjusted.* Controlled by the 3km filter; no per-community data. Qualitative.

## Reconciliation
Point = **median** of the comps' adjusted prices (no weighting). Range = 25th–75th percentile.
Confidence = comp count + $/sqft dispersion + ladder depth, capped if time/size fell back to
regression/none.

## Overrides
An underwriter can replace any derived coefficient via `estimate_value(..., overrides=...)`
(`marginal_ppsf`, `garage_value`, …) after inspecting the evidence.

## Out of scope
Condition, rehab, deferred maintenance — disclosed, with guidance to mark the baseline down.
```

- [ ] **Step 2: Edit `skill/comp-analysis/references/house-rules.md`** — append:

```markdown
## Adjusted vs bracketed
- **Adjusted** (magnitude derived from the comps): recency/time, size, beds, baths, garage.
- **Bracketed** (filtered, not adjusted; imbalance disclosed): age (±10yr), radius/location (3km).
  $/sqft remains the normalizer, not an adjustment.
```

- [ ] **Step 3: Edit `skill/comp-analysis/SKILL.md`** — replace the `## Output — "the file"` section with:

```markdown
## Output — "the file"

1. **Subject** — address + key attributes, noting user-provided vs looked-up.
2. **Comps** — table: address, sold price/date, sqft, $/sqft, distance, why included. Use the
   FULL comp set in the math even if you only display the closest ~10.
3. **Adjustment grid** — per comp, each line item shows: factor, $ or % value, **method**
   (matched_pair/grouping/regression), **source** (article-method/our-judgment), and confidence.
4. **Disclosures** — Tier-2 caveats (age/vintage skew, location clustering): the imbalance, its
   likely direction of bias, and why it wasn't adjusted.
5. **Conclusion** — median point value + 25–75% range + confidence, and the one-paragraph "why".
6. **Not in this number** — condition/rehab/deferred maintenance are out of scope; suggest the
   user mark the baseline down for them.
7. **Cross-check** — vs AVM and assessment.
8. **What I'd verify next.**

If the underwriter disputes a derived number, re-run `estimate_value` with `overrides`
(e.g. `{"garage_value": 10000}`) and show the revised file.
```

Also update the step-4 line in `## Workflow` to mention overrides:

```markdown
4. **`estimate_value(subject, comps, overrides?, ladder_depth)`** — pass `ladder_depth =
   len(relaxations)` and the FULL comp set. Adjustments are derived from the comps and reported
   with method/source/confidence; pass `overrides` to correct any coefficient.
```

- [ ] **Step 4: Commit**

```bash
git add skill/comp-analysis/
git commit -m "docs(skill): two-tier article-backed methodology, disclosures, overrides in output"
```

---

## Task 10: Manual smoke + spec cross-check

**Files:** none (verification only)

- [ ] **Step 1: Smoke the engine end-to-end with a stub**

Run:
```bash
python -c "
from datetime import date
from mcp_server.models import Subject, Comp
from mcp_server.estimate import reconcile
from mcp_server.models import AdjustmentRules
s = Subject(address='S', lat=51.05, lng=-114.08, sqft=1800, year_built=2012, beds=3, baths=2, garage=2)
def c(p, sq, g, d): 
    x=Comp(address='c'+str(p), lat=51.05, lng=-114.08, sold_price=p, sold_date=d, sqft=sq, year_built=2010, beds=3, baths=2, garage=g); x.distance_km=0.7; return x
comps=[c(700000,1800,1,date(2026,1,1)),c(705000,1800,1,date(2026,2,1)),c(720000,2000,2,date(2026,5,1)),c(725000,2000,2,date(2026,5,1)),c(715000,1900,2,date(2026,4,1)),c(710000,1850,1,date(2026,3,1))]
e=reconcile(s,comps,AdjustmentRules(),as_of=date(2026,6,1))
print('point',e.point,'range',e.low,e.high,'conf',e.confidence)
for d in e.disclosures: print('DISCLOSE',d.factor,d.direction,'-',d.skew)
for a in e.per_comp[0].adjustments: print(' ',a.factor,a.method_used,a.source_type,a.value_pct,a.value_dollar)
"
```
Expected: a positive `point`, `low <= point <= high`, at least one disclosure, and per-line adjustments showing `method_used`/`source_type`.

- [ ] **Step 2: Cross-check against the spec**

Open `docs/superpowers/specs/2026-06-10-adjustment-methodology-design.md` and confirm each of §2–§9 maps to a task above. Note any gap; if found, add a task.

- [ ] **Step 3: Final full run**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 4: Commit (if any smoke-driven fixes were made)**

```bash
git add -A && git commit -m "chore: smoke-test fixes for derived-adjustment engine" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Skill-eval validation** (per `skill-creator`) happens *after* this plan lands — run the eval loop on the rewritten Skill with a few realistic prompts, then description-optimize. Not part of these code tasks.
- **MCP restart gotcha:** after server changes, a Claude Desktop fix needs BOTH a full Desktop restart AND a fresh conversation (see memory). Tests run without the transport, so iterate via `pytest`, not Desktop.
- **No new tool** was added — derivation lives inside `estimate_value` by design (group-by-intent).
- **Structured output:** the tool returns the full payload as a structured dict (`model_dump()`), which satisfies the spec's machine-readable-payload intent. Declaring an explicit FastMCP `outputSchema`/`structuredContent` is deferred — all four tools currently return `dict`, and adding typed output schemas is a uniform polish pass better done across the whole surface at once, not just here.
