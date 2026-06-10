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

- **Time:** %/month, measured on **size-controlled** data so a size-imbalanced comp set can't
  masquerade as a price trend. Rungs: (1) **size-matched pairs across time** — comps within ±5%
  sqft sold at different dates, so the $/sqft gap is pure market movement; (2) **grouping** of
  recent vs older sales on **size-normalized** $/sqft (each price leveled to the subject's size
  via a provisional marginal rate); (3) **regression** on size-normalized $/sqft (small-N
  fallback). Clamped ±2%/mo; a clamped trend lowers confidence. If recent vs older comps differ
  in size, a `time` disclosure flags the residual risk.
- **Size (GLA):** `(comp.sqft − subject.sqft) × marginal $/sqft`, marginal rate = Δprice/Δsqft from
  the comps. Marginal $/sqft is below average $/sqft — land + fixed value already counted.
- **Beds/Baths/Garage:** value of ONE unit, then × (subject − comp) count. Derived
  matched-pair-first (comps alike in size/other-features/type, differing only in this
  feature → clean isolation) → grouping → regression; capped to reject confounded values;
  null-safe. **Selection uses only Sam's 5** (match toggles default off) — these features
  are *adjusted*, not filtered, so the comp set keeps the variation the engine needs.
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
