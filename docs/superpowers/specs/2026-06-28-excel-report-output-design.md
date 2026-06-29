# Excel (.xlsx) report output — KV underwriter template-faithful

**Date:** 2026-06-28
**Status:** Approved design, ready for implementation plan

## Goal

Produce a KV-underwriter-style **Excel** comp-analysis report that mirrors the layout
of KV's own spreadsheet (`SF UW Template - 41 HP - TEST.xlsx`), populated with **our
engine's** computed values. The Excel output is offered **alongside** the existing HTML
report, not as a replacement.

## Decisions (locked with the user)

1. **Math source = our engine, their layout (Option B).** The workbook holds our
   computed per-comp adjusted prices and our headline value/range, in the template's
   exact look. The template's *native* math (Option A) stays available behind a flag for
   when a client asks for the KV-spreadsheet-style calculation.
2. **Option A is a switch, not a second tab.** One renderer, a `method` flag
   (`"ours"` default | `"template"`). No duplicated grid to keep in sync.
3. **Added alongside HTML.** `render_report` gains a `format` arg (`"html"` default |
   `"xlsx"`). Nothing existing breaks.
4. **The skill pauses and asks** which output the user wants (HTML / Excel-our-math /
   Excel-template-math) before the final render step.
5. **Comp grid shows all kept comps**, dynamically extending columns past the template's
   fixed 7; excluded comps grouped at the right (template's "Too Small" column style).
6. **Land valuation stays out of scope.** Land UW / Land Comps sheets are preserved
   exactly as the template has them (formulas + their sample land comps), to be automated
   later from a separate project.
7. **Blank the subject-specific manual inputs we don't produce** (presale price,
   construction costs, Plan/Block/Lot, tax-assessed land). Keep labels, formulas,
   formatting, and the Land sheets' reference data intact.

## Architecture

### New components

- **`mcp_server/templates/sf_uw_template.xlsx`** — the provided file, vendored into the
  package as a read-only resource. Each render loads it fresh and copies before filling.
- **`mcp_server/excel_report.py`** — new module, parallel to `report.py`. Public entry:
  `render_report_xlsx(payload: ReportPayload, method: str = "ours") -> bytes` (or writes
  to a path; see server wiring). Internals: load template → clear+fill the comp region →
  fill subject/Summary → set the headline value per `method` → save.

### Changed components

- **`mcp_server/server.py` (`Tools.render_report`)** — branch on `format`. For `xlsx`
  it writes `<slug>-<as_of>.xlsx` to the same `_reports_dir()` (with the existing
  tempdir fallback) and returns the path/dir exactly as today. The MCP `render_report`
  tool gains `format` and `method` params, documented in its docstring.
- **`pyproject.toml`** — add `openpyxl` as a runtime dependency.
- **`skill/comp-analysis/SKILL.md`** — add a step: before the final render, ask the user
  which output format they want; pass `format`/`method` through to `render_report`.

## Data flow / cell mapping — Property Comparables sheet

- **Columns:** `D` = subject; `E` onward = each **kept** `Comp`, dynamically extending
  past `K` for >7 comps. Excluded comps are grouped in a trailing column block (the
  template's `P` "Too Small" style). Nothing is hidden.
- **Attribute rows (6–29):** filled from each `Comp` — address, data source
  (`REALTOR`/`HD`), price, sale/list flag, neighbourhood, sale date, floor area above
  grade, site size, year built, beds, baths, basement, parking, etc.
- **Adjustment rows (34–45):** our per-comp `Adjustment`s mapped to template rows:
  - beds → **Bedroom** (row 37)
  - full + half baths → **Bathroom** (row 38, combined)
  - garage → **Garage** (row 36)
  - Our two extra factors get explicit homes by **relabeling two unused manual rows** to
    **"Adjustment Size (per sqft)"** and **"Adjustment Time (market)"**.
  - Remaining manual rows (Format, Finishing, Basement, Appliances, Fireplace,
    Neighbourhood, deck, Other) → 0, left for the underwriter.
  - **Bid-Ask row (31)** → `$nil` (we use sold prices, not listings).
- **Totals (rows 46 / 48 / 49):** Total Adjustments / Adjusted Price / Adjusted Unit
  Price written as **static numbers from our engine**, so each comp's Adjusted Price
  equals our `adjusted_price` exactly — correct even in viewers that don't recalc
  formulas.

## The headline value — the A/B switch

- **Option B (`method="ours"`, default):** set the single **KV $/sqft cell `D64` =
  `point` ÷ subject sqft**, so the existing `D65` chain and `Summary!D32` resolve to our
  number with zero formula surgery. `low`/`high`/`confidence` are written into a small
  labeled block beside it ("KV Value Range (25th–75th)", "Confidence"). The per-comp
  percentile stats block (rows 54–61) stays — informative, computed over our adjusted
  comps.
- **Option A (`method="template"`):** leave `D64` at KV's convention, feed our **derived
  coefficients into Table A** (`D73` bed, `D74` bath, `D75` garage, …) plus the raw
  comps, and let the sheet's own percentile math drive the value.

Both methods share the same data-fill code; the flag only toggles the headline source.

## Summary sheet & out-of-scope

- **Populate only what we own:** address (`B3`), community (`D14`), year built (`D16`),
  floor area above grade (`D20`), site area (`D18`/`D19` if `lot_sf` known), internal
  value/range/confidence (flows from the headline-value step above). HD AVM + municipal
  assessment from `cross_check` go into a small cross-check note.
- **Blank** the subject-specific manual inputs we don't produce: presale/list price,
  construction costs, Plan/Block/Lot, tax-assessed land value, appraised values. Labels,
  formulas, number formats, and structure are preserved.
- **Land UW / Land Comps:** untouched (formulas + sample land comps intact).

## Fidelity, error handling, testing

- **Fidelity:** openpyxl preserves formulas, number formats, and cell styles. It drops
  the unsupported data-validation extension and any charts (none present) — acceptable.
  Set workbook `fullCalcOnLoad` so Excel recomputes any remaining formulas on open.
- **Errors:** missing template resource → clear, explicit error. Same `_reports_dir()`
  primary-then-tempdir fallback as the HTML path.
- **Testing:** a golden test renders the existing eval subject to xlsx, reloads it, and
  asserts key cells: subject sqft, kept-comp count, each comp's Adjusted Price = engine
  `adjusted_price`, `Summary` internal value = `point`, and the range cells. Both
  `method="ours"` and `method="template"` are smoke-tested (load without error, headline
  cell populated).

## Out of scope (this iteration)

- Land valuation (Land UW / Land Comps automation) — deferred to a later project.
- Charts / conditional formatting beyond what the template already carries.
- Any change to the comp-finding or estimation engines; this is a rendering feature only.
