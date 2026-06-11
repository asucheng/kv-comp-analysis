# Interactive HTML Comp Report — Design

**Date:** 2026-06-10
**Status:** Approved (pending spec review)
**Branch:** `feat/html-comp-report`

## Problem

The comp-analysis skill produces a transparent underwriter file *in chat*, but the
reasoning (which comps, which adjustments, how each coefficient was derived) is dense
prose. We want a **single self-contained, interactive HTML report** delivered at the end
of a Claude Desktop comp analysis that the user can click to open in a browser, where the
baseline value, confidence, comps, and every adjustment's derivation are explorable.

## Delivery mechanism (researched, not assumed)

Claude Desktop cannot reliably render inline MCP-UI widgets, embedded-resource artifacts,
or `file://` links today. The robust path is: an MCP tool **writes a self-contained `.html`
to disk and returns the absolute path**; the agent surfaces that path (+ `file://` link) at
the very end of chat; the user opens it in a browser. All interactivity lives *inside* the
HTML via native `<details>`/`<summary>` — **zero JavaScript, no external assets**. If Desktop
later supports inline widgets, that can be layered on without reworking the renderer.

## Architecture

Three pieces across the existing Skill + MCP combo:

```
comp-analysis skill (agent)
  get_subject -> find_comps -> curate -> estimate_value     [unchanged flow]
  present the chat file                                       [unchanged]
  NEW final step: assemble ReportPayload -> render_report(payload)
                  -> returns absolute .html path
                  -> agent posts path + file:// link at end of chat

mcp_server/
  derivation.py   emit structured CoefficientTrace per factor  (4a/4b enrichment)
  estimate.py     carry traces on Estimate.coefficients; unify size to median-of-pairs
  report.py (NEW) pure  render_report_html(payload) -> str      (testable, no IO)
  server.py       NEW render_report tool: slug path, write file, return absolute path
```

`report.py` keeps rendering **pure** (payload in, HTML string out) so it is unit-testable;
file IO lives only in the `server.py` tool wrapper.

## Derivation enrichment (items 4, 4a, 4b)

### Methodology change: unify size to median-of-pairs
Today the **size** coefficient returns the *first* qualifying matched pair, while time/beds/
baths/garage take the **median of all** qualifying pairs. Unify size to **median of all
matched pairs** so every tile tells the same story ("N pairs -> median"). This is a numeric
change; **re-run the `comp-verify` golden set** to confirm the 7/10 pass rate does not
regress. If it regresses, revisit before keeping.

### New models (`mcp_server/models.py`)
```python
class PairTrace(BaseModel):
    comp_a: str          # address
    comp_b: str          # address
    detail: str          # human arithmetic, e.g. "Δ$46,355 over 167 sqft"
    value: float         # per-unit value this pair implies

class CoefficientTrace(BaseModel):
    factor: str                      # time|size|beds|baths|garage
    method: AdjMethod
    source_type: SourceType
    value: float                     # pct for time, $ otherwise
    is_pct: bool
    confidence: Confidence
    equation: str                    # general formula, e.g. "per-unit = Δresidual / Δcount"
    pairs: list[PairTrace] = []      # matched-pair contributors (all of them)
    groups: Optional[dict] = None    # populated when method == grouping (group medians)
    regression: Optional[dict] = None  # populated when method == regression (n, slope)
    aggregate: str                   # e.g. "median of 3 pairs = $19,580"
    summary: str                     # = existing evidence string (fallback / no-signal case)
```

`Estimate` gains `coefficients: list[CoefficientTrace]` (one per factor, in
time/size/beds/baths/garage order). The existing per-comp `adjustments` (raw -> time ->
size -> beds -> baths -> garage -> adjusted) are unchanged and feed each tile's
"applied to comps" sub-table.

### Derivation functions return contributors
`derive_time_trend`, `derive_marginal_ppsf`, `derive_feature_unit` collect and return the
contributing pairs/groups/regression inputs alongside the chosen value, so `estimate.py`
can build each `CoefficientTrace`. The no-signal / capped cases set `pairs=[]` and explain
via `summary` (existing evidence text).

## ReportPayload (`mcp_server/models.py`)

```python
class ReportComp(BaseModel):       # a comp + its disposition
    comp: Comp
    kept: bool
    exclude_reason: Optional[str] = None

class ReportPayload(BaseModel):
    subject: Subject
    comps: list[ReportComp]                 # kept + excluded, with reasons
    estimate: Estimate                      # point/low/high/confidence/coefficients/per_comp/disclosures
    confidence_reasoning: str               # agent-authored "why" paragraph
    target_warnings: list[str] = []         # subject-specific, shown FIRST
    verify_next: list[str] = []             # "what I'd verify next"
    as_of: date
```

**Static project-level warnings live in the renderer as constants** (identical every run),
not in the payload:
- baseline only — excludes rehab/condition/fees; may need further feature adjustments (5a)
- no location/community adjustment; assumes location within 3 km is neutral; flagged future
  work, and material in real NA markets (5b)
- scope is AB/BC, calibrated on Calgary samples; other regions may be less accurate (5c)
- data/source notes: AVM is an estimate not a sale; ~180-day window; per-community search

## Report layout (top -> bottom)

1. **Header** — resolved address, baseline point value (large), low–high range, confidence
   badge (green/amber/red).
2. **Warnings** — directly under the header, before the detailed body (item 5d):
   **target-specific warnings first** (red/orange callouts), then **project-level warnings**
   (5a/5b/5c, neutral/blue).
3. **Subject** — attributes + provenance (user-supplied vs looked-up).
4. **Confidence & reasoning** — badge + the "why" paragraph + drivers (comp count, $/sqft
   CoV, ladder depth from `method_notes`).
5. **Comps** — table of **closest 10** (address, sold price/date, sqft, $/sqft, distance,
   why-included); `<details>` expands the remaining kept comps; excluded comps in a separate
   collapsed block with reasons.
6. **Adjustments** — grid of **tiles**, one per coefficient (value + method + confidence
   chip). Each tile is a `<details>`: expands to the equation, the exact pairs/groups used
   with arithmetic (4a/4b), the aggregate line, and the per-comp application sub-table.
7. **Disclosures** — age/location/time-mix (skew, direction of bias, why not adjusted).
8. **Not in this number** — condition/rehab/fees out of scope.
9. **What I'd verify next.**
10. **Footer** — data source, generated timestamp, AB/BC scope + ~180-day window note.

**Look & feel:** clean underwriter aesthetic — system-font stack, white cards on light-gray,
subtle shadow/rounded corners, one slate + accent palette, monospace for equations,
print-friendly. No logos or external fonts (keeps the file self-contained).

## Output file

- Directory: `./reports/` (relative to MCP server CWD), created if absent; gitignored.
- Filename: `<slugified-resolved-address>-<as_of>.html`.
- Tool returns the **absolute** path; agent posts path + `file://` link.

## Timing

Generated **on demand as the final step**, after the value is settled (address confirmed,
any `overrides` re-runs done). Re-running after an override regenerates/overwrites the same
file. Not produced on every `estimate_value` call.

## Skill wiring

`skill/comp-analysis/SKILL.md` gains a final step 7: after presenting the chat file and
settling overrides, assemble the `ReportPayload` (filling `confidence_reasoning`,
`target_warnings`, `verify_next`), call `render_report`, and surface the returned path +
`file://` link at the end of chat. Document the narrative fields the agent must fill.

## Testing

- **`render_report_html(payload)`** against a fixture estimate: all sections present; no
  `None`/placeholder leakage; tiles contain pair traces; warnings ordered target-then-project;
  self-contained (no external `http`/`src=` refs).
- **`CoefficientTrace` derivation**: pairs listed; `aggregate` matches the chosen value;
  no-signal case falls back to `summary` with empty `pairs`.
- **`comp-verify` regression**: confirm the size-median refinement does not drop the golden-set
  pass rate.

## Out of scope

- Inline in-chat widget rendering (MCP-UI / Apps SDK) — not dependable in Desktop today.
- Location/community adjustment — disclosed as future work (5b), not built here.
- Map/photo embedding — keeps the file self-contained and lean.
