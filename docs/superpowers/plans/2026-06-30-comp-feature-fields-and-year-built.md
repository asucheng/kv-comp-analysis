# Comp Feature Fields + Year-Built Adjustment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface `style`/`basement`/`neighbourhood` on each comp for human review (no auto-adjustment), and add a data-derived Year-Built dollar adjustment.

**Architecture:** Part (a) extends the `getListings2` query + `Comp` model + `listing_to_comp` mapping + HTML comp table — display only. Part (c) reuses the existing matched-pair → grouping → regression ladder (`derive_feature_unit`, already generic) for `year_built`, capped, wired through `DerivedSet` / `apply_adjustments` / the coefficient list.

**Tech Stack:** Python 3.14, Pydantic models, `pytest`, `httpx` (HonestDoor GraphQL), `openpyxl` (not touched here).

## Global Constraints

- Branch `feat/comp-feature-fields-and-year-built` off `master`; its own PR. (Already created.)
- Run tests with `.venv/bin/python -m pytest`.
- New categorical fields are **display-only** — never fed into any number.
- `year_built` stays **out of** `_FEATS` in `mcp_server/derivation.py`.
- `_FEATURE_CAP["year_built"] = 4000.0` ($/yr sanity ceiling).
- Adjustment sequence after this work: time → size → beds → full_baths → half_baths → garage → year_built.
- Preserve API `"None"` basement strings as-is; convert empty-string neighbourhood to `None`.

---

### Task 1: Extract style / basement / neighbourhood onto `Comp`

**Files:**
- Modify: `mcp_server/models.py` (the `Comp` class, around line 52–68)
- Modify: `mcp_server/compsource/honestdoor.py` (`_LISTINGS_QUERY` ~line 18-26; add helper; `listing_to_comp` ~line 214-226)
- Test: `tests/test_honestdoor.py`

**Interfaces:**
- Produces: `Comp.style: Optional[str]`, `Comp.basement: Optional[str]`, `Comp.community: Optional[str]`; helper `_basement_display(details: dict) -> Optional[str]`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_honestdoor.py`:

```python
def test_listing_to_comp_maps_style_basement_neighbourhood():
    row = {"type": "SALE", "soldPrice": "550000", "soldDate": "2026-03-01T00:00:00Z",
           "address": {"streetNumber": "12", "streetName": "Elm St",
                       "city": "Calgary", "neighborhood": "Windsor Park"},
           "details": {"propertyType": "Detached", "style": "2-Storey",
                       "basement1": "Fin W/O", "basement2": "Sep Entrance"},
           "property": {"livingArea": "1500", "yearBuilt": 2005,
                        "location": {"lat": 51.0, "lon": -114.0}}}
    c = listing_to_comp(row)
    assert c.style == "2-Storey"
    assert c.basement == "Fin W/O — Sep Entrance"
    assert c.community == "Windsor Park"


def test_listing_to_comp_basement_and_neighbourhood_edge_cases():
    row = {"type": "SALE", "soldPrice": "500000", "soldDate": "2026-03-01T00:00:00Z",
           "address": {"streetNumber": "9", "streetName": "Oak Rd", "neighborhood": ""},
           "details": {"basement1": "None"},   # "None" is a real value (no basement)
           "property": {"livingArea": "1200", "location": {"lat": 51.0, "lon": -114.0}}}
    c = listing_to_comp(row)
    assert c.basement == "None"     # preserved, not coerced
    assert c.community is None       # empty string -> None
    assert c.style is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_honestdoor.py::test_listing_to_comp_maps_style_basement_neighbourhood tests/test_honestdoor.py::test_listing_to_comp_basement_and_neighbourhood_edge_cases -v`
Expected: FAIL — `Comp` has no attribute `style` (or `AttributeError`/validation).

- [ ] **Step 3a: Add the model fields**

In `mcp_server/models.py`, inside `class Comp`, add after the `parking_type` / `property_type` lines (before `distance_km`):

```python
    style: Optional[str] = None        # MLS style/format, e.g. "2-Storey" (display-only)
    basement: Optional[str] = None     # MLS basement1[+basement2], e.g. "Fin W/O — Sep Entrance" (display-only)
    community: Optional[str] = None     # MLS neighbourhood (display-only)
```

- [ ] **Step 3b: Extend the query**

In `mcp_server/compsource/honestdoor.py`, in `_LISTINGS_QUERY`, change the `details` line to add the three fields:

```python
    "details { numGarageSpaces numBedrooms numBedroomsPlus numBathrooms numBathroomsPlus propertyType style basement1 basement2 } "
```

- [ ] **Step 3c: Add the basement helper and map the fields**

In `mcp_server/compsource/honestdoor.py`, add this helper near the other `_`-helpers (e.g. after `_garage_from_parking`):

```python
def _basement_display(details: dict[str, Any]) -> Optional[str]:
    """Human-readable basement string from MLS basement1[+basement2]. Preserves the
    API's literal "None" (= no basement); returns Python None only when both absent."""
    parts = [b for b in (details.get("basement1"), details.get("basement2")) if b]
    return " — ".join(parts) if parts else None
```

In `listing_to_comp`, add to the `return Comp(...)` call (after `property_type=...`):

```python
        style=details.get("style"),
        basement=_basement_display(details),
        community=(addr.get("neighborhood") or None),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_honestdoor.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add mcp_server/models.py mcp_server/compsource/honestdoor.py tests/test_honestdoor.py
git commit -m "feat(comps): extract style/basement/neighbourhood onto Comp (display-only)"
```

---

### Task 2: Show the new fields in the HTML comp table

**Files:**
- Modify: `mcp_server/report.py` (`_comp_row` ~line 97-104; `_comps_section` header ~line 111-112)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Comp.style`, `Comp.basement`, `Comp.community` (Task 1).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_report.py` (the `_comp_row`/`ReportComp` imports are added in the next step):

```python
def test_comp_row_shows_style_basement_community():
    from mcp_server.report import _comp_row
    from mcp_server.models import Comp, ReportComp
    from datetime import date as _date
    c = Comp(address="12 Elm St", lat=51.0, lng=-114.0, sold_price=550000,
             sold_date=_date(2026, 3, 1), sqft=1500, year_built=2005,
             style="2-Storey", basement="Fin W/O — Sep Entrance", community="Windsor Park")
    html = _comp_row(ReportComp(comp=c, kept=True))
    assert "2-Storey" in html
    assert "Fin W/O — Sep Entrance" in html
    assert "Windsor Park" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_report.py::test_comp_row_shows_style_basement_community -v`
Expected: FAIL — `"2-Storey"` not found (columns not rendered).

- [ ] **Step 3a: Render the cells in `_comp_row`**

In `mcp_server/report.py`, replace `_comp_row` body's `return` with the version adding three cells after `built`:

```python
def _comp_row(rc) -> str:
    c = rc.comp
    dist = f"{c.distance_km:.1f} km" if c.distance_km is not None else "—"
    built = _esc(c.year_built) if c.year_built else "—"
    style = _esc(c.style or "—")
    basement = _esc(c.basement or "—")
    community = _esc(c.community or "—")
    return (f"<tr><td>{_esc(c.address)}</td><td>{_money(c.sold_price)}</td>"
            f"<td>{_esc(c.sold_date)}</td><td>{c.sqft:,.0f}</td><td>{built}</td>"
            f"<td>{style}</td><td>{basement}</td><td>{community}</td>"
            f"<td>${c.price_per_sqft:,.0f}</td><td>{dist}</td>"
            f"<td>{_esc(c.include_reason or '')}</td></tr>")
```

- [ ] **Step 3b: Add the matching header columns**

In `_comps_section`, update the `head` string to insert the three headers after `Built`:

```python
    head = ("<thead><tr><th>Address</th><th>Sold $</th><th>Sold date</th><th>Sqft</th>"
            "<th>Built</th><th>Style</th><th>Basement</th><th>Nbhd</th>"
            "<th>$/sqft</th><th>Dist</th><th>Why included</th></tr></thead>")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_report.py -v`
Expected: PASS (new test + existing report tests, since the excluded-comp table is unchanged).

- [ ] **Step 5: Commit**

```bash
git add mcp_server/report.py tests/test_report.py
git commit -m "feat(report): show style/basement/neighbourhood in the comp table"
```

---

### Task 3: Cap the year-built derivation

**Files:**
- Modify: `mcp_server/derivation.py` (`_FEATURE_CAP` ~line 239)
- Test: `tests/test_derivation.py`

**Interfaces:**
- Consumes: existing `derive_feature_unit(subject, comps, residuals, factor)` (generic via `getattr`).
- Produces: `derive_feature_unit(..., "year_built")` returns a capped `$/year` Derivation or `none`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_derivation.py` (`_comp`/`_subject` helpers already exist; `yb` sets year built):

```python
def test_feature_unit_year_built_recovers_rate():
    # comps alike except year built; residuals ~ $2,000 per year newer
    s = _subject(yb=2010)
    comps = [_comp(700_000, yb=1980), _comp(702_000, yb=1980),
             _comp(760_000, yb=2010), _comp(762_000, yb=2010)]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "year_built")
    assert dv.method in ("matched_pair", "grouping", "regression")
    assert 1000 <= dv.value <= 4000


def test_feature_unit_year_built_capped_when_implausible():
    # $10,000/yr is above the $4,000 cap -> rejected -> none
    s = _subject(yb=2010)
    comps = [_comp(700_000, yb=1980), _comp(701_000, yb=1980),
             _comp(1_000_000, yb=2010), _comp(1_001_000, yb=2010)]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "year_built")
    assert dv.method == "none" and dv.value == 0.0


def test_feature_unit_year_built_none_without_variation():
    s = _subject(yb=2000)
    comps = [_comp(700_000, yb=2000), _comp(705_000, yb=2000)]
    dv = derive_feature_unit(s, comps, [c.sold_price for c in comps], "year_built")
    assert dv.method == "none" and dv.value == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_derivation.py -k year_built -v`
Expected: `test_feature_unit_year_built_capped_when_implausible` FAILS (default cap 50_000 admits the $10k/yr value, so `method != "none"`). The other two may already pass; that is fine — the cap test is the behavioural driver.

- [ ] **Step 3: Add the cap**

In `mcp_server/derivation.py`, extend `_FEATURE_CAP`:

```python
_FEATURE_CAP = {"beds": 80_000.0, "full_baths": 40_000.0, "half_baths": 15_000.0,
                "garage": 40_000.0, "year_built": 4_000.0}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_derivation.py -k year_built -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add mcp_server/derivation.py tests/test_derivation.py
git commit -m "feat(derivation): cap year-built $/yr adjustment at \$4,000/year"
```

---

### Task 4: Wire year_built through the estimate

**Files:**
- Modify: `mcp_server/estimate.py` (`DerivedSet` ~line 17-24; imports ~top; `_UNIT` ~line 38; `apply_adjustments` ~line 88-130; `reconcile` orchestration ~line 184-204; coefficients ~line 230-237)
- Test: `tests/test_estimate.py`

**Interfaces:**
- Consumes: `derive_feature_unit(..., "year_built")` (Task 3); `feat_dollar` (existing); `DerivedSet`.
- Produces: `DerivedSet.year_built: Derivation` (defaulted); a `"year_built"` `Adjustment` on every `CompAdjustment`; a `"year_built"` entry in `Estimate.coefficients`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_estimate.py`:

```python
def test_reconcile_applies_year_built_adjustment():
    # subject is newer; comps split 1980 vs 2010 with a clean ~$2,000/yr residual
    s = _subject(sqft=2000, yb=2010)
    comps = [_comp(700_000, yb=1980), _comp(702_000, yb=1980),
             _comp(760_000, yb=2010), _comp(762_000, yb=2010)]
    est = reconcile(s, comps, AdjustmentRules(), as_of=AS_OF, ladder_depth=0)
    # the year_built coefficient is present and positive
    yb = next(c for c in est.coefficients if c.factor == "year_built")
    assert yb.value > 0
    # an older (1980) comp gets a positive year_built dollar (adjusted up toward 2010 subject)
    older = next(ca for ca in est.per_comp if ca.raw_price in (700_000, 702_000))
    yb_adj = next(a for a in older.adjustments if a.factor == "year_built")
    assert yb_adj.value_dollar > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_estimate.py::test_reconcile_applies_year_built_adjustment -v`
Expected: FAIL — `StopIteration` (no `"year_built"` coefficient / adjustment yet).

- [ ] **Step 3a: Import `field` and extend `DerivedSet`**

In `mcp_server/estimate.py`, ensure the dataclasses import includes `field`:

```python
from dataclasses import dataclass, field
```

Add the defaulted `year_built` field to `DerivedSet` (default keeps the one manual construction in `tests/test_estimate.py` valid):

```python
@dataclass
class DerivedSet:
    time: Derivation
    size: Derivation
    beds: Derivation
    full_baths: Derivation
    half_baths: Derivation
    garage: Derivation
    year_built: Derivation = field(
        default_factory=lambda: Derivation(0.0, "none", "our-judgment", "not adjusted", "low"))
```

- [ ] **Step 3b: Register the unit label**

Update `_UNIT`:

```python
_UNIT = {"beds": "bed", "full_baths": "full bath", "half_baths": "half bath",
         "garage": "garage", "year_built": "year"}
```

- [ ] **Step 3c: Apply the adjustment in `apply_adjustments`**

In `apply_adjustments`, immediately after the `for factor, dv in (("beds", ...), ...)` loop (after its `adjustments.append(...)`) and before `adjusted_price = round(p, 0)`, add:

```python
    yb = derived.year_built
    syb, cyb = subject.year_built, comp.year_built
    d = feat_dollar(syb, cyb, yb.value)
    p += d
    if d != 0:
        ev = (f"subject {syb:g} vs comp {cyb:g} year -> {syb - cyb:+g} x "
              f"${yb.value:,.0f}/year ({yb.method})")
    else:
        ev = yb.evidence
    adjustments.append(_adj("year_built", yb.method, yb.source_type, dollar=d,
                            evidence=ev, conf=yb.confidence))
```

- [ ] **Step 3d: Derive year_built in `reconcile` and put it in the set**

In `reconcile`, after the `for factor in _FEATURES:` loop completes (after the `resid = [...]` update) and before the `derived = DerivedSet(...)` line, add:

```python
    year_built = derive_feature_unit(subject, comps, resid, "year_built")
```

Change the `DerivedSet(...)` construction to pass it:

```python
    derived = DerivedSet(time, size, feats["beds"], feats["full_baths"],
                         feats["half_baths"], feats["garage"], year_built)
```

Add a note after the existing feature notes loop:

```python
    if year_built.value:
        notes.append(f"year_built: ${year_built.value:,.0f} per year "
                     f"({year_built.method}; {year_built.evidence})")
```

- [ ] **Step 3e: Add the coefficient**

In the `coefficients = [ ... ]` list, add after the `garage` entry:

```python
        _coeff("year_built", year_built, is_pct=False, unit="year"),
```

- [ ] **Step 4: Run the full suite to verify it passes**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — the new test passes; `test_apply_size_brings_larger_comp_down` and `test_reconcile_blends_by_median_and_emits_payload` still pass (year_built defaults / no year variation → $0, no price change).

- [ ] **Step 5: Commit**

```bash
git add mcp_server/estimate.py tests/test_estimate.py
git commit -m "feat(estimate): apply derived year-built adjustment + coefficient"
```

---

## Self-Review

**Spec coverage:**
- §3 extract `style`/`basement`/`neighbourhood` → Task 1; display → Task 2. ✓
- §3 "basement1='None' preserved; both-absent → None; empty neighbourhood → None" → Task 1 Step 1 edge-case test + `_basement_display` + `or None`. ✓
- §4 year_built via `derive_feature_unit`, cap, out of `_FEATS`, `_UNIT`, sequence after garage, `feat_dollar` direction, coefficient tile → Tasks 3 & 4. ✓
- §4 keep age disclosure unchanged → no task touches the disclosure code (`derive_*` disclosure block in derivation.py is left as-is). ✓
- §5 tests: extraction mapping (Task 1), matched-pair/cap/none (Task 3), direction+application (Task 4). ✓
- §6 own branch off master → Global Constraints. ✓
- Out of scope (Excel grid) → no task touches `excel_report.py`. ✓

**Placeholder scan:** none — every code step shows full code; no TBD/TODO. ✓

**Type consistency:** `Comp.style/basement/community` (Task 1) consumed verbatim in Task 2; `_basement_display` defined and used in Task 1; `DerivedSet.year_built` (Task 4 3a) consumed in `apply_adjustments` (4c) and construction (4d); `_coeff("year_built", year_built, ...)` uses the local `year_built` Derivation set in 4d; `_FEATURE_CAP["year_built"]` (Task 3) read by `derive_feature_unit`'s existing `cap = _FEATURE_CAP.get(factor, ...)`. ✓
