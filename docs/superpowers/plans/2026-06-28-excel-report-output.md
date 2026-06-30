# Excel (.xlsx) Report Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a KV-underwriter-style Excel (`.xlsx`) comp report, populated with our engine's math in KV's template layout, offered alongside the existing HTML report.

**Architecture:** A new `excel_report.py` loads the vendored KV template workbook, clears the sample comp data, and writes our `ReportPayload` into the Property Comparables and Summary sheets. A `method` flag picks the headline-value source: `"ours"` (our per-comp adjusted prices + value drive the sheet) or `"template"` (our derived coefficients feed KV's own formulas). `server.py` gains a `format` arg on `render_report` that branches HTML vs xlsx; everything else (caching, output dir, return shape) is unchanged.

**Tech Stack:** Python 3.11+, openpyxl (new dep), pydantic v2, pytest, FastMCP.

## Global Constraints

- Python `>=3.11` (per `pyproject.toml`).
- New runtime dependency: `openpyxl` (add to `[project].dependencies`).
- The vendored template is **read-only** — every render loads a fresh copy; never write back to it.
- Output dir logic is unchanged: absolute `_reports_dir()` (`$KV_COMP_REPORTS_DIR` else `~/kv-comp-reports`), falling back to the system tempdir. xlsx files are written **binary** (`"wb"`), HTML stays text.
- Backward compatibility: `render_report(payload, out_dir=...)` with no `format` must still produce HTML exactly as today. Existing `test_server.py` / `test_report.py` must stay green.
- The engine identity that the grid must honor (verified in `estimate.py:88-127`):
  `adjusted_price = raw_price + raw_price*time_pct + size$ + beds$ + full_baths$ + half_baths$ + garage$`.
  Adjustment factors are `time` (a `value_pct`), and `size`/`beds`/`full_baths`/`half_baths`/`garage` (each a `value_dollar`).
- Template cell anchors (0-indexed sheet names `"Property Comparables"`, `"Summary"`), confirmed from the file:
  - Property Comparables: subject column `D`; sample comps `E`–`K`; "Too Small" sample col `P`.
  - Attribute rows: address `6`, data source `7`, price `9`, list/sale flag `11`, neighbourhood `13`, unit price `14`, sale date `15`, floor area `17`, site size `18`, style `19`, format `20`, year built `21`, beds `22`, baths `23`, basement `24`, parking `25`, fireplace `26`, deck `27`, appliances `28`, notes `29`.
  - Money/adjustment rows: appraised/list `30`, bid-ask `31`, adj rows `34`–`45` (34 Year Built, 35 Format, 36 Garage, 37 Bedroom, 38 Bathroom, 39 Finishing, 40 Basement, 41 Appliances, 42 Fireplace, 43 Neighbourhood, 44 deck, 45 Other), Total Adjustments `46`, Adjusted Price `48`, Adjusted Unit Price `49`.
  - Stats block (unit price): MIN `D54`, 25th `D55`, median `D56`, weighted avg `D57`, 75th `D58`, max `D59`, SD `D60`; implied-value column `E54:E59`.
  - Headline: KV $/sqft `D64`, KV Internal Value `D65` (`=D64*$D$17`); Table A `D73` bedroom, `D74` bathroom, `D75` garage, `D77`/`D78` basement.
  - Summary: client/address `B3`, community `D14`, year built `D16`, site area m² `D18` (`D19=D18*10.7639` sqft), floor area `D20`, internal value `D32` (`='Property Comparables'!D65`). Manual inputs to blank: Plan `D10`, Block `D11`, Lot `D12`, tax-assessed land `D22`, land cost `D24`, presale/list price `D37`, construction cost `D42`.

---

### Task 1: Add openpyxl, vendor the template, and a loader

**Files:**
- Modify: `pyproject.toml` (add `openpyxl` to `[project].dependencies`)
- Create: `mcp_server/templates/sf_uw_template.xlsx` (copy of the provided file)
- Create: `mcp_server/excel_report.py` (template path + loader only, this task)
- Create: `tests/test_excel_report.py`
- Modify: `pyproject.toml` `[tool.hatch.build.targets.wheel]` — ensure the `.xlsx` ships in the wheel

**Interfaces:**
- Produces: `mcp_server.excel_report.TEMPLATE_PATH: str`, `mcp_server.excel_report.load_template() -> openpyxl.Workbook`

- [ ] **Step 1: Vendor the template and add the dependency**

```bash
mkdir -p mcp_server/templates
cp "SF UW Template - 41 HP - TEST.xlsx" mcp_server/templates/sf_uw_template.xlsx
.venv/bin/pip install openpyxl
```

Edit `pyproject.toml` — add `"openpyxl>=3.1"` to `dependencies`:

```toml
dependencies = [
    "fastmcp>=3.0",
    "pydantic>=2.6",
    "httpx>=0.27",
    "openpyxl>=3.1",
]
```

And make hatch include the template in the wheel (append under the existing wheel target):

```toml
[tool.hatch.build.targets.wheel]
packages = ["mcp_server"]
include = ["mcp_server/templates/*.xlsx"]
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_excel_report.py
import os
import openpyxl
from mcp_server.excel_report import TEMPLATE_PATH, load_template


def test_template_is_vendored_and_loads():
    assert os.path.isfile(TEMPLATE_PATH)
    wb = load_template()
    assert "Property Comparables" in wb.sheetnames
    assert "Summary" in wb.sheetnames
    # anchor cells the rest of the code depends on
    pc = wb["Property Comparables"]
    assert pc["B6"].value == "Address"
    assert pc["B65"].value == "KV Internal Value"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py::test_template_is_vendored_and_loads -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.excel_report'`

- [ ] **Step 4: Write the minimal loader**

```python
# mcp_server/excel_report.py
from __future__ import annotations
import os
import warnings
import openpyxl

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "sf_uw_template.xlsx")


def load_template() -> "openpyxl.Workbook":
    """Load a fresh, writable copy of the KV underwriter template. Read-only on disk —
    callers mutate the returned in-memory workbook and save elsewhere."""
    if not os.path.isfile(TEMPLATE_PATH):
        raise FileNotFoundError(f"KV Excel template missing at {TEMPLATE_PATH}")
    with warnings.catch_warnings():
        # openpyxl warns that the template's Data Validation extension is dropped; harmless.
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(TEMPLATE_PATH)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml mcp_server/templates/sf_uw_template.xlsx mcp_server/excel_report.py tests/test_excel_report.py
git commit -m "feat(excel): vendor KV template + loader, add openpyxl dep"
```

---

### Task 2: Fill the comp grid (subject + all comps, raw attributes)

**Files:**
- Modify: `mcp_server/excel_report.py`
- Modify: `tests/test_excel_report.py`

**Interfaces:**
- Consumes: `load_template()`; `ReportPayload`, `ReportComp`, `Comp`, `Subject` from `mcp_server.models`.
- Produces:
  - `ROWS: dict[str, int]` — attribute/adjustment row map.
  - `FORMULA_ROWS: tuple[int, ...]` — rows that hold per-comp formulas in the template.
  - `fill_comp_grid(ws, payload) -> dict` returning `{"cols": list[str], "excluded_cols": list[str], "last_col": str, "formulas": dict[int, str]}` where `cols` are the kept-comp column letters (subject is always `"D"`).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_excel_report.py
from datetime import date
from mcp_server.models import Subject, Comp, AdjustmentRules, ReportComp, ReportPayload
from mcp_server.estimate import reconcile
from mcp_server.excel_report import load_template, fill_comp_grid, ROWS


def _payload():
    s = Subject(address="138 Cranberry Place SE", lat=51.0, lng=-114.0, sqft=1416,
                year_built=2007, beds=3, baths=3, garage=1, community="Cranston",
                property_type="detached")
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
                         confidence_reasoning="Tight cluster.", as_of=date(2026, 6, 10))


def test_fill_comp_grid_writes_subject_and_all_comps():
    wb = load_template()
    ws = wb["Property Comparables"]
    info = fill_comp_grid(ws, _payload())
    # subject column D
    assert ws[f"D{ROWS['floor_area']}"].value == 1416
    # 4 kept comps land in E,F,G,H
    assert info["cols"] == ["E", "F", "G", "H"]
    assert ws[f"E{ROWS['address']}"].value == "71 Cranberry Pl"
    assert ws[f"E{ROWS['price']}"].value == 536_500
    assert ws[f"E{ROWS['floor_area']}"].value == 1429
    # excluded comp grouped after the kept ones, with a flag
    assert info["excluded_cols"] == ["J"]   # E-H kept, I gap header, J excluded
    assert ws[f"J{ROWS['address']}"].value == "56 Cranbrook Landing"
    # template sample data cleared (K had a sample comp address)
    assert ws[f"K{ROWS['address']}"].value is None
    # formulas captured for Option A reuse
    assert ROWS["adjusted_price"] in info["formulas"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py::test_fill_comp_grid_writes_subject_and_all_comps -v`
Expected: FAIL with `ImportError: cannot import name 'fill_comp_grid'`

- [ ] **Step 3: Write the implementation**

Append to `mcp_server/excel_report.py`:

```python
from datetime import date, datetime
from openpyxl.utils import get_column_letter, column_index_from_string
from mcp_server.models import ReportPayload, ReportComp, Comp, Subject

ROWS = {
    "address": 6, "source": 7, "price": 9, "list_sale": 11, "neighbourhood": 13,
    "unit_price": 14, "sale_date": 15, "floor_area": 17, "site_size": 18, "style": 19,
    "format": 20, "year_built": 21, "beds": 22, "baths": 23, "basement": 24,
    "parking": 25, "fireplace": 26, "deck": 27, "appliances": 28, "notes": 29,
    "appraised_list": 30, "bidask": 31, "adj_time": 34, "adj_size": 35, "adj_garage": 36,
    "adj_beds": 37, "adj_baths": 38, "adj_finishing": 39, "adj_basement": 40,
    "adj_appliances": 41, "adj_fireplace": 42, "adj_neighbourhood": 43, "adj_deck": 44,
    "adj_other": 45, "total_adj": 46, "adjusted_price": 48, "adjusted_unit": 49,
}
# Rows that hold a per-comp formula in column E of the template (reused for Option A).
FORMULA_ROWS = (ROWS["unit_price"], ROWS["appraised_list"], ROWS["bidask"],
                ROWS["adj_time"], ROWS["adj_size"], ROWS["adj_garage"], ROWS["adj_beds"],
                ROWS["adj_baths"], ROWS["adj_finishing"], ROWS["adj_basement"],
                ROWS["adj_appliances"], ROWS["adj_fireplace"], ROWS["adj_neighbourhood"],
                ROWS["adj_deck"], ROWS["adj_other"], ROWS["total_adj"],
                ROWS["adjusted_price"], ROWS["adjusted_unit"])

_FIRST_COMP_COL = "E"          # subject is D; comps start at E
_CLEAR_LAST_COL = "P"          # sample comps + stray notes live in E..P
_CLEAR_ROWS = range(5, 50)     # attribute + adjustment block


def _set(ws, col: str, row: int, val) -> None:
    ws[f"{col}{row}"] = val


def _capture_formulas(ws) -> dict:
    """Read column-E formula strings before clearing, so Option A can re-instantiate them
    for every comp column (including ones past the template's K)."""
    out = {}
    for r in FORMULA_ROWS:
        v = ws[f"E{r}"].value
        if isinstance(v, str) and v.startswith("="):
            out[r] = v
    return out


def _clear_comp_region(ws) -> None:
    first = column_index_from_string(_FIRST_COMP_COL)
    last = column_index_from_string(_CLEAR_LAST_COL)
    for r in _CLEAR_ROWS:
        for ci in range(first, last + 1):
            ws.cell(row=r, column=ci).value = None


def _fill_attr_col(ws, col: str, c: Comp, *, source: str = "HD") -> None:
    _set(ws, col, ROWS["address"], c.address)
    _set(ws, col, ROWS["source"], source)
    _set(ws, col, ROWS["price"], c.sold_price)
    _set(ws, col, ROWS["list_sale"], "Sale Price")
    sd = ws[f"{col}{ROWS['sale_date']}"]
    sd.value = datetime(c.sold_date.year, c.sold_date.month, c.sold_date.day)
    sd.number_format = "m/d/yyyy"
    _set(ws, col, ROWS["floor_area"], c.sqft)
    if c.year_built is not None:
        _set(ws, col, ROWS["year_built"], c.year_built)
    if c.beds is not None:
        _set(ws, col, ROWS["beds"], c.beds)
    if c.baths is not None:
        _set(ws, col, ROWS["baths"], c.baths)
    if c.parking_type:
        _set(ws, col, ROWS["parking"], c.parking_type)
    if c.include_reason:
        _set(ws, col, ROWS["notes"], c.include_reason)


def _fill_subject_col(ws, s: Subject) -> None:
    _set(ws, "D", ROWS["address"], s.resolved_address or s.address)
    if s.community:
        _set(ws, "D", ROWS["neighbourhood"], s.community)
    if s.sqft is not None:
        _set(ws, "D", ROWS["floor_area"], s.sqft)
    if s.lot_sf is not None:
        _set(ws, "D", ROWS["site_size"], s.lot_sf)
    if s.year_built is not None:
        _set(ws, "D", ROWS["year_built"], s.year_built)
    if s.beds is not None:
        _set(ws, "D", ROWS["beds"], s.beds)
    if s.baths is not None:
        _set(ws, "D", ROWS["baths"], s.baths)
    if s.parking_type:
        _set(ws, "D", ROWS["parking"], s.parking_type)


def fill_comp_grid(ws, payload: ReportPayload) -> dict:
    kept = [rc for rc in payload.comps if rc.kept]
    kept.sort(key=lambda rc: (rc.comp.distance_km is None, rc.comp.distance_km or 0))
    excluded = [rc for rc in payload.comps if not rc.kept]

    formulas = _capture_formulas(ws)
    _clear_comp_region(ws)
    _fill_subject_col(ws, payload.subject)

    cols: list[str] = []
    idx = column_index_from_string(_FIRST_COMP_COL)
    for rc in kept:
        col = get_column_letter(idx)
        _fill_attr_col(ws, col, rc.comp)
        cols.append(col)
        idx += 1

    excluded_cols: list[str] = []
    if excluded:
        idx += 1  # one-column gap separates kept from excluded
        header_col = get_column_letter(idx - 1)
        ws[f"{header_col}4"] = "Excluded"
        for rc in excluded:
            col = get_column_letter(idx)
            _fill_attr_col(ws, col, rc.comp)
            if rc.exclude_reason:
                _set(ws, col, ROWS["notes"], rc.exclude_reason)
            excluded_cols.append(col)
            idx += 1

    last_col = get_column_letter(idx - 1)
    return {"cols": cols, "excluded_cols": excluded_cols,
            "last_col": last_col, "formulas": formulas}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py::test_fill_comp_grid_writes_subject_and_all_comps -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server/excel_report.py tests/test_excel_report.py
git commit -m "feat(excel): fill comp grid with subject + all comps (raw attributes)"
```

---

### Task 3: Our-math adjustments + headline value (`method="ours"`)

**Files:**
- Modify: `mcp_server/excel_report.py`
- Modify: `tests/test_excel_report.py`

**Interfaces:**
- Consumes: `fill_comp_grid` result dict; `CompAdjustment`/`Adjustment` via `payload.estimate.per_comp`.
- Produces: `apply_ours(ws, payload, info) -> None` — writes per-factor dollar rows, static totals, headline `D64`/`D65`, range/confidence block, and widens the stat-block ranges to the real last comp column.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_excel_report.py
from mcp_server.excel_report import apply_ours


def _per_comp_by_addr(est, addr):
    return next(ca for ca in est.per_comp if ca.address == addr)


def test_apply_ours_reconciles_grid_to_engine():
    wb = load_template()
    ws = wb["Property Comparables"]
    p = _payload()
    info = fill_comp_grid(ws, p)
    apply_ours(ws, p, info)
    ca = _per_comp_by_addr(p.estimate, "71 Cranberry Pl")   # column E
    # Adjusted Price cell equals the engine's adjusted_price exactly
    assert ws[f"E{ROWS['adjusted_price']}"].value == ca.adjusted_price
    # rows 34..45 sum to Total Adjustments
    total = sum(ws[f"E{r}"].value or 0 for r in range(34, 46))
    assert round(total) == round(ws[f"E{ROWS['total_adj']}"].value)
    # headline: D64 = point/sqft, D65 stamped to point
    assert abs(ws["D64"].value - p.estimate.point / p.subject.sqft) < 1e-6
    assert ws["D65"].value == p.estimate.point
    # range + confidence block written below the headline
    assert ws["C66"].value == p.estimate.low and ws["D66"].value == p.estimate.high
    assert ws["D67"].value == p.estimate.confidence
    # two manual rows relabeled to host our size/time factors
    assert ws[f"B{ROWS['adj_time']}"].value == "Adjustment Time (market)"
    assert ws[f"B{ROWS['adj_size']}"].value == "Adjustment Size (per sqft)"
    # stat range widened past K to the real last comp column (H here)
    assert "E49:H49" in ws["D54"].value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py::test_apply_ours_reconciles_grid_to_engine -v`
Expected: FAIL with `ImportError: cannot import name 'apply_ours'`

- [ ] **Step 3: Write the implementation**

Append to `mcp_server/excel_report.py`:

```python
# factor -> the template row it occupies under our-math.
_FACTOR_ROW = {
    "time": ROWS["adj_time"], "size": ROWS["adj_size"], "garage": ROWS["adj_garage"],
    "beds": ROWS["adj_beds"], "full_baths": ROWS["adj_baths"], "half_baths": ROWS["adj_baths"],
}
_ZERO_ROWS = (ROWS["adj_finishing"], ROWS["adj_basement"], ROWS["adj_appliances"],
              ROWS["adj_fireplace"], ROWS["adj_neighbourhood"], ROWS["adj_deck"],
              ROWS["adj_other"])


def _adj_dollars(ca) -> dict:
    """Per-factor dollar impact for one comp, summing to adjusted_price - raw_price.
    Time is a pct on the raw price; everything else is already a dollar amount."""
    out: dict[int, float] = {}
    for a in ca.adjustments:
        row = _FACTOR_ROW.get(a.factor)
        if row is None:
            continue
        d = ca.raw_price * a.value_pct if a.value_pct is not None else (a.value_dollar or 0.0)
        out[row] = out.get(row, 0.0) + d   # full+half baths fold into the Bathroom row
    return out


def apply_ours(ws, payload: ReportPayload, info: dict) -> None:
    ws[f"B{ROWS['adj_time']}"] = "Adjustment Time (market)"
    ws[f"B{ROWS['adj_size']}"] = "Adjustment Size (per sqft)"
    by_addr = {ca.address: ca for ca in payload.estimate.per_comp}

    # Map columns to comps in the SAME order fill_comp_grid used (sorted by distance),
    # so column E in the grid is the same comp E gets here.
    kept = [rc for rc in payload.comps if rc.kept]
    kept.sort(key=lambda rc: (rc.comp.distance_km is None, rc.comp.distance_km or 0))
    for col, rc in zip(info["cols"], kept):
        ca = by_addr.get(rc.comp.address)
        if ca is None:
            continue
        _set(ws, col, ROWS["appraised_list"], ca.raw_price)
        _set(ws, col, ROWS["bidask"], "$nil")
        dollars = _adj_dollars(ca)
        for row in range(34, 46):
            _set(ws, col, row, round(dollars.get(row, 0.0)))
        total = round(ca.adjusted_price - ca.raw_price)
        _set(ws, col, ROWS["total_adj"], total)
        _set(ws, col, ROWS["adjusted_price"], ca.adjusted_price)
        _set(ws, col, ROWS["adjusted_unit"], ca.adjusted_ppsf)

    _write_headline(ws, payload)
    _widen_stat_ranges(ws, info["cols"][-1] if info["cols"] else "K")


def _write_headline(ws, payload: ReportPayload) -> None:
    est, s = payload.estimate, payload.subject
    if s.sqft:
        ws["D64"] = est.point / s.sqft       # D65 = D64*D17 = point (on recalc)
    ws["D65"] = est.point                     # stamp static so non-Excel viewers are correct
    ws["B66"] = "KV Value Range (25th-75th)"
    ws["C66"] = est.low
    ws["D66"] = est.high
    ws["B67"] = "Confidence"
    ws["D67"] = est.confidence


def _widen_stat_ranges(ws, last_col: str) -> None:
    """Repoint the unit-price stat formulas from the template's fixed E:K to E:<last_col>."""
    rng = f"$E$49:${last_col}$49"
    ws["D54"] = f"=MIN({rng})"
    ws["D55"] = f"=_xlfn.PERCENTILE.INC({rng},0.25)"
    ws["D56"] = f"=AVERAGE({rng})"
    ws["D58"] = f"=_xlfn.PERCENTILE.INC({rng},0.75)"
    ws["D59"] = f"=MAX({rng})"
    ws["D60"] = f"=_xlfn.STDEV.P(E49:{last_col}49)"
    ws["D57"] = f"=SUM($E$48:${last_col}$48)/SUM($E$17:${last_col}$17)"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py::test_apply_ours_reconciles_grid_to_engine -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server/excel_report.py tests/test_excel_report.py
git commit -m "feat(excel): our-math adjustments + headline value (method=ours)"
```

---

### Task 4: Fill Summary sheet + assemble `render_report_xlsx`

**Files:**
- Modify: `mcp_server/excel_report.py`
- Modify: `tests/test_excel_report.py`

**Interfaces:**
- Produces:
  - `fill_summary(wb, payload) -> None` — writes subject facts, blanks the manual-input cells, adds an HD-AVM note.
  - `render_report_xlsx(payload, method="ours") -> bytes` — the public entry: load template → fill grid → apply method → fill summary → set `fullCalcOnLoad` → return xlsx bytes.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_excel_report.py
import io
from mcp_server.excel_report import render_report_xlsx

_BLANKED = ["D10", "D11", "D12", "D22", "D24", "D37", "D42"]


def test_render_xlsx_fills_summary_and_blanks_manual_inputs():
    raw = render_report_xlsx(_payload(), method="ours")
    assert isinstance(raw, bytes) and raw[:2] == b"PK"   # xlsx is a zip
    wb = openpyxl.load_workbook(io.BytesIO(raw))
    sm = wb["Summary"]
    assert sm["B3"].value == "138 Cranberry Place SE"
    assert sm["D14"].value == "Cranston"
    assert sm["D16"].value == 2007
    assert sm["D20"].value == 1416
    for cell in _BLANKED:
        assert sm[cell].value is None, f"{cell} should be blanked"


def test_render_xlsx_rejects_unknown_method():
    import pytest
    with pytest.raises(ValueError):
        render_report_xlsx(_payload(), method="bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py::test_render_xlsx_fills_summary_and_blanks_manual_inputs -v`
Expected: FAIL with `ImportError: cannot import name 'render_report_xlsx'`

- [ ] **Step 3: Write the implementation**

Append to `mcp_server/excel_report.py`:

```python
import io

_SUMMARY_BLANK = ("D10", "D11", "D12", "D22", "D24", "D37", "D42")


def fill_summary(wb, payload: ReportPayload) -> None:
    s = payload.subject
    sm = wb["Summary"]
    sm["B3"] = s.resolved_address or s.address
    if s.community:
        sm["D14"] = s.community
    if s.year_built is not None:
        sm["D16"] = s.year_built
    if s.sqft is not None:
        sm["D20"] = s.sqft
    if s.lot_sf is not None:
        sm["D18"] = round(s.lot_sf / 10.7639, 2)   # m2; D19 = D18*10.7639 recomputes sqft
    for cell in _SUMMARY_BLANK:
        sm[cell] = None
    if s.hd_estimate:
        sm["B46"] = "HD AVM (cross-check, not a sale)"
        sm["D46"] = s.hd_estimate


def render_report_xlsx(payload: ReportPayload, method: str = "ours") -> bytes:
    if method not in ("ours", "template"):
        raise ValueError(f"unknown method {method!r}; expected 'ours' or 'template'")
    wb = load_template()
    ws = wb["Property Comparables"]
    info = fill_comp_grid(ws, payload)
    if method == "ours":
        apply_ours(ws, payload, info)
    else:
        apply_template(ws, payload, info)
    fill_summary(wb, payload)
    try:
        wb.calculation.fullCalcOnLoad = True   # force Excel to recompute remaining formulas
    except Exception:
        pass
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()
```

> Note: `apply_template` is implemented in Task 5. Until then, `method="ours"` (the default and the only path these tests exercise) works; the `else` branch will raise `NameError` only if called with `method="template"`, which Task 4's tests do not do.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py -k "summary or unknown_method" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server/excel_report.py tests/test_excel_report.py
git commit -m "feat(excel): fill Summary sheet + assemble render_report_xlsx"
```

---

### Task 5: Template-math path (`method="template"`)

**Files:**
- Modify: `mcp_server/excel_report.py`
- Modify: `tests/test_excel_report.py`

**Interfaces:**
- Consumes: `info["formulas"]` (column-E formula strings captured before clearing); `payload.estimate.coefficients`.
- Produces: `apply_template(ws, payload, info) -> None` — writes our derived coefficients into Table A, re-instantiates the template's per-comp formulas for every comp column, leaves `D64` at KV's convention.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_excel_report.py
from openpyxl.formula.translate import Translator  # noqa: F401  (sanity that dep is present)
from mcp_server.excel_report import apply_template


def _coeff(est, factor):
    return next((c.value for c in est.coefficients if c.factor == factor), None)


def test_apply_template_sets_table_a_and_keeps_kv_dollar_per_sqft():
    wb = load_template()
    ws = wb["Property Comparables"]
    kv_default = ws["D64"].value          # template's KV $/sqft before we touch it
    p = _payload()
    info = fill_comp_grid(ws, p)
    apply_template(ws, p, info)
    assert ws["D73"].value == _coeff(p.estimate, "beds")
    assert ws["D74"].value == _coeff(p.estimate, "full_baths")
    assert ws["D75"].value == _coeff(p.estimate, "garage")
    # Option A leaves KV's $/sqft convention in place (does NOT stamp our point)
    assert ws["D64"].value == kv_default
    # per-comp formula re-instantiated for column E (adjusted price)
    assert str(ws[f"E{ROWS['adjusted_price']}"].value).startswith("=")


def test_render_xlsx_template_method_loads():
    raw = render_report_xlsx(_payload(), method="template")
    wb = openpyxl.load_workbook(io.BytesIO(raw))
    assert "Property Comparables" in wb.sheetnames
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py::test_apply_template_sets_table_a_and_keeps_kv_dollar_per_sqft -v`
Expected: FAIL with `ImportError: cannot import name 'apply_template'`

- [ ] **Step 3: Write the implementation**

Append to `mcp_server/excel_report.py`:

```python
from openpyxl.formula.translate import Translator


def apply_template(ws, payload: ReportPayload, info: dict) -> None:
    """Option A: feed our derived coefficients into KV's Table A and let the sheet's own
    formulas compute. Re-instantiate the per-comp formulas (captured before clearing) for
    every comp column so they exist past the template's original K."""
    coeffs = {c.factor: c.value for c in payload.estimate.coefficients}
    if "beds" in coeffs:
        ws["D73"] = coeffs["beds"]
    if "full_baths" in coeffs:
        ws["D74"] = coeffs["full_baths"]
    if "garage" in coeffs:
        ws["D75"] = coeffs["garage"]

    formulas = info["formulas"]
    for col in info["cols"] + info["excluded_cols"]:
        for row, f in formulas.items():
            ws[f"{col}{row}"] = Translator(f, origin=f"E{row}").translate_formula(f"{col}{row}")
    _widen_stat_ranges(ws, info["cols"][-1] if info["cols"] else "K")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py -k template -v`
Expected: PASS

- [ ] **Step 5: Run the full module test to confirm nothing regressed**

Run: `.venv/bin/python -m pytest tests/test_excel_report.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add mcp_server/excel_report.py tests/test_excel_report.py
git commit -m "feat(excel): template-math path (method=template) via Table A + formula reuse"
```

---

### Task 6: Wire the Excel format into the server / MCP tool

**Files:**
- Modify: `mcp_server/server.py` (`render_report`, `render_from_estimate`, the `render_report` MCP tool, the `render_report_html`/`slug` import line)
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `mcp_server.excel_report.render_report_xlsx`.
- Produces:
  - `Tools.render_report(payload, out_dir=None, *, fmt="html", method="ours") -> str`
  - `Tools.render_from_estimate(..., fmt="html", method="ours")` — passes through.
  - MCP `render_report(estimate_id, confidence_reasoning="", target_warnings=None, verify_next=None, format="html", method="ours")`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_server.py
import io as _io
import openpyxl as _openpyxl


def test_render_report_writes_xlsx_when_format_xlsx(tmp_path):
    from tests.test_server import _report_payload  # reuse existing helper in this module
    tools = build_tools(source=StubCompSource(), geocoder=StubGeocoder((51.0, -114.0)),
                        as_of=date(2026, 6, 1))
    path = tools.render_report(_report_payload(), out_dir=str(tmp_path), fmt="xlsx")
    assert path.endswith(".xlsx")
    wb = _openpyxl.load_workbook(path)
    assert "Property Comparables" in wb.sheetnames


def test_render_from_estimate_supports_xlsx(tmp_path):
    res = TOOLS.find_comps(TOOLS.get_subject("123 Maple Dr", overrides=SUBJECT_OVERRIDES))
    est = TOOLS.estimate_from_comps(res.comps_id)
    path = TOOLS.render_from_estimate(est.estimate_id, out_dir=str(tmp_path), fmt="xlsx")
    assert path.endswith(".xlsx") and path.lower().endswith("xlsx")
```

> If `_report_payload` is not importable as written, copy the existing `_report_payload()` helper already defined in `tests/test_server.py` (it is defined near `test_render_report_writes_file`); call it directly rather than importing.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_server.py::test_render_report_writes_xlsx_when_format_xlsx -v`
Expected: FAIL with `TypeError: render_report() got an unexpected keyword argument 'fmt'`

- [ ] **Step 3: Update the import line**

In `mcp_server/server.py` line 17, extend the import:

```python
from mcp_server.report import render_report_html, slug
from mcp_server.excel_report import render_report_xlsx
```

- [ ] **Step 4: Rewrite `Tools.render_report` to branch on format**

Replace the body of `render_report` (currently `mcp_server/server.py:173-196`) with:

```python
    def render_report(self, payload: ReportPayload, out_dir: Optional[str] = None,
                      *, fmt: str = "html", method: str = "ours") -> str:
        """Write the report to disk; return its absolute path. `fmt` is "html" (default)
        or "xlsx"; for xlsx, `method` is "ours" (our math in KV's layout) or "template"
        (our coefficients drive KV's own formulas).

        The default output dir is ABSOLUTE ($KV_COMP_REPORTS_DIR, else ~/kv-comp-reports),
        never CWD-relative — Claude Desktop launches the stdio server from a non-writable
        working directory. Falls back to the system temp dir if the primary location can't
        be written."""
        name = slug(payload.subject.resolved_address or payload.subject.address)[:80].rstrip("-")
        if fmt == "xlsx":
            content: "str | bytes" = render_report_xlsx(payload, method=method)
            ext, mode = "xlsx", "wb"
        else:
            content = render_report_html(payload)
            ext, mode = "html", "w"
        fname = f"{name}-{payload.as_of}.{ext}"
        candidates = ([out_dir] if out_dir is not None
                      else [_reports_dir(), os.path.join(tempfile.gettempdir(), "kv-comp-reports")])
        last_err: Optional[OSError] = None
        for d in candidates:
            try:
                os.makedirs(d, exist_ok=True)
                path = os.path.abspath(os.path.join(d, fname))
                if mode == "wb":
                    with open(path, "wb") as f:
                        f.write(content)               # type: ignore[arg-type]
                else:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)               # type: ignore[arg-type]
                return path
            except OSError as e:
                last_err = e
        raise last_err  # type: ignore[misc]
```

- [ ] **Step 5: Thread `fmt`/`method` through `render_from_estimate`**

In `render_from_estimate` (`mcp_server/server.py:136-156`), change the signature and the final return:

```python
    def render_from_estimate(self, estimate_id: str, *, confidence_reasoning: str = "",
                             target_warnings: Optional[list] = None,
                             verify_next: Optional[list] = None,
                             out_dir: Optional[str] = None,
                             fmt: str = "html", method: str = "ours") -> str:
```

and the last line:

```python
        return self.render_report(payload, out_dir=out_dir, fmt=fmt, method=method)
```

- [ ] **Step 6: Add `format`/`method` to the MCP `render_report` tool**

In the `render_report` MCP tool (`mcp_server/server.py:268-284`), extend the signature and call. Update the docstring's first line and add one sentence; keep the rest:

```python
    def render_report(estimate_id: str, confidence_reasoning: str = "",
                      target_warnings: Optional[list] = None,
                      verify_next: Optional[list] = None,
                      format: str = "html", method: str = "ours") -> dict:
        """Render the comp report to disk; return its absolute path. Call this as the FINAL
        step, once the value is settled.

        `format`: "html" (default, interactive web report) or "xlsx" (KV underwriter
        spreadsheet). For xlsx, `method`: "ours" (default — our math in KV's layout) or
        "template" (our coefficients feed KV's own formulas). Ask the user which output
        they want before calling.

        Pass ONLY the `estimate_id` returned by estimate_value (the server still holds the
        subject, comps and full estimate for it) plus your small narrative — do NOT re-send
        the estimate or comps. `confidence_reasoning`: your one-paragraph why. `target_warnings`:
        subject-specific cautions, shown first. `verify_next`: what you'd check next. Tell the
        user the FOLDER and the full file path explicitly (file:// links usually aren't
        clickable in Desktop chat, so the path must be copy-pasteable)."""
        path = tools.render_from_estimate(
            estimate_id, confidence_reasoning=confidence_reasoning,
            target_warnings=target_warnings or [], verify_next=verify_next or [],
            fmt=format, method=method)
        return {"path": path, "directory": os.path.dirname(path), "open_url": "file://" + path}
```

- [ ] **Step 7: Run the new + existing server tests**

Run: `.venv/bin/python -m pytest tests/test_server.py -v`
Expected: PASS (new xlsx tests pass; all pre-existing tests still pass)

- [ ] **Step 8: Commit**

```bash
git add mcp_server/server.py tests/test_server.py
git commit -m "feat(server): render_report format=xlsx with method ours|template"
```

---

### Task 7: Skill — ask which output, document the Excel option

**Files:**
- Modify: `skill/comp-analysis/SKILL.md`

**Interfaces:** none (documentation/process change consumed by the agent at runtime).

- [ ] **Step 1: Add the "ask which output" step**

In `skill/comp-analysis/SKILL.md`, edit step 6 (the `render_report` step, around line 49). Insert, immediately before the `render_report` call sentence, a new instruction:

```markdown
6. **Ask which output format the user wants**, then **`render_report(estimate_id, confidence_reasoning?, target_warnings?, verify_next?, format?, method?)`**
   — the FINAL step, once the value is settled. Offer three choices and pass them through:
   - **HTML** (`format="html"`, default) — the interactive web report.
   - **Excel — KV method** (`format="xlsx", method="ours"`) — our math in KV's underwriter
     layout; the authoritative spreadsheet.
   - **Excel — template method** (`format="xlsx", method="template"`) — our coefficients feed
     KV's own formulas; produce this only if a client specifically asks for the KV-spreadsheet
     calculation.
   Do not assume — ask before rendering. Pass the **`estimate_id`** from `estimate_value` plus
   your short narrative — the server still holds the subject, comps and estimate, so **do NOT
   re-send them**.
```

- [ ] **Step 2: Document the Excel output under "The report" section**

In the "## The HTML report (`render_report`)" section (around line 109), add a short paragraph after the existing intro:

```markdown
**Excel output.** When the user picks Excel, `render_report` writes an `.xlsx` built from KV's
underwriter template (Property Comparables + Summary sheets) instead of HTML. `method="ours"`
(default) writes our per-comp adjusted prices and value into the KV layout; `method="template"`
feeds our derived coefficients into KV's own formulas. Land UW / Land Comps sheets and the
underwriter's manual inputs (presale price, construction costs, plan/block/lot) are left blank
for the underwriter to complete. The returned `path`/`directory`/`open_url` work the same as the
HTML report — present the folder and full file path to the user.
```

- [ ] **Step 3: Verify the skill file still reads coherently**

Run: `.venv/bin/python -c "print(open('skill/comp-analysis/SKILL.md').read().count('render_report'))"`
Expected: a count ≥ 4 (the references are intact); manually skim the two edited sections for flow.

- [ ] **Step 4: Commit**

```bash
git add skill/comp-analysis/SKILL.md
git commit -m "docs(skill): ask which output format; document Excel report option"
```

---

### Task 8: Full regression + smoke-open both workbooks

**Files:** none (verification only)

- [ ] **Step 1: Run the whole non-live suite**

Run: `.venv/bin/python -m pytest -m "not live" -q`
Expected: PASS (all green, including `test_report.py`, `test_server.py`, `test_excel_report.py`)

- [ ] **Step 2: Generate both workbooks against a real golden subject and reload them**

```bash
.venv/bin/python - <<'PY'
from datetime import date
from mcp_server.server import build_tools
import openpyxl, io

t = build_tools(as_of=date(2026, 6, 28))   # live HonestDoor source
s = t.get_subject("138 Cranberry Place SE, Calgary")
res = t.find_comps(s)
est = t.estimate_from_comps(res.comps_id)
for m in ("ours", "template"):
    p = t.render_from_estimate(est.estimate_id, out_dir="/tmp/kv-xlsx", fmt="xlsx", method=m)
    wb = openpyxl.load_workbook(p)
    pc = wb["Property Comparables"]
    print(m, "->", p, "| D65:", pc["D65"].value, "| comps in E6:",
          pc["E6"].value)
PY
```

Expected: two file paths printed, each reloading without error; `method="ours"` shows `D65` equal to the engine point value, both show a real comp address in `E6`.

- [ ] **Step 3: Final commit (if any verification fixes were needed)**

```bash
git add -A && git commit -m "test(excel): full regression + dual-method smoke check" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- Excel alongside HTML, `format` arg → Task 6. ✓
- `method` flag ours|template → Tasks 3, 5, 6. ✓
- Skill pauses to ask output → Task 7. ✓
- Show all kept comps, dynamic columns, excluded grouped right → Task 2. ✓
- Our math reconciles to engine adjusted_price; size/time relabeled rows → Task 3. ✓
- Headline via D64 = point/sqft; range + confidence block → Task 3. ✓
- Summary fill + blank manual inputs + HD AVM note → Task 4. ✓
- Land sheets untouched (no task writes to them) → satisfied by omission. ✓
- Template-math via Table A + formula reuse → Task 5. ✓
- fullCalcOnLoad, missing-template error, tempdir fallback, binary write → Tasks 1, 4, 6. ✓
- Vendored template ships in wheel; openpyxl dep → Task 1. ✓
- Golden/regression test → Task 8 (plus per-task unit tests). ✓

**Placeholder scan:** No `TODO`/`TBD`/"implement later" remain; every code step shows complete, runnable code.

**Type consistency:** `fill_comp_grid` returns `{"cols","excluded_cols","last_col","formulas"}`; consumed with those exact keys in `apply_ours` (Task 3) and `apply_template` (Task 5). `render_report_xlsx(payload, method)` signature matches its callers in `server.py` (Task 6). `render_report(..., fmt=, method=)` (Python) vs MCP tool param `format=` (mapped to `fmt=` at the call site) — consistent and intentional (the user-facing MCP param is `format`).
