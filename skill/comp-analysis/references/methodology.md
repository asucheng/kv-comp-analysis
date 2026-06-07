# Methodology — Sales Comparison Approach (defined method)

The deterministic math lives in `estimate_value`; this documents it so you can explain
each number and know what an expert can override (via `rules`).

For each comp:
1. **Raw $/sqft** = sold_price / sqft.
2. **Adjust to subject-equivalent** (multiplicative):
   - **Time:** ×(1 + trend × months_old). `trend` is fit from the comp set (0 if < 4 comps;
     clamped ±2%/mo).
   - **Age:** ×(1 + age_rate × (subject_year − comp_year)). Default age_rate 0.5%/yr
     (newer = premium).
   - **Size:** ×(1 + size_elast × (comp_sqft − subject_sqft)/subject_sqft). Default 0.20
     (larger homes have lower $/sqft, so a larger comp adjusts upward toward the subject).
3. **Drop outliers** beyond median ± 1.5·IQR of adjusted $/sqft.
4. **Reconcile** weighted by similarity: w = 1/(1 + 0.5·dist_km + 2·|size%| + 0.05·|ageΔ| +
   0.1·months_old). Value = (weighted-average adjusted $/sqft) × subject sqft.
5. **Range** = 25th–75th percentile of adjusted $/sqft × subject sqft.
6. **Confidence:** high = ≥6 comps ∧ CoV ≤ 0.10 ∧ no widening; low = <4 comps ∨ CoV > 0.20 ∨
   ≥3 relaxations; medium otherwise.

These coefficients are defaults. An expert may pass a `rules` object to `estimate_value`
(e.g. a higher `size_elast` for luxury areas) or encode it in a playbook.
