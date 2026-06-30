# Spec — Comp Feature Fields (display) + Year-Built Adjustment

**Date:** 2026-06-30
**Author:** Allen (solo)
**Status:** Approved in brainstorming; ready for implementation plan
**Scope:** Pull three more HonestDoor MLS fields onto each comp for human review, and promote
**year built** from a disclosed caveat to a data-derived dollar adjustment.
**Out of scope (follow-on on `feat/excel-report-output`):** wiring these fields into the KV Excel
comp grid (rows r13 neighbourhood / r19 style / r24 basement) and writing the year-built dollar into
the template's "Adjustment Year Built" row r34.

---

## 1. Problem & goal

KV's underwriter template has 12 named adjustment lines (Year Built, Format, Garage, Bedroom, Bathroom,
Finishing, Basement, Appliances, Fireplace, Neighbourhood, Backyard/Deck, Other). Our engine derives
only **beds, baths, garage** (plus time and size). The rest are absent — partly because they are human
judgment calls that KV itself keeps **Manual**, and partly because we never extracted the underlying
data.

We investigated the reference project `KV-Capital-propcomp-ai` (user request). Finding: it **resolves
more raw fields** than we do (`basement1`, `style`, `neighbourhood`, `legalDescription`) **but performs
no numeric adjustment at all** — its system prompt is explicit, *"Never provide valuation opinions —
only present comparable data."* So there is **no adjustment methodology to copy**; the numeric engine is
our project's distinctive piece. What we *can* copy is the richer field extraction.

**Goal:** two narrow, honest improvements:
- **(a) Surface** three categorical fields (`style`, `basement`, `neighbourhood`) on each comp so a human
  can eyeball KV's Format / Basement / Neighbourhood lines. **No auto-adjustment** for these.
- **(c) Auto-adjust Year Built**, the one currently-absent line for which we have clean, continuous data
  and a defensible matched-pair method.

We deliberately do **not** attempt dollar adjustments for the remaining lines (Finishing, Appliances,
Fireplace, Deck, Other): we have no comp-level data, and KV keeps them Manual.

---

## 2. Data-availability probe (done 2026-06-30)

HonestDoor introspection is disabled; we probed `getListings2` via error-suggestion. Confirmed valid on
`Listing2Details`, no auth change:

| Field | Sample live values | Maps to KV line |
|---|---|---|
| `style` | `2-Storey`, `Backsplit 4`, `Half Duplex`, `Townhouse`, `Apartment` | Format (r20) / Style (r19) |
| `basement1` | `Finished`, `Fin W/O`, `W/O`, `Unfinished`, `None` | Basement (r24) |
| `basement2` | `Sep Entrance`, `Unfinished` | Basement detail — `Sep Entrance` is a legal-suite signal |
| `address.neighborhood` | `Vellore Village`, `Burke Mountain`, … (already fetched) | Neighbourhood (r13) |

Note: these are nationwide samples (field availability check); our comps are bbox-filtered to Calgary.

---

## 3. Part (a) — extract + display, no adjustment

**Query:** add `style basement1 basement2` to the `getListings2` `details` block; `address.neighborhood`
is already selected.

**`Comp` model (display-only, all `Optional[str]`):**
- `style` — raw style string, e.g. `"2-Storey"`.
- `basement` — combined display string from `basement1` (+ `basement2` when present),
  e.g. `"Fin W/O — Sep Entrance"`; `None` when both absent. `"None"` from the API is a real value
  (no basement) and is preserved as-is.
- `community` — from `address.neighborhood` (the `Subject` already has `community`; mirror the name).

**Mapping:** populate these in `listing_to_comp` (and `multisearch_item_to_record` where applicable),
reusing the existing null-safe helpers.

**Display:** add the three fields to the HTML report's per-comp rows. They are descriptive only — never
fed into any number.

**Explicitly NOT done:** no `Comp`-level dollar adjustment, no entry in the derivation set, no effect on
`adjusted_price`. Categorical matched-pairs are unreliable on sparse comps, and KV treats these as Manual.

---

## 4. Part (c) — Year-Built dollar adjustment

### 4.1 This revisits an earlier design decision

The adjustment-methodology spec (2026-06-10) put **age in Tier 2** ("bracketed / filtered only, no
adjustment") because the only age treatment then was an **invented constant** (`0.005/yr`), which
violated the no-unbacked-constant rule. We are **not** reintroducing a constant. We promote year built to
**Tier 1 (data-derived)** by deriving `$/year` from the **actual comp set** via the same matched-pair
ladder used for beds/baths/garage. The Tier-2 objection (invented constant) no longer applies; the
Tier-2 confound concern (age correlates with size/quality) is mitigated by `_alike_except` (size within
10% + other features matched). When no clean signal exists, it falls back to **$0 / unadjusted** — the
same honest behaviour as the other features, and strictly better than a blanket constant.

### 4.2 Mechanism (reuse existing machinery)

- Add `year_built` to the derived set (`DerivedSet`) and derive it with the **existing**
  `derive_feature_unit` (already generic via `getattr`): matched-pair → grouping → regression → `_none`.
  The per-unit value is `$/year`; `feat_dollar(subject_year, comp_year, value)` gives the correct
  direction (subject newer → comp adjusted up).
- Add `_FEATURE_CAP["year_built"] = 4000.0` — a `$/yr` sanity ceiling that rejects a confounded pair
  (e.g. a "$50k/year" rate that is really a quality difference). A per-year value above the cap falls
  through to the next rung / `$0`.
- Keep `year_built` **out of `_FEATS`** so the bed/bath/garage `_alike_except` matching is not tightened
  (which would shrink their pair counts and regress existing adjustments). `year_built` derivation
  isolates its own factor by matching on the existing `_FEATS` instead.
- Add `_UNIT["year_built"] = "year"` for the evidence string, and wire `year_built` into the
  `apply_adjustments` sequence after garage (time → size → beds → full_baths → half_baths → garage →
  year_built).
- Surface as a coefficient tile in the HTML report, with the same method/evidence transparency as the
  other features.
- Keep the existing "age skew" **disclosure** unchanged. It is purely descriptive (a directional caveat
  about the comp set's vintage balance), never numeric, so it does **not** double-count with the new
  dollar adjustment. The adjustment is the numeric signal; the disclosure remains the narrative caveat.

---

## 5. Testing

**Part (a) — extraction mapping (unit):**
- Stub HonestDoor `details` payload with `style`/`basement1`/`basement2`/`neighborhood` → assert the
  `Comp` carries `style`, combined `basement`, and `community`.
- `basement1="None"` is preserved (not coerced to `None`); both-absent → `basement is None`.

**Part (c) — year-built derivation (TDD, watch each fail first):**
- Matched-pair signal: comps alike except year built → derives a positive `$/year` near the planted rate.
- Cap rejection: an implausible `$/year` (> $4,000) falls through to `$0`/next rung.
- `$0` fallback: no year variation (or no clean pairs) → `none`, factor not adjusted.
- Direction + application: subject newer than comp → positive dollar added to the comp; older → negative.
- Regression-suite guard: existing bed/bath/garage adjustments are unchanged (year built stays out of
  `_FEATS`).

Full `pytest` suite green before PR.

---

## 6. Delivery

- **Own branch off `master`:** `feat/comp-feature-fields-and-year-built` → its own PR.
- Core engine + HTML report only. The KV Excel-grid wiring of these fields/row is a separate follow-on
  on `feat/excel-report-output`.
