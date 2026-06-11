# Interactive HTML Comp Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a single self-contained, interactive HTML comp report (clickable at the end of a Claude Desktop comp analysis) that shows the baseline value, confidence + reasoning, the comp list, every adjustment's full derivation (which comps + arithmetic), and warnings.

**Architecture:** Enrich the MCP derivation engine to emit a structured `CoefficientTrace` per factor (the 4a/4b drill-down) and carry it on `Estimate.coefficients`; unify the size coefficient to median-of-all-pairs for a uniform "N pairs → median" story. Add a pure `render_report_html(payload)` renderer (`mcp_server/report.py`) and an MCP `render_report` tool that writes the `.html` to disk and returns its absolute path. The comp-analysis skill calls it as the final step and surfaces the path + `file://` link.

**Tech Stack:** Python 3, Pydantic, FastMCP (stdio), pytest. HTML uses inline CSS + native `<details>`/`<summary>` — zero JS, no external assets.

---

## File Structure

- `mcp_server/models.py` (modify) — add `PairTrace`, `CoefficientTrace`, `Estimate.coefficients`, `ReportComp`, `ReportPayload`.
- `mcp_server/derivation.py` (modify) — `Derivation` carries `pairs`/`groups`/`regression`; `derive_time_trend`, `derive_marginal_ppsf` (unify to median), `derive_feature_unit` populate them.
- `mcp_server/estimate.py` (modify) — build `Estimate.coefficients` from the derivations via a `_coeff` helper.
- `mcp_server/report.py` (create) — pure `render_report_html(payload) -> str` + `slug()` helper + static project-warning constants.
- `mcp_server/server.py` (modify) — `Tools.render_report` (file IO) + FastMCP `render_report` tool.
- `tests/test_derivation.py` (modify) — assert traces populated.
- `tests/test_estimate.py` (modify) — assert `coefficients` present and consistent.
- `tests/test_report.py` (create) — render a fixture payload, assert sections/ordering/self-containment.
- `tests/test_server.py` (modify) — `render_report` writes a file and returns an absolute path.
- `.gitignore` (modify) — ignore `reports/`.
- `skill/comp-analysis/SKILL.md` (modify) — add final step 7 + payload field guidance.

---

## Task 1: Add report + trace models

**Files:**
- Modify: `mcp_server/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_coefficient_trace_and_report_payload_models():
    from datetime import date
    from mcp_server.models import (
        PairTrace, CoefficientTrace, Estimate, CompAdjustment,
        ReportComp, ReportPayload, Subject, Comp,
    )
    pt = PairTrace(comp_a="A", comp_b="B", detail="Δ$10,000 over 100 sqft", value=100.0)
    ct = CoefficientTrace(
        factor="size", method="matched_pair", source_type="article-method",
        value=284.0, is_pct=False, confidence="high",
        equation="per-sqft $ = median of Δprice / Δsqft", pairs=[pt],
        aggregate="median of 1 pair = $284/sqft", summary="1 matched pair",
    )
    est = Estimate(point=500_000, low=480_000, high=520_000, confidence="high",
                   per_comp=[], coefficients=[ct])
    assert est.coefficients[0].pairs[0].comp_a == "A"
    subj = Subject(address="S", sqft=1800)
    comp = Comp(address="C", lat=51.0, lng=-114.0, sold_price=500_000,
                sold_date=date(2026, 5, 1), sqft=1800)
    payload = ReportPayload(
        subject=subj, comps=[ReportComp(comp=comp, kept=True)], estimate=est,
        confidence_reasoning="Tight cluster.", target_warnings=["Subject's own sale in pool."],
        verify_next=["Confirm basement."], as_of=date(2026, 6, 10),
    )
    assert payload.comps[0].kept is True
    assert payload.estimate.coefficients[0].value == 284.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models.py::test_coefficient_trace_and_report_payload_models -v`
Expected: FAIL with `ImportError`/`cannot import name 'PairTrace'`.

- [ ] **Step 3: Add the models**

In `mcp_server/models.py`, after the `Adjustment` class add:

```python
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
```

In the `Estimate` class, add the field (keep the others unchanged):

```python
    coefficients: list[CoefficientTrace] = Field(default_factory=list)
```

At the end of the file add:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_models.py::test_coefficient_trace_and_report_payload_models -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server/models.py tests/test_models.py
git commit -m "feat(models): coefficient traces + report payload"
```

---

## Task 2: Carry trace data on Derivation + enrich time trend

**Files:**
- Modify: `mcp_server/derivation.py`
- Test: `tests/test_derivation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_derivation.py`:

```python
def test_time_trend_emits_pair_traces():
    recent = [_comp(860_000, d=date(2026, 6, 1), addr="r1"),
              _comp(862_000, d=date(2026, 5, 1), addr="r2")]
    older = [_comp(800_000, d=date(2025, 12, 1), addr="o1"),
             _comp(804_000, d=date(2026, 1, 1), addr="o2")]
    dv = derive_time_trend(_subject(sqft=2000), recent + older, as_of=AS_OF, clamp=0.02)
    assert dv.method == "matched_pair"
    assert len(dv.pairs) >= 1
    assert dv.pairs[0].comp_a and dv.pairs[0].comp_b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_derivation.py::test_time_trend_emits_pair_traces -v`
Expected: FAIL with `AttributeError: 'Derivation' object has no attribute 'pairs'`.

- [ ] **Step 3: Extend Derivation + time trend**

In `mcp_server/derivation.py`, update the imports and `Derivation`:

```python
from dataclasses import dataclass, field
```

```python
from mcp_server.models import (
    Subject, Comp, Disclosure, AdjMethod, SourceType, Confidence, PairTrace,
)
```

```python
@dataclass
class Derivation:
    value: float
    method: AdjMethod
    source_type: SourceType
    evidence: str
    confidence: Confidence
    pairs: list[PairTrace] = field(default_factory=list)
    groups: Optional[dict] = None
    regression: Optional[dict] = None
```

In `derive_time_trend`, replace the Rung-1 loop and its return so it records pairs (keep the size-tolerance + clamp logic identical):

```python
    # Rung 1: size-matched pairs across time.
    SIZE_TOL = 0.05
    rates: list[float] = []
    pairs: list[PairTrace] = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = comps[i], comps[j]
            big = max(a.sqft, b.sqft)
            if big == 0 or abs(a.sqft - b.sqft) / big > SIZE_TOL:
                continue
            mi, mj = months[i], months[j]
            if mi == mj:
                continue
            (older, om), (recent, rm) = ((a, mi), (b, mj)) if mi > mj else ((b, mj), (a, mi))
            op, rp = older.price_per_sqft, recent.price_per_sqft
            if op <= 0:
                continue
            rate = ((rp - op) / op) / (om - rm)
            rates.append(rate)
            pairs.append(PairTrace(
                comp_a=recent.address, comp_b=older.address,
                detail=f"${rp:.0f}/sqft ({rm:.0f} mo) vs ${op:.0f}/sqft ({om:.0f} mo), {om-rm:.0f} mo apart",
                value=round(rate, 5)))
    if rates:
        raw = median(rates)
        per_month = _clamp(raw, clamp)
        conf = "low" if per_month != raw else ("high" if len(rates) >= 2 else "medium")
        return Derivation(round(per_month, 5), "matched_pair", "article-method",
                          f"{len(rates)} size-matched pair(s) across time", conf, pairs=pairs)
```

In the same function, update the grouping return to attach `groups` (replace the existing grouping `return`):

```python
            return Derivation(round(per_month, 5), "grouping", "article-method", ev, conf,
                              groups={"recent_ppsf": round(rp), "recent_mo": round(rm),
                                      "older_ppsf": round(op), "older_mo": round(om),
                                      "gap_mo": round(gap)})
```

And the regression return:

```python
    return Derivation(round(per_month, 5), "regression", "article-method",
                      f"least-squares on size-normalized $/sqft ({n} comps, small-N)", "low",
                      regression={"n": n, "slope_per_mo": round(per_month, 5)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_derivation.py -v`
Expected: PASS (new test + all existing derivation tests).

- [ ] **Step 5: Commit**

```bash
git add mcp_server/derivation.py tests/test_derivation.py
git commit -m "feat(derivation): time-trend pair traces; Derivation carries trace data"
```

---

## Task 3: Unify size coefficient to median-of-pairs + traces

**Files:**
- Modify: `mcp_server/derivation.py`
- Test: `tests/test_derivation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_derivation.py`:

```python
def test_marginal_ppsf_uses_median_of_all_pairs_with_traces():
    s = _subject(sqft=1800)
    # three matched pairs alike except size (>=8% apart), implying ~$50-60/sqft
    comps = [_comp(700_000, sqft=1800, addr="a"), _comp(710_000, sqft=2000, addr="b"),
             _comp(702_000, sqft=1800, addr="c"), _comp(715_000, sqft=2000, addr="d"),
             _comp(704_000, sqft=1800, addr="e"), _comp(719_000, sqft=2000, addr="f")]
    prices = [c.sold_price for c in comps]
    dv = derive_marginal_ppsf(s, comps, prices)
    assert dv.method == "matched_pair"
    assert len(dv.pairs) >= 3          # all qualifying pairs recorded, not just the first
    from statistics import median
    assert dv.value == round(median([p.value for p in dv.pairs]), 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_derivation.py::test_marginal_ppsf_uses_median_of_all_pairs_with_traces -v`
Expected: FAIL (current code returns the first pair; `len(dv.pairs)` is 0 / value ≠ median).

- [ ] **Step 3: Rewrite `_matched_pair_ppsf` to collect all pairs**

In `mcp_server/derivation.py`, replace the whole `_matched_pair_ppsf` function with:

```python
def _matched_pair_ppsf(subject: Subject, comps: list[Comp], prices: list[float]) -> Optional[Derivation]:
    """Comps alike except sqft (>=8% apart, same beds/baths/garage) -> Δprice/Δsqft.
    Collects EVERY qualifying pair and uses their median (uniform with time/features)."""
    n = len(comps)
    rates: list[float] = []
    pairs: list[PairTrace] = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = comps[i], comps[j]
            dsqft = a.sqft - b.sqft
            if a.sqft == 0 or b.sqft == 0 or abs(dsqft) / max(a.sqft, b.sqft) < 0.08:
                continue
            if (a.beds, a.baths, a.garage) != (b.beds, b.baths, b.garage):
                continue
            rate = (prices[i] - prices[j]) / dsqft
            if 0 < rate < 1000:
                rates.append(rate)
                pairs.append(PairTrace(
                    comp_a=a.address, comp_b=b.address,
                    detail=f"Δ${prices[i]-prices[j]:,.0f} over {dsqft:+.0f} sqft",
                    value=round(rate, 2)))
    if rates:
        rate = median(rates)
        conf = "high" if len(rates) >= 2 else "medium"
        return Derivation(round(rate, 2), "matched_pair", "article-method",
                          f"{len(rates)} matched pair(s); per-sqft median ${rate:.0f}",
                          conf, pairs=pairs)
    return None
```

In `derive_marginal_ppsf`, update the grouping return to attach `groups`:

```python
                return Derivation(round(rate, 2), "grouping", "article-method",
                                  f"larger half median ${lp:.0f}@{ls:.0f}sqft vs smaller "
                                  f"${sp:.0f}@{ss:.0f}sqft", "medium",
                                  groups={"large_med_price": round(lp), "large_med_sqft": round(ls),
                                          "small_med_price": round(sp), "small_med_sqft": round(ss),
                                          "rate_per_sqft": round(rate, 2)})
```

And the regression return:

```python
        return Derivation(round(slope, 2), "regression", "article-method",
                          f"slope of price~sqft over {len(comps)} comps", "low",
                          regression={"n": len(comps), "slope_per_sqft": round(slope, 2)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_derivation.py -v`
Expected: PASS (new test + existing).

- [ ] **Step 5: Commit**

```bash
git add mcp_server/derivation.py tests/test_derivation.py
git commit -m "feat(derivation): unify size to median-of-pairs with full pair traces"
```

---

## Task 4: Feature pair traces

**Files:**
- Modify: `mcp_server/derivation.py`
- Test: `tests/test_derivation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_derivation.py`:

```python
def test_feature_unit_emits_pair_traces():
    s = _subject(garage=2)
    comps = [_comp(700_000, sqft=1800, garage=1, addr="a"),
             _comp(712_000, sqft=1800, garage=2, addr="b"),
             _comp(705_000, sqft=1850, garage=1, addr="c"),
             _comp(718_000, sqft=1850, garage=2, addr="d")]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "garage")
    assert dv.method == "matched_pair"
    assert len(dv.pairs) >= 1
    assert "garage" in dv.pairs[0].detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_derivation.py::test_feature_unit_emits_pair_traces -v`
Expected: FAIL (`dv.pairs` empty).

- [ ] **Step 3: Record pairs in `derive_feature_unit`**

In `mcp_server/derivation.py`, in `derive_feature_unit` replace the Rung-1 loop and its return:

```python
    # Rung 1: matched pairs — control for the confounds by selection, value ONE unit.
    n = len(comps)
    rates: list[float] = []
    pairs: list[PairTrace] = []
    for i in range(n):
        for j in range(i + 1, n):
            fa, fb = getattr(comps[i], factor), getattr(comps[j], factor)
            if fa is None or fb is None or fa == fb or not _alike_except(comps[i], comps[j], factor):
                continue
            rate = (residuals[i] - residuals[j]) / (fa - fb)
            if 0 < rate <= cap:
                rates.append(rate)
                pairs.append(PairTrace(
                    comp_a=comps[i].address, comp_b=comps[j].address,
                    detail=f"Δresidual ${residuals[i]-residuals[j]:,.0f} over {fa-fb:+g} {factor}",
                    value=round(rate, 2)))
    if rates:
        per_unit = median(rates)
        conf = "high" if len(rates) >= 3 else "medium"
        return Derivation(round(per_unit, 2), "matched_pair", "article-method",
                          f"{factor}: {len(rates)} matched pair(s) alike except {factor}; "
                          f"per-unit median ${per_unit:.0f}", conf, pairs=pairs)
```

Update the grouping return to attach `groups`:

```python
            if 0 < per_unit <= cap:
                return Derivation(round(per_unit, 2), "grouping", "article-method",
                                  f"{factor}: {hk:g}-count median ${hr:.0f} vs {lk:g}-count "
                                  f"${lr:.0f}", "low",
                                  groups={"hi_count": hk, "hi_resid": round(hr),
                                          "lo_count": lk, "lo_resid": round(lr),
                                          "per_unit": round(per_unit, 2)})
```

And the regression return:

```python
    if slope is not None and 0 < slope <= cap:
        return Derivation(round(slope, 2), "regression", "article-method",
                          f"slope of residual~{factor} over {len(known)} comps", "low",
                          regression={"n": len(known), "slope": round(slope, 2)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_derivation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server/derivation.py tests/test_derivation.py
git commit -m "feat(derivation): feature-coefficient pair traces"
```

---

## Task 5: Assemble Estimate.coefficients in reconcile

**Files:**
- Modify: `mcp_server/estimate.py`
- Test: `tests/test_estimate.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_estimate.py` (reuse that file's existing `_subject`/`_comp` helpers; if absent, import from `tests.test_derivation`):

```python
def test_estimate_exposes_coefficient_traces():
    from datetime import date
    from mcp_server.models import Subject, Comp, AdjustmentRules
    from mcp_server.estimate import reconcile
    s = Subject(address="S", lat=51.05, lng=-114.08, sqft=1800, year_built=1985,
                beds=3, baths=2, garage=2)
    comps = [Comp(address=a, lat=51.05, lng=-114.08, sold_price=p, sold_date=date(2026, 5, 1),
                  sqft=sq, year_built=1985, beds=3, baths=2, garage=g)
             for a, p, sq, g in [("a", 700_000, 1800, 1), ("b", 712_000, 1800, 2),
                                 ("c", 705_000, 2000, 1), ("d", 718_000, 2000, 2)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 1))
    factors = [c.factor for c in est.coefficients]
    assert factors == ["time", "size", "beds", "baths", "garage"]
    size = next(c for c in est.coefficients if c.factor == "size")
    assert size.is_pct is False and size.value == size.value  # present, numeric
    time = next(c for c in est.coefficients if c.factor == "time")
    assert time.is_pct is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_estimate.py::test_estimate_exposes_coefficient_traces -v`
Expected: FAIL (`est.coefficients` is empty → `next(...)` raises `StopIteration`).

- [ ] **Step 3: Build coefficients in reconcile**

In `mcp_server/estimate.py`, update the imports to include the trace model:

```python
from mcp_server.models import (
    Subject, Comp, AdjustmentRules, Overrides, Adjustment, CompAdjustment,
    Estimate, Confidence, CoefficientTrace,
)
```

Add this helper near `_adj` (before `reconcile`):

```python
def _coeff(factor: str, dv: Derivation, *, is_pct: bool, unit: Optional[str] = None) -> CoefficientTrace:
    n = len(dv.pairs)
    if is_pct:
        equation = ("monthly % = median of ((p_recent − p_older)/p_older) / Δmonths "
                    "over size-matched pairs")
        aggregate = f"median of {n} size-matched pair(s) = {dv.value*100:+.3f}%/mo" if n else dv.evidence
    elif unit:
        equation = f"per-{unit} $ = median of Δresidual / Δcount over pairs alike except {factor}"
        aggregate = f"median of {n} matched pair(s) = ${dv.value:,.0f}/{unit}" if n else dv.evidence
    else:
        equation = "per-sqft $ = median of Δprice / Δsqft over matched pairs"
        aggregate = f"median of {n} matched pair(s) = ${dv.value:,.0f}/sqft" if n else dv.evidence
    return CoefficientTrace(
        factor=factor, method=dv.method, source_type=dv.source_type, value=dv.value,
        is_pct=is_pct, confidence=dv.confidence, equation=equation, pairs=dv.pairs,
        groups=dv.groups, regression=dv.regression, aggregate=aggregate, summary=dv.evidence)
```

In `reconcile`, just before the final `return Estimate(...)`, build the list:

```python
    coefficients = [
        _coeff("time", time, is_pct=True),
        _coeff("size", size, is_pct=False),
        _coeff("beds", feats["beds"], is_pct=False, unit="bed"),
        _coeff("baths", feats["baths"], is_pct=False, unit="bath"),
        _coeff("garage", feats["garage"], is_pct=False, unit="garage"),
    ]
```

Change the final return to pass them:

```python
    return Estimate(point=point, low=low, high=high, confidence=conf, per_comp=per_comp,
                    coefficients=coefficients,
                    disclosures=compute_disclosures(subject, comps, as_of=as_of), method_notes=notes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_estimate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server/estimate.py tests/test_estimate.py
git commit -m "feat(estimate): expose per-factor coefficient traces"
```

---

## Task 6: Full engine regression gate

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — confirms the size→median-of-pairs change did not break any engine test (all assertions use tolerance ranges).

- [ ] **Step 2: Re-run the comp-verify golden set (manual, Desktop-faithful)**

Invoke the `comp-verify` skill (needs the `kv-comp-analysis` MCP connected). Confirm the pass count is **≥ 7/10** (the pre-change baseline). If it drops, STOP and report the regression before continuing — do not proceed to the renderer until the engine change is cleared.

- [ ] **Step 3: No commit** (verification gate only).

---

## Task 7: Pure HTML renderer

**Files:**
- Create: `mcp_server/report.py`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_report.py`:

```python
from datetime import date
from mcp_server.models import Subject, Comp, AdjustmentRules, ReportComp, ReportPayload
from mcp_server.estimate import reconcile
from mcp_server.report import render_report_html, slug


def _payload():
    s = Subject(address="138 Cranberry Place SE", lat=51.0, lng=-114.0, sqft=1416,
                year_built=2007, beds=3, baths=3, garage=1, community="Cranston",
                property_type="detached",
                provenance={"sqft": "honestdoor", "year_built": "honestdoor"})
    comps = [Comp(address=a, lat=51.0, lng=-114.0, sold_price=p, sold_date=date(2026, 4, 1),
                  sqft=sq, year_built=2007, beds=3, baths=3, garage=g, distance_km=d,
                  include_reason="same community")
             for a, p, sq, g, d in [("71 Cranberry Pl", 536_500, 1429, 2, 0.1),
                                    ("78 Cranberry Cl", 560_000, 1425, 2, 0.3),
                                    ("420 Cranberry Cir", 535_000, 1356, 2, 0.2),
                                    ("389 Cranberry Cir", 558_500, 1358, 2, 0.2)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 10))
    rcomps = [ReportComp(comp=c, kept=True) for c in comps]
    rcomps.append(ReportComp(comp=Comp(address="56 Cranbrook Landing", lat=51.0, lng=-114.0,
                  sold_price=1_173_000, sold_date=date(2026, 4, 1), sqft=1540),
                  kept=False, exclude_reason="lakefront outlier"))
    return ReportPayload(subject=s, comps=rcomps, estimate=est,
                         confidence_reasoning="Tight same-street cluster.",
                         target_warnings=["Subject's own sale appears in the pool."],
                         verify_next=["Confirm basement development."], as_of=date(2026, 6, 10))


def test_slug():
    assert slug("138 Cranberry Place SE") == "138-cranberry-place-se"


def test_render_has_all_sections_and_value():
    html = render_report_html(_payload())
    for token in ["138 Cranberry Place SE", "Baseline", "Confidence", "Comparable",
                  "Adjustment", "Disclosure", "Not in this number", "What I'd verify next"]:
        assert token in html


def test_render_orders_target_warnings_before_project_warnings():
    html = render_report_html(_payload())
    assert "Subject&#x27;s own sale appears in the pool." in html or \
           "Subject's own sale appears in the pool." in html
    assert html.index("own sale appears") < html.index("No location")


def test_render_is_self_contained_no_external_refs():
    html = render_report_html(_payload())
    assert "src=" not in html and "http://" not in html and "https://" not in html
    assert "<details" in html  # interactive tiles present


def test_render_shows_pair_traces_in_tiles():
    html = render_report_html(_payload())
    assert "Δ" in html  # arithmetic detail rendered
    assert "median of" in html  # aggregate line rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.report'`.

- [ ] **Step 3: Create `mcp_server/report.py`**

```python
from __future__ import annotations
import html
import re
from mcp_server.models import ReportPayload, CoefficientTrace, Estimate, Subject

CONF_COLOR = {"high": "#1a7f37", "medium": "#9a6700", "low": "#b42318"}

# Project-level disclaimers — identical every run, so they live here, not in the payload.
PROJECT_WARNINGS = [
    ("Baseline value only",
     "This figure is a comps-derived baseline. It excludes condition, rehab/repair, deferred "
     "maintenance and transaction fees, and may need further feature-level adjustments. Adjust "
     "it up or down for property-specific factors before relying on it."),
    ("No location / community adjustment",
     "This analysis assumes location does not affect value within the 3 km search radius. In real "
     "North American markets community matters a great deal; a per-community adjustment is a focus "
     "of future work."),
    ("Market scope: AB / BC",
     "The system is calibrated on Alberta (Calgary) sales. It can run on other regions, but "
     "accuracy outside AB / BC may be lower than tested."),
    ("Data limitations",
     "Sold history is drawn from a ~180-day, per-community window, and only sold transactions are "
     "used as comps. Any third-party AVM is an estimate, not a sale."),
]


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "—"


def _pct(v) -> str:
    return f"{v*100:+.2f}%" if v is not None else "—"


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "report"


def _subject_section(s: Subject) -> str:
    prov = s.provenance or {}
    rows = [
        ("Community", s.community, "community"),
        ("Property type", s.property_type, "property_type"),
        ("Size", f"{s.sqft:,.0f} sqft" if s.sqft else None, "sqft"),
        ("Year built", s.year_built, "year_built"),
        ("Beds", s.beds, "beds"),
        ("Baths", s.baths, "baths"),
        ("Garage", s.garage, "garage"),
        ("Lot", f"{s.lot_sf:,.0f} sf" if s.lot_sf else None, "lot_sf"),
    ]
    body = "".join(
        f"<tr><th>{_esc(label)}</th><td>{_esc(val) if val is not None else '—'}</td>"
        f"<td class='prov'>{_esc(prov.get(key, ''))}</td></tr>"
        for label, val, key in rows)
    return f"<section><h2>Subject</h2><table class='kv'>{body}</table></section>"


def _warnings_section(target: list[str]) -> str:
    tgt = "".join(f"<div class='warn target'>{_esc(w)}</div>" for w in target)
    proj = "".join(
        f"<div class='warn project'><strong>{_esc(t)}.</strong> {_esc(b)}</div>"
        for t, b in PROJECT_WARNINGS)
    return f"<section class='warnings'><h2>Warnings</h2>{tgt}{proj}</section>"


def _comp_row(rc) -> str:
    c = rc.comp
    dist = f"{c.distance_km:.1f} km" if c.distance_km is not None else "—"
    return (f"<tr><td>{_esc(c.address)}</td><td>{_money(c.sold_price)}</td>"
            f"<td>{_esc(c.sold_date)}</td><td>{c.sqft:,.0f}</td>"
            f"<td>${c.price_per_sqft:,.0f}</td><td>{dist}</td>"
            f"<td>{_esc(c.include_reason or '')}</td></tr>")


def _comps_section(comps) -> str:
    kept = [rc for rc in comps if rc.kept]
    kept.sort(key=lambda rc: (rc.comp.distance_km is None, rc.comp.distance_km or 0))
    excluded = [rc for rc in comps if not rc.kept]
    head = ("<thead><tr><th>Address</th><th>Sold</th><th>Date</th><th>Sqft</th>"
            "<th>$/sqft</th><th>Dist</th><th>Why included</th></tr></thead>")
    top = "".join(_comp_row(rc) for rc in kept[:10])
    out = [f"<section><h2>Comparable sales</h2>"
           f"<p class='muted'>{len(kept)} comps used (closest 10 shown).</p>"
           f"<table class='comps'>{head}<tbody>{top}</tbody></table>"]
    if len(kept) > 10:
        rest = "".join(_comp_row(rc) for rc in kept[10:])
        out.append(f"<details><summary>Show {len(kept)-10} more comps</summary>"
                   f"<table class='comps'>{head}<tbody>{rest}</tbody></table></details>")
    if excluded:
        exrows = "".join(
            f"<tr><td>{_esc(rc.comp.address)}</td><td>{_money(rc.comp.sold_price)}</td>"
            f"<td>${rc.comp.price_per_sqft:,.0f}</td><td>{_esc(rc.exclude_reason or '')}</td></tr>"
            for rc in excluded)
        out.append(f"<details><summary>Excluded ({len(excluded)})</summary>"
                   f"<table class='comps'><thead><tr><th>Address</th><th>Sold</th>"
                   f"<th>$/sqft</th><th>Reason excluded</th></tr></thead>"
                   f"<tbody>{exrows}</tbody></table></details>")
    return "".join(out) + "</section>"


def _trace_table(c: CoefficientTrace) -> str:
    if c.pairs:
        rows = "".join(
            f"<tr><td>{_esc(p.comp_a)}</td><td>{_esc(p.comp_b)}</td><td>{_esc(p.detail)}</td>"
            f"<td>{_pct(p.value) if c.is_pct else _money(p.value)}</td></tr>" for p in c.pairs)
        return ("<table class='trace'><thead><tr><th>Comp A</th><th>Comp B</th>"
                f"<th>Arithmetic</th><th>Implies</th></tr></thead><tbody>{rows}</tbody></table>")
    src = c.groups or c.regression
    if src:
        rows = "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in src.items())
        return f"<table class='trace'><tbody>{rows}</tbody></table>"
    return f"<p class='muted'>{_esc(c.summary)}</p>"


def _applied_table(factor: str, per_comp) -> str:
    rows = []
    for ca in per_comp:
        for a in ca.adjustments:
            if a.factor == factor and (a.value_dollar or a.value_pct):
                amt = _pct(a.value_pct) if a.value_pct is not None else _money(a.value_dollar)
                rows.append(f"<tr><td>{_esc(ca.address)}</td><td>{amt}</td>"
                            f"<td>{_esc(a.evidence)}</td></tr>")
    if not rows:
        return ""
    return ("<h4>Applied to comps</h4><table class='trace'><thead><tr><th>Comp</th>"
            "<th>Adjustment</th><th>Basis</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def _coeff_tile(c: CoefficientTrace, per_comp) -> str:
    if c.value == 0:
        val = "not adjusted"
    elif c.is_pct:
        val = f"{c.value*100:+.3f}%/mo"
    else:
        val = f"${c.value:,.0f}"
    chip = f"<span class='chip {_esc(c.confidence)}'>{_esc(c.confidence)}</span>"
    body = (f"<p class='eq'>{_esc(c.equation)}</p>{_trace_table(c)}"
            f"<p class='agg'>{_esc(c.aggregate)}</p>{_applied_table(c.factor, per_comp)}")
    return (f"<details class='tile'><summary><span class='factor'>{_esc(c.factor)}</span>"
            f"<span class='val'>{_esc(val)}</span><span class='method'>{_esc(c.method)}</span>"
            f"{chip}</summary><div class='tilebody'>{body}</div></details>")


def _adjustments_section(est: Estimate) -> str:
    tiles = "".join(_coeff_tile(c, est.per_comp) for c in est.coefficients)
    return (f"<section><h2>Adjustments</h2>"
            f"<p class='muted'>Click a tile to see the comps and arithmetic behind each number.</p>"
            f"<div class='tiles'>{tiles}</div></section>")


def _disclosures_section(est: Estimate) -> str:
    if not est.disclosures:
        return ""
    items = "".join(
        f"<div class='disc'><strong>{_esc(d.factor)}</strong> — {_esc(d.skew)} "
        f"(<em>likely {_esc(d.direction)}</em>). {_esc(d.caveat)}</div>" for d in est.disclosures)
    return f"<section><h2>Disclosures</h2>{items}</section>"


def _list_section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{_esc(x)}</li>" for x in items)
    return f"<section><h2>{_esc(title)}</h2><ul>{lis}</ul></section>"


_CSS = """
:root{--ink:#1f2933;--muted:#667085;--line:#e4e7ec;--bg:#f5f6f8;--card:#fff;--accent:#1e3a5f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:32px 20px 64px}
header.hero{background:var(--accent);color:#fff;border-radius:14px;padding:28px 32px;margin-bottom:24px}
header.hero .addr{font-size:14px;opacity:.85;letter-spacing:.02em}
header.hero .value{font-size:42px;font-weight:700;margin:6px 0}
header.hero .range{font-size:15px;opacity:.9}
.badge{display:inline-block;padding:4px 12px;border-radius:999px;font-size:13px;font-weight:600;
color:#fff;margin-top:10px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px 24px;margin:16px 0}
h2{font-size:18px;margin:0 0 14px}h4{margin:14px 0 6px;font-size:14px}
.muted{color:var(--muted);font-size:13px;margin:0 0 12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
table.kv th{width:130px;color:var(--muted);font-weight:500}.prov{color:var(--muted);font-size:11px}
.warnings .warn{border-radius:9px;padding:11px 14px;margin:8px 0;font-size:13px}
.warn.target{background:#fff4ed;border:1px solid #f9b98a;color:#8a3b12}
.warn.project{background:#eef4fb;border:1px solid #c5d9ef;color:#234a73}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
details.tile{border:1px solid var(--line);border-radius:10px;background:#fafbfc;overflow:hidden}
details.tile>summary{list-style:none;cursor:pointer;padding:12px 14px;display:flex;
flex-wrap:wrap;align-items:center;gap:8px}
details.tile>summary::-webkit-details-marker{display:none}
.factor{font-weight:600;text-transform:capitalize}.val{font-variant-numeric:tabular-nums;
font-weight:700;color:var(--accent)}.method{font-size:11px;color:var(--muted)}
.chip{margin-left:auto;font-size:11px;padding:2px 9px;border-radius:999px;color:#fff}
.chip.high{background:#1a7f37}.chip.medium{background:#9a6700}.chip.low{background:#b42318}
.tilebody{padding:0 14px 14px;border-top:1px solid var(--line)}
.eq{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:#f2f4f7;
padding:8px 10px;border-radius:7px;color:#344054}
.agg{font-weight:600;font-size:13px;margin:10px 0}
table.trace td:last-child{font-variant-numeric:tabular-nums;white-space:nowrap}
.disc{font-size:13px;padding:9px 0;border-bottom:1px solid var(--line)}.disc:last-child{border:0}
ul{margin:0;padding-left:20px;font-size:13px}li{margin:4px 0}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:20px}
@media print{body{background:#fff}section,details.tile{break-inside:avoid}}
"""


def render_report_html(payload: ReportPayload) -> str:
    s, est = payload.subject, payload.estimate
    color = CONF_COLOR.get(est.confidence, "#667085")
    addr = s.resolved_address or s.address
    drivers = est.method_notes[-1] if est.method_notes else ""
    hero = (f"<header class='hero'><div class='addr'>{_esc(addr)}</div>"
            f"<div class='value'>{_money(est.point)}</div>"
            f"<div class='range'>Range {_money(est.low)} – {_money(est.high)} "
            f"(25th–75th percentile of adjusted comps)</div>"
            f"<span class='badge' style='background:{color}'>"
            f"{_esc(est.confidence)} confidence</span></header>")
    conf = (f"<section><h2>Confidence &amp; reasoning</h2>"
            f"<p>{_esc(payload.confidence_reasoning)}</p>"
            f"<p class='muted'>{_esc(drivers)}</p></section>")
    body = "".join([
        hero,
        _warnings_section(payload.target_warnings),
        _subject_section(s),
        conf,
        _comps_section(payload.comps),
        _adjustments_section(est),
        _disclosures_section(est),
        _list_section("Not in this number", [
            "Condition, rehab and deferred maintenance are out of scope.",
            "Mark the baseline down for repairs, or up for recent renovation, before use.",
        ]),
        _list_section("What I'd verify next", payload.verify_next),
        f"<footer>Source: HonestDoor sold history (~180-day window). "
        f"Generated {_esc(payload.as_of)}. Scope: AB / BC, calibrated on Calgary.</footer>",
    ])
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Comp report — {_esc(addr)}</title><style>{_CSS}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_report.py -v`
Expected: PASS (all five tests).

- [ ] **Step 5: Commit**

```bash
git add mcp_server/report.py tests/test_report.py
git commit -m "feat(report): self-contained interactive HTML renderer"
```

---

## Task 8: render_report MCP tool

**Files:**
- Modify: `mcp_server/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py` (it already builds `Tools` via stubs; reuse the existing `tools`/`build_tools` pattern in that file — this test builds `Tools` directly):

```python
def test_render_report_writes_file(tmp_path):
    from datetime import date
    from mcp_server.server import Tools
    from mcp_server.models import (
        Subject, Comp, AdjustmentRules, ReportComp, ReportPayload,
    )
    from mcp_server.estimate import reconcile
    s = Subject(address="138 Cranberry Place SE", resolved_address="138 Cranberry Place SE",
                lat=51.0, lng=-114.0, sqft=1416, year_built=2007, beds=3, baths=3, garage=1)
    comps = [Comp(address=a, lat=51.0, lng=-114.0, sold_price=p, sold_date=date(2026, 4, 1),
                  sqft=sq, year_built=2007, beds=3, baths=3, garage=2, distance_km=0.2)
             for a, p, sq in [("71 Cranberry", 536_500, 1429), ("78 Cranberry", 560_000, 1425),
                              ("420 Cranberry", 535_000, 1356), ("389 Cranberry", 558_500, 1358)]]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026, 6, 10))
    payload = ReportPayload(subject=s, comps=[ReportComp(comp=c) for c in comps], estimate=est,
                            confidence_reasoning="ok", as_of=date(2026, 6, 10))
    tools = Tools(source=None, as_of=date(2026, 6, 10))
    path = tools.render_report(payload, out_dir=str(tmp_path))
    import os
    assert os.path.isabs(path) and os.path.exists(path)
    assert path.endswith("138-cranberry-place-se-2026-06-10.html")
    assert "<details" in open(path, encoding="utf-8").read()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_server.py::test_render_report_writes_file -v`
Expected: FAIL with `AttributeError: 'Tools' object has no attribute 'render_report'`.

- [ ] **Step 3: Add the Tools method + FastMCP tool**

In `mcp_server/server.py`, extend the imports:

```python
import os
from mcp_server.models import (
    Subject, FindCompsResult, Estimate, CrossCheck, Criteria, AdjustmentRules, Overrides,
    ReportPayload,
)
from mcp_server.report import render_report_html, slug
```

Add this method to the `Tools` dataclass (after `cross_check`):

```python
    def render_report(self, payload: ReportPayload, out_dir: str = "reports") -> str:
        """Write the self-contained HTML report to disk; return its absolute path."""
        os.makedirs(out_dir, exist_ok=True)
        name = slug(payload.subject.resolved_address or payload.subject.address)
        path = os.path.abspath(os.path.join(out_dir, f"{name}-{payload.as_of}.html"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_report_html(payload))
        return path
```

In `main()`, register the tool after `cross_check`:

```python
    @mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": True,
                           "openWorldHint": False, "title": "Render HTML report"})
    def render_report(payload: dict) -> dict:
        """Render the self-contained, interactive HTML comp report to disk and return its
        absolute path. Call this as the FINAL step, once the value is settled (address
        confirmed, any overrides applied). `payload` carries: subject, comps (each
        {comp, kept, exclude_reason}) including excluded ones, the full estimate object
        from estimate_value (with coefficients), plus agent-authored confidence_reasoning,
        target_warnings (subject-specific, shown first) and verify_next. Surface the
        returned path and a file:// link so the user can open it in a browser."""
        payload.setdefault("as_of", tools.as_of.isoformat())
        return {"path": tools.render_report(ReportPayload(**payload))}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server/server.py tests/test_server.py
git commit -m "feat(server): render_report MCP tool writes self-contained HTML"
```

---

## Task 9: Gitignore the reports output dir

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Append the ignore rule**

Add a line to `.gitignore`:

```
reports/
```

- [ ] **Step 2: Verify**

Run: `printf 'x' > reports/_probe.html && git status --porcelain reports/ ; rm -rf reports/`
Expected: no output (the `reports/` path is ignored).

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore generated reports/"
```

---

## Task 10: Wire the report into the comp-analysis skill

**Files:**
- Modify: `skill/comp-analysis/SKILL.md`

- [ ] **Step 1: Add the final workflow step**

In `skill/comp-analysis/SKILL.md`, in the `## Workflow` list, after step 5 ("Present the file") add:

```markdown
6. **`render_report(payload)`** — as the FINAL step, once the value is settled (address
   confirmed, any `overrides` applied). Assemble `payload` from the estimate plus your
   narrative, then surface the returned path and a `file://` link so the user can open the
   interactive report in a browser. Re-running after an override overwrites the same file.
```

- [ ] **Step 2: Document the payload fields**

After the `## Output — "the file"` section, add:

```markdown
## The HTML report (`render_report`)

Build `payload` and call `render_report`:
- `subject` — the confirmed subject object.
- `comps` — a list of `{comp, kept, exclude_reason}`: every comp you considered, with
  `kept: false` + a reason for the ones you curated out (so the report shows them).
- `estimate` — the object returned by `estimate_value` verbatim (it carries `coefficients`
  with the per-factor derivation traces the report renders into expandable tiles).
- `confidence_reasoning` — your one-paragraph "why" for the confidence.
- `target_warnings` — subject-specific cautions (e.g. "subject's own recent sale is in the
  pool", "semi subject vs. detached comps"). These render FIRST, above the standard
  project-level disclaimers (which the renderer adds automatically — do not repeat them).
- `verify_next` — your "what I'd verify next" bullets.

Then post the returned path to the user, e.g.:
`✅ Interactive report: /abs/path/138-cranberry-place-se-2026-06-10.html`
`[open report](file:///abs/path/138-cranberry-place-se-2026-06-10.html)`
```

- [ ] **Step 3: Commit**

```bash
git add skill/comp-analysis/SKILL.md
git commit -m "docs(skill): wire render_report as final comp-analysis step"
```

---

## Self-Review Notes

- **Spec coverage:** report-1 (baseline value) → hero in Task 7; report-2 (confidence + reasoning)
  → `conf` section Task 7; report-3 (closest 10, collapse rest) → `_comps_section` Task 7;
  report-4/4a/4b (adjustment tiles, which comps, equation + steps) → Tasks 2-5 (traces) + Task 7
  (`_coeff_tile`/`_trace_table`/`_applied_table`); report-5/5a/5b/5c (disclosures + project
  warnings) → `PROJECT_WARNINGS` + `_disclosures_section`; report-5d (target-specific first) →
  `_warnings_section` order + `test_render_orders_target_warnings_before_project_warnings`;
  report-6 ("not in this number", verify-next, footer) → Task 7. Delivery (file + link) → Task 8;
  timing/wiring → Task 10; size-unify + regression gate → Tasks 3 + 6.
- **Placeholders:** none — all steps contain runnable code/commands.
- **Type consistency:** `PairTrace`/`CoefficientTrace`/`ReportComp`/`ReportPayload` field names are
  identical across models (Task 1), derivation (Tasks 2-4), estimate `_coeff` (Task 5), and
  renderer (Task 7). `Derivation` gains `pairs`/`groups`/`regression`; `_coeff` reads exactly those.
  `render_report_html`/`slug` names match between `report.py` and `server.py`.
```
