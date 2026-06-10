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
