# Spec A — Article-Backed Adjustment Methodology & Transparency Payload

**Date:** 2026-06-10
**Author:** Allen (solo)
**Status:** Approved in brainstorming; ready for implementation plan
**Scope:** The value-adjustment engine + the structured "why" each step emits.
**Out of scope (later specs):** Spec B = interactive HTML presentation that *renders* this payload.

---

## 1. Problem & goal

The comp engine works end-to-end, but the **adjustment math is not backed by evidence.** Of the three
adjustments in `adjust_comp` today, only **time** is data-derived; **size** (`0.20` elasticity) and
**age** (`0.005`/yr) are invented constants. Several real value drivers (beds, baths, garage) are used
only as *filters*, never adjusted. The reconciliation **weight** coefficients (`0.5`, `0.05`, …) are
also invented, and they double-count dimensions that are already dollar-adjusted. There is no structured
record of *how* each number was produced.

**Goal:** every number in the estimate is either (a) **market-derived** from the actual comp set via a
**named appraisal method**, or (b) a **disclosed filter threshold** that is Sam's stated rule — and each
adjustment carries a structured, human-readable rationale tagging *which method* produced it and whether
that method comes from the **cited source** or from **our own judgment**. No unbacked constant survives.

**Source of methods (cite in output and docs):**
- McKissock, "Appraisal Adjustments: Types, Methods, and Cheat Sheet" —
  https://www.mckissock.com/blog/appraisal/appraisal-adjustments-types-methods-and-cheat-sheet/
- McKissock, "Paired Sales Analysis" —
  https://www.mckissock.com/blog/appraisal/paired-sales-analysis/

> **Attribution discipline (hard rule).** We may use our own domain reasoning, but we must never
> attribute it to the source. Every transparency line is tagged `article-method` or `our-judgment`.
> (Origin of this rule: we initially mis-quoted the article as prescribing a method for *age* and using
> the term "effective age" — neither is in the article. Verified by grep of the article text.)

---

## 2. The two-tier framework (the core decision)

Every dimension falls into exactly one tier:

| Tier | Treatment | Dimensions |
|---|---|---|
| **1 — Quantifiable** | **Dollar-adjusted**, magnitude **derived from the comps** via a named method | **time, size, beds, baths, garage** |
| **2 — Bracketed** | **Filtered only**, then treated as equivalent — **no adjustment, no weighting** — but the comp set's *imbalance* is **disclosed** as a directional caveat | **age, distance/location** |

**Consequences:**
- The made-up constants `0.20`, `0.005`, and the weight coefficients `0.5 / 2.0 / 0.05 / 0.1` are **all retired.**
- The reconciliation **weight is removed entirely**; comps are blended by **median** (see §5).
- For Tier 2 the **filter does all the work**: a comp 0.3 km away and one 2.9 km away count *identically*,
  as do a same-vintage comp and one 10 yr off. Comparability is *defined by the filter*; within it,
  everything is equal.

---

## 3. The method hierarchy (how each Tier-1 magnitude is derived)

For each Tier-1 dimension the engine walks down this ladder and uses the **first rung the comp set can
support**, then records which rung it used. Order follows the appraisers' own stated preference.

1. **Matched pair** — two comps identical except this one feature → cleanest isolation.
2. **Grouping of sales** *(the realistic primary)* — compare the median of comps *with* vs *without* the
   feature (or larger vs smaller half), on the size/time-netted residual.
3. **Regression / least-squares** *(small-N fallback)* — slope across all comps; the existing
   `estimate_trend` math, generalised. Used when there are too few comps to form trustworthy groups.
4. **Cost / convention** *(last resort)* — a cited value, labelled "not locally derived." Only when the
   comps carry no usable signal for that dimension. Flagged low-confidence.

A rung that produces an unstable/implausible result (e.g. negative marginal $/sqft, empty group) falls
through to the next rung. Every result is stamped with `method_used`, the evidence behind it, and a
per-line confidence. **Lower rung → lower confidence.**

---

## 4. Adjustment sequence & math

Adjustments are applied **in order, netting each out before isolating the next** — this is the article's
"subtract the size effect first, then attribute the garage" logic, and it is what prevents double-counting
(e.g. a bigger house already having more bedrooms).

```
Sequence:  transactional  →  time  →  size  →  beds  →  baths  →  garage  →  [location: qualitative]
```

**Transactional** is the article's *first* step (arm's-length normalisation: financing, distressed/
foreclosure/family transfer, concessions). We have **no transactional-detail data** in the source, so we
do **not** add a dollar adjustment here — it remains an *exclusion/flag* (the existing non-arm's-length
curation), disclosed like a Tier-2 caveat. It is named in the sequence for completeness, not implemented
as a derived magnitude.

Math operates on the comp's **price** (mixing % and $ adjustments, as appraisal does):

```
p0      = comp.sold_price
p_time  = p0 × (1 + time_pct)                         # market conditions — a percentage
p_size  = p_time − (comp.sqft − subject.sqft) × marginal_$psf   # GLA — dollars
p_beds  = p_size  ± bed_value                          # grouping on residual
p_baths = p_beds  ± bath_value
p_grg   = p_baths ± garage_value
adjusted_price = p_grg          # this comp's indication of subject value
adjusted_ppsf  = adjusted_price / subject.sqft         # for dispersion/range only
```

`adjusted_price` is the comp's direct indication of the subject's value; the point estimate is the
**median** of `adjusted_price` across comps (§5).

### 4.1 Time (market conditions) — Tier 1, **adjusted**
**Measured on size-controlled data** so a size-imbalanced comp set cannot masquerade as a price
trend (the time/size confound — see the addendum at the end of this section).
- Hierarchy: (1) **size-matched pairs across time** — comps within ±5% sqft sold at different
  dates; the $/sqft gap ÷ months apart is pure market movement (median across pairs). This
  generalises the original "repeat sale" rung to the *findable* case of same-size homes.
  → (2) **grouping of sales on size-normalized $/sqft** — level each price to the subject's size
  via a provisional marginal rate (`linreg_slope` of price~sqft), then bucket recent vs older.
  → (3) **regression on size-normalized $/sqft** (small-N fallback) → qualitative.
- Clamped ±2%/mo; **a clamped trend lowers confidence** (saturation = unstable fit), and that
  low per-rung confidence propagates to the overall estimate confidence.
- **Disclosure (§6):** when recent vs older comps differ in mean sqft (≥8%), emit a `time`
  `Disclosure` flagging residual confound risk.

> **Addendum (added during implementation).** The first build derived the trend by grouping on
> *raw* $/sqft, before size was netted out. A size-imbalanced set (e.g. recent sales larger →
> lower $/sqft) then read as a spurious market decline and biased the estimate ~11% with "high"
> confidence. The fix — measure time on size-controlled data (match by size, else normalize) — is
> the hierarchy above. "Time first, then size" remains the *application* order; this only changes
> how the time *rate* is *measured*.

### 4.2 Size / GLA — Tier 1, **adjusted**
- Mechanic (article-standard): `adjustment = (comp.sqft − subject.sqft) × marginal_$psf`.
- `marginal_$psf` = **Δprice ÷ Δsqft**, derived by: matched pair → grouping (larger-half vs smaller-half medians) → regression (slope of `price ~ sqft`) → cited rate.
- **Marginal $/sqft is lower than *average* $/sqft** (land + fixed value already counted) — this is why the old `0.20 × average ppsf` form was wrong in *form*, not just constant.

### 4.3 Beds → Baths → Garage — Tier 1, **adjusted**
- One shared mechanic, applied in sequence on the size/time-netted residual:
  `feature_value = median(adjusted_price | has feature) − median(adjusted_price | lacks feature)`.
- Matched-pair rung = two comps identical except the feature (article's literal $15k garage case).
- **No variation** in the comp set (all comps match the subject, or none differ) → **no adjustment, noted.**
- Later features in the sequence carry **lower confidence** (more residual noise) — disclosed.

### 4.4 Age — Tier 2, **filter-only**
- **No dollar adjustment.** Controlled by Sam's ±10-yr filter (bracketing).
- Rationale (`our-judgment`, *not* the article): buyers price *effective* age (a condition judgment),
  not chronological year; a renovated old home behaves "newer." We have no condition data, so the
  residual is deferred to the rehab/condition markdown (§7, out of scope).
- **Disclosure (§6):** report the comp set's vintage skew vs the subject; flag one-sided skew.

### 4.5 Distance / Location — Tier 2, **filter-only**
- **No dollar adjustment.** Controlled by Sam's 3-km filter.
- **Distance ≠ location quality:** 3 km can straddle communities of very different $/sqft, so this is a
  *stronger* assumption than age. Honoured because Sam chose 3 km as the locational control.
- **Data limit:** `Comp` carries only `lat/lng` + `distance_km`, **no community field** — so a per-community
  dollar adjustment isn't even supportable. This is the honest reason location stays qualitative.
- **Disclosure (§6):** report directional/clustering skew of the comp set.

---

## 5. Reconciliation — median blend, no weighting

- **Point estimate = median of `adjusted_price`** across the kept comps. No weighting; every in-filter,
  adjusted comp counts equally.
- **Why median, not mean:** robust to a single oddball comp; needs no coefficients to defend.
- **Why no weight:** weighting can't be derived from market evidence (there is no sold price telling you
  how much a 2 km comp should count vs a 1 km comp), and weighting on already-adjusted dimensions
  double-counts them. The whole `comp_weight` function is removed.
- **Range:** 25th–75th percentile of `adjusted_price` (unchanged in spirit; now on price not ppsf).
- **Outlier handling:** keep the IQR drop *optional*; with a median it matters far less. Decide in the plan.

---

## 6. Disclosed-insight warnings (Tier-2 imbalance → underwriter caveats)

Because Tier-2 dimensions are *not* corrected, any imbalance in the comp set flows straight into the
baseline. We **compute and surface** that imbalance instead of hiding it. Pattern for each:

> *imbalance → likely direction of bias → why we didn't adjust → residual for the user to weigh.*

Examples:
- ⚠️ *Vintage: comps average **5 yr older** than the subject; controlled by selection, not adjustment, so
  an older set may **understate** a newer subject. (Condition/rehab out of scope — §7.)*
- ⚠️ *Location: comps cluster **~2.5 km NE** toward a higher-$/sqft pocket; location isn't dollar-adjusted
  (no community data), so the baseline may be **biased upward**. Flagged risk.*

These are first-class output, not footnotes — they turn our limitations into judgment.

---

## 7. What's explicitly *not* in the number (out-of-scope, disclosed)

The comp approach does not price **condition, rehab, or deferred maintenance** (no condition data in the
source). A standing output section states this and guides the user to **mark the baseline down** for it,
rather than fabricating a condition adjustment. This is where the age/effective-age residual lands.

---

## 8. Transparency payload (data model)

`Adjustment` grows from `{factor, pct, rationale}` to:

```
Adjustment:
  factor:       "time" | "size" | "beds" | "baths" | "garage"
  method_used:  "matched_pair" | "grouping" | "regression" | "cost_convention" | "none"
  source_type:  "article-method" | "our-judgment"
  value_pct:    float | None        # for percentage adjustments (time)
  value_dollar: float | None        # for dollar adjustments (size/features)
  evidence:     str                 # the pairs/groups/slope or cited source behind the number
  confidence:   "high" | "medium" | "low"
  rationale:    str                 # plain-English "why", human-readable
```

Tier-2 dimensions emit a parallel **`Disclosure`** record (factor, skew stat, direction, caveat) rather
than an `Adjustment`. This structured payload is exactly what Spec B's HTML will render — and it is fully
readable in the text output today.

`Estimate.confidence` factors in **method strength** (matched-pair > grouping > regression/cost >
qualitative), in addition to comp count, $/sqft dispersion (CoV), and ladder depth.

---

## 9. Compute vs. display (guardrail)

The estimate is derived from the **entire** comp set `find_comps` returns — there is **no cap**
(`comps.py` returns all; verified). The "10 comps" seen in Claude Desktop is a **chat-display** artifact
only. Spec A must ensure the skill passes the **full** comp set to `estimate_value`; the 10-row view must
never leak into the math. Genuine non-arm's-length/outlier *exclusions* are quality drops, not a count cap.

---

## 10. Components touched

The work splits cleanly along the project's architecture: **MCP = deterministic math; Skill =
methodology, judgment, narrative.** Design follows the `mcp-server-dev` and `skill-creator` plugin
guidance.

### 10.1 MCP changes — grouped by intent, not API

**Tool-surface decision: no new tool.** The surface stays the four intent-mapped tools
(`get_subject` → `find_comps` → `estimate_value` → `cross_check`). Per the "group by intent, not like an
API" rule, mechanical helpers like `calculate_matched_pairs` / `compute_marginal_ppsf` would be
API-shaped internals, not intents — so the derivation lives **inside** the `estimate_value` valuation
intent, exposed in its output and overridable by parameter.

- **`mcp_server/models.py`** — restructure `Adjustment` (add `method_used`, `source_type`, `value_dollar`,
  `evidence`, per-line `confidence`); add `Disclosure`; retire `AdjustmentRules` constants (`size_elast`,
  `age_rate`, weight coeffs); `CompAdjustment`/`Estimate` carry the new payload.
- **`mcp_server/derivation.py` (new, internal module — NOT a tool)** — per-dimension hierarchy walkers
  (`derive_time_trend`, `derive_marginal_ppsf`, `derive_feature_value`, `compute_disclosures`), each
  returning value + method + evidence + confidence.
- **`mcp_server/estimate.py`** — restructure `adjust_comp` to the sequenced price math; `reconcile` →
  median + attached disclosures; **remove `comp_weight`**; keep `estimate_trend` as the time regression rung.
- **`mcp_server/server.py`** — `estimate_value` keeps its single intent but gains: **structured output**
  (`outputSchema` + `structuredContent`, with text fallback) for the transparency payload; an
  **`overrides` parameter** (replacing the retired `rules` constants) so a human can correct a *derived*
  coefficient and re-value — the in-loop override point, without a new tool. Add `readOnlyHint: true` +
  `title` annotations to **all** tools (everything here is pure read computation); tighten schemas and add
  sibling-disambiguating descriptions.

### 10.2 Skill changes — content, in skill-creator style

Keep the progressive-disclosure structure (lean `SKILL.md` + `references/`); changes are content,
imperative, *explaining the why* (no heavy MUSTs), `SKILL.md` < 500 lines.

- **`skill/comp-analysis/references/methodology.md`** — rewrite to the two-tier framework + method
  hierarchy + attribution discipline; cite the two articles.
- **`skill/comp-analysis/references/house-rules.md`** — mark each criterion *adjusted* vs *bracketed*.
- **`skill/comp-analysis/SKILL.md`** — workflow (pass the **full** comp set; the inspect→override→
  re-estimate human loop; cross-check); output format (per-line method/source tags, Tier-2 disclosures,
  out-of-scope rehab note); judgment rules (excluding non-arm's-length comps; reading a disclosure;
  tagging `article-method` vs `our-judgment`).

## 11. Testing & validation

- **MCP unit tests** — each derivation rung with hand-built comp sets (clean matched pair; grouped; small-N
  regression fallback; no-variation → no adjustment; unstable → fall-through); the sequence/netting
  (garage isolated only after size netted — the article's $15k example); median reconciliation and range;
  Tier-2 disclosures fire on skewed sets and stay quiet on balanced sets; `overrides` parameter respected.
- **Regression guard** — keep the existing hold-one-out accuracy eval.
- **Skill validation (post-implementation)** — run the `skill-creator` eval loop: a few realistic test
  prompts, with-skill vs baseline, the benchmark viewer for qualitative + quantitative review; then
  description-optimize the SKILL.md frontmatter.

## 12. Non-goals

Interactive HTML (Spec B); dollar modelling of condition/rehab; acquiring condition data; multivariate
regression; any separate/broader data pull (the comp set *is* the market study).
