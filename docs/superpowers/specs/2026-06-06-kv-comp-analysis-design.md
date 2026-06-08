# KV Capital — Residential Comp-Analysis Agent — Design Spec

**Date:** 2026-06-06
**Author:** Allen (solo)
**Deadline:** Fri 2026-06-12 11:59 PM MST (firm)
**Deliverable:** Public GitHub repo + README + ≤3-min demo video

---

## 1. Problem & goal

KV Capital finances Alberta home builders. Underwriters need **comp analysis** (comparative market analysis): given a residential property, find comparable recent sales and estimate the subject's value with defensible reasoning.

**Goal:** an agent that, given a subject address (plus optional attributes), finds comparable recent sales per KV's house rules, adjusts them, and produces a transparent value estimate — the way a senior underwriter walks through their file.

**Judging alignment:**
- *Domain understanding* → method grounded in the real **Sales Comparison Approach**; honest about Alberta data realities.
- *Judgment (focused > general)* → residential, Calgary-first; does one thing well.
- *Agent quality (reliability/latency/experience)* → lean intent-based tool surface, deterministic math, transparent narrative.
- *Code (clarity/structure/tests)* → clean MCP-tool / Skill split; pure functions unit-tested; hold-one-out accuracy eval.

## 2. Approach (settled)

A **Python MCP server** (deterministic mechanics) + a **Skill** (underwriter methodology/judgment), running in the user's existing Claude Desktop — no new app. This augments the underwriter's existing workflow rather than forcing a new platform.

**Local & free (hackathon constraint):** the server runs as a **local stdio process** launched by Claude Desktop on the user's machine. FastMCP is only the Python framework; we use its **stdio transport**, so there is **nothing to host and no hosting cost.** The only network traffic is outbound HonestDoor calls. The remote-HTTP transport (also supported by FastMCP) is reserved for *future productization* and is out of scope for the hackathon.

- **MCP tools** = deterministic, neutral, single-purpose, testable.
- **Skill** = orchestration + judgment (this is *required*: Anthropic's tool-design rules forbid encoding ordering/judgment in tool descriptions — that's flagged as prompt injection at review).

## 3. Data strategy (settled)

### 3.1 The Alberta data reality
- realtor.ca / MLS: public shows sold **status** but **not sold price** (Alberta sold prices are confidential on MLS); DDF API requires REALTOR® membership.
- Zillow: does not operate in Canada.
- Municipal assessment open data (Calgary/Edmonton Socrata): gives location, lot size, year built (Calgary), **assessed value** — but **no living-area sqft, beds, baths, sale price, or sale date.** It is a *valuation snapshot, not a transaction.* → enrichment / cross-check only, **not** a comp source.

### 3.2 Primary source: HonestDoor public data
HonestDoor publicly displays, per property, the fields Sam's criteria need: **real sold price + sale date, living-area sqft, beds, baths, year built, community, lot** (sale data sourced from Land Titles). It also exposes a **public per-community recently-sold index**:

```
https://www.honestdoor.com/recently-sold/ab/calgary/{community}
```
…which lists recent sales (beds/baths/price/type, sortable by recency, ~180-day default window), each linking to a full property page.

**Distinctions to respect in code:**
- The headline "$X Estimated Market Value" is HonestDoor's **AVM estimate** — NOT a sale. Use the **Sold History** table for real sales.
- Some attributes may be tagged "Estimate" (e.g. baths) — carry that provenance through.

**Usage rules:** fetch public pages politely / low-volume; attribute the source honestly in the README; do **not** use their paid "Sold Data+" product.

**Fetch mechanics (probed 2026-06-07):** HonestDoor is a Next.js app whose listing data is loaded client-side from a **GraphQL backend** (`https://core-backend.honestdoor.com/v2/graphql`) — structured JSON, cleaner than HTML parsing. The page shell (~15 KB) does **not** embed the listing data.

**SCHEMA VERIFIED (2026-06-07, live):** The GraphQL endpoint is **directly reachable — no Cloudflare/Turnstile block on the API**. Introspection is disabled (Apollo production), so the schema was mapped via error-suggestion probing. Verified surface:
- Root query fields: `getProperty(filter: PropertyUniqueFilterInput!)`, `getProperties(filter: PropertyFilterInput)`, `getListing2`/`getListings2`, `getPermits`.
- `Property` fields: `fullAddress, neighbourhood, city, yearBuilt, livingArea, bedroomsTotal, bathroomsTotal, closePrice, closeDate, taxAssessedValue, predictedValue` (AVM), `lotSizeArea, location { lat lon }, slug`.
- Filters: `getProperties(filter: { neighbourhoodName })`; `getProperty(filter: { slug })`.

**REAL-DATA NOTES (re-verified 2026-06-08 — real data is now the only source):**
1. **Attributes are dense on the `getListings2` bbox query we use.** The "~90% NULL" sparsity earlier recorded was for the bulk `getProperties` neighbourhood feed, **not** the bbox `getListings2` query. Live probe (50-row bbox over downtown Calgary): `livingArea` 40/50, beds 39/50, baths 40/50, yearBuilt 42/50, and `soldPrice`+`soldDate` 28/50. After `recent_sales` filters to completed SALEs with price+date+livingArea+location, that is ~20–28 usable real comps per query — sufficient for Sam's $/sqft method.
2. **No address lookup** — `getProperty` is **slug-only**; no address→record search. **Resolved** by geocoding the subject address → lat/lng with OSM/Nominatim (`mcp_server/geocode.py`); attributes the user knows (sqft/year/beds/baths) come via overrides.
3. **`neighbourhoodName` is not geo-scoped** — filtering "Roxboro" returned Moncton, NB properties. Avoided entirely: we enumerate by bbox + haversine radius, never by neighbourhood name.

**Conclusion:** the public feed is **real and good enough** — `HonestDoorCompSource` + the Nominatim geocoder are the default and only data path. The synthetic generator has been **removed**; a production integration could still plug a denser/authoritative source (MLS/DDF, Land Titles, HonestDoor paid API) into the same `CompSource` interface. Playwright/HTML-parsing fallbacks are unnecessary (API reachable) and out of scope.

### 3.3 No cache; live by design
The tool queries HonestDoor live at request time and is **re-run to refresh**. No pre-built dataset, no local cache (you cannot pre-cache comps for an address not yet typed). Consequence: the README accuracy figure is a **representative sample-run number** ("~X% over N live sales"), not byte-reproducible.

### 3.4 Subject resolution & extension
- **Subject geocoding** — the subject address is resolved to lat/lng via OSM/Nominatim (free, no key, low-volume); user-supplied overrides win. Thin areas / brand-new construction simply return fewer comps (surfaced via the widening-ladder flags), rather than being masked by synthetic data.
- Pluggable `CompSource` interface so KV can later swap in **MLS/DDF, Land Titles/SPIN, HonestDoor's paid API, or internal deal records.**

## 4. Architecture

```
Claude Desktop
  └─ Skill: comp-analysis  (methodology + judgment + orchestration)
       └─ MCP server (Python / FastMCP, local stdio, no auth)
            ├─ get_subject       ─┐
            ├─ find_comps         ├─ CompSource (HonestDoor public; pluggable)
            ├─ estimate_value    ─┘  (pure compute — no network)
            └─ cross_check        ─  (HonestDoor AVM + municipal assessment)
```

## 5. MCP tool surface (locked — Approach A, one-tool-per-action)

All tools are **read-only** (`readOnlyHint: true`, `idempotentHint: true`). Tight schemas (Sam's 5 as enum / min-max / defaults), `.describe()` on every param, `outputSchema` + `structuredContent` with a JSON text fallback, recoverable error messages. **Descriptions stay neutral** — no ordering/judgment (that lives in the Skill).

### 5.1 `get_subject(address, overrides?)` — *openWorld*
Resolve the subject property.
- **in:** `address`; optional `overrides` (beds, baths, sqft, year_built, lot_sf, property_type, community)
- **out:** `{ address, community, lat, lng, sqft, year_built, beds, baths, lot_sf, property_type, hd_estimate, provenance{field → user|honestdoor|missing} }`
- Auto-fills from HonestDoor; `provenance` marks gaps. `overrides` + `provenance` is how **new construction** is handled (underwriter supplies what HonestDoor lacks).

### 5.2 `find_comps(subject, criteria?)` — *openWorld*
Find + filter + rank comparable recent sales.
- **in:** `subject`; `criteria` defaulting to Sam's 5 (`radius_km=3, size_pct=0.20, lookback_months=12, age_years=10`) + secondary (`match_type`, `match_beds`); `min_comps`
- **behavior:** enumerate recent sales in subject's community + adjacent within radius; compute distance & $/sqft; apply Sam's 5 + secondary; rank by $/sqft proximity. If `< min_comps`, run the **deterministic widening ladder** (time → radius → size → age), logging each step.
- **out:** `{ comps:[{address, coords, sold_price, sold_date, sqft, beds, baths, year_built, type, price_per_sqft, distance_km, include_reason}], candidates_considered, relaxations:[{step, from, to}], flags }`

### 5.3 `estimate_value(subject, comps, rules?)` — *pure compute, no network*
Adjust comps to subject + reconcile.
- **in:** `subject`, `comps`, optional `rules` (size/age/time adjustment rates, weighting scheme, min_comps)
- **out:** `{ point, low, high, confidence, per_comp:[{address, raw_price, adjustments:[{factor, amount, rationale}], adjusted_price, adjusted_ppsf, weight}], method_notes }`

### 5.4 `cross_check(subject, estimate)` — *openWorld*
Independent sanity check.
- **out:** `{ hd_avm, assessed_value?, vs_avm_pct, vs_assessment_pct, verdict, notes }` — compares our estimate against HonestDoor's AVM and (if available) municipal assessed value.

**Defaults location (decision):** house-rule numbers (adjustment rates, `min_comps`, ladder triggers) live as **tool parameter defaults** so each tool is self-sufficient and unit-testable; the Skill documents and overrides them.

## 6. Skill design (`comp-analysis`)

A senior-underwriter methodology grounded in the **Sales Comparison Approach** (adjustment grid, price/sqft-centric per Sam). Sections of `SKILL.md`:

1. **Role & stance** — senior underwriter walking through their file; transparent (which comps, why, what adjusted); assistant for every level (novice → defensible estimate out of the box; expert → override criteria/inputs/house-rules for higher accuracy). Surfaces judgment, never replaces it.

2. **Workflow orchestration** — `get_subject` → resolve gaps → `find_comps` → curate → `estimate_value` → `cross_check` → present the file. (Lives here because tools may not encode ordering.)

3. **Judgment rules:**
   - **Missing subject data (new construction):** essential = sqft, year built, location, type. Auto-fill via `provenance`; for genuine gaps, **ask the user** rather than guess.
   - **Widening ladder:** when comps < threshold, relax **one step at a time in Sam's order** (time → radius → size → age), log each as a flag, **stop when enough comps OR further relaxation is unreliable** — and say which.
   - **Comp curation:** flag/drop outliers, note possible non-arm's-length sales, weight by similarity (distance, size, age) + recency.
   - **Adjustment grid:** price/sqft primary normalizer; transparent line-item adjustments for time, size, age.
   - **Reconciliation + confidence:** weighted blend → point + range; **explainable confidence rubric** (high/med/low from comp count, $/sqft dispersion, ladder depth) — not a black box.
   - **Cross-check interpretation:** material divergence from AVM or assessment → flag and explain; don't silently trust either.

4. **Output narrative ("the file"):** subject summary → comps table (each w/ inclusion reason + flags) → adjustment grid → value conclusion (point + range + confidence + *why*) → cross-check → **"what an experienced underwriter would verify next."**

5. **Guardrails / honesty:** never fabricate sales; always distinguish **HonestDoor AVM vs real sold price**; surface data limits (≈180-day window, per-community enumeration, "Estimate"-tagged attributes); state low confidence plainly and why.

6. **Extending this skill** — the capture loop for turning an underwriter's demonstrated method into a playbook (see §6.2): detect the intent, reflect, generalize, confirm, write, and apply on future matching runs.

**Methodology rigor (decision):** **defined method** — fixed adjustment-grid + reconciliation formula + confidence rubric; Claude's judgment is comp curation/weighting *within* that frame (reproducible & defensible).

### 6.1 Adjustment grid + reconciliation (the defined method)

Lives in `estimate_value` (pure compute) and is documented in `references/methodology.md`. Price/sqft-centric Sales Comparison Approach:

1. **Raw** `$/sqft_i = sold_price_i / sqft_i`
2. **Adjust each comp's $/sqft to subject-equivalent** (multiplicative line items, each surfaced in `per_comp.adjustments`):
   - **Time:** `× (1 + trend × months_since_sale)` — `trend` fit from the comp set itself; fall back to 0 if < 4 comps or weak fit; clamp `|trend| ≤ 2%/mo`.
   - **Age:** `× (1 + age_rate × (subj_year − comp_year))` — newer = premium; `age_rate` default **0.5%/yr**.
   - **Size:** `× (1 + size_elast × (comp_sqft − subj_sqft)/subj_sqft)` — larger home → lower $/sqft, adjust toward subject; `size_elast` default **0.20**.
3. **Outlier removal:** drop comps whose adjusted $/sqft falls outside `median ± 1.5·IQR`.
4. **Weighted reconciliation:** `w_i = 1 / (1 + a·dist_km + b·|size%Δ| + c·|ageΔyr| + d·months_old)`; `reconciled_ppsf = Σ w_i·adj_ppsf_i / Σ w_i`. Default coefficients `a=0.5, b=2, c=0.05, d=0.1`.
5. **Point value** = `reconciled_ppsf × subject_sqft`.
6. **Range** = `subject_sqft × {25th, 75th percentile of adjusted $/sqft}`.
7. **Confidence rubric:** **high** = ≥6 comps after filtering ∧ CoV(adj $/sqft) ≤ 0.10 ∧ no widening; **low** = <4 comps ∨ CoV > 0.20 ∨ ≥3 ladder relaxations; **medium** = otherwise.

*Formulas are fixed; coefficient values are tool defaults (per the defaults-location decision), overridable via `rules` or a playbook, and calibrated against real HonestDoor anchors during the build.*

### 6.2 Expandable playbooks (capture expert methodology)

The baseline (`methodology.md` + `house-rules.md`) is the **floor**. On top, a growing `playbooks/` library lets real underwriters teach the agent their own methods — turning each expert interaction into durable, shareable methodology (raises the ceiling for everyone).

**Capture loop** — when an underwriter departs from the baseline, gets a better result, and says *"make my way into a skill"* (or similar), the agent: (1) **reflects** on what differed from the baseline and why; (2) **generalizes** it into a reusable play (not a transcript); (3) **confirms** — drafts the play and asks the underwriter to approve/edit (never silently codifies); (4) **writes** `playbooks/<name>.md`; (5) on future runs, **scans playbook triggers**, and if one matches the subject, **applies it and notes in the output that a custom play was used.**

**No new tools needed** — a play only changes *how* the 4 tools are driven (different `criteria` to `find_comps`, different `rules` to `estimate_value`, plus heuristics as instructions). It composes with everything above.

**Capture target (decision): expand-by-default** — add a playbook to `comp-analysis`; spin out a *new* skill only when the captured method is a genuinely distinct domain (e.g. commercial).

**Play template** (matched by its `when:`, the way skills are matched by description):

```markdown
---
name: acreage-with-outbuildings
when: subject is an acreage / non-standard lot, or has significant outbuildings
author: <underwriter>   date: 2026-06-07   status: personal | shared
validated: "hold-one-out median error 5.1% over 12 sales"   # optional
---
Trigger:   community-boundary comp search gives poor comps for rural lots
Method:    1. search comps by river/road corridor, not community
           2. weight lot size heavily (rules.lot_weight=high)
           3. add outbuilding premium line-item in the grid
Rationale: why this beats the baseline for this subject type
```

**Governance** (keeps it trustworthy for a lender): confirm-before-save; provenance + `status` (`personal` vs team-promoted `shared`); transparency (output states when a play was applied); optional hold-one-out validation before promotion to `shared`.

## 7. Evaluation

**Hold-one-out backtest against real sold prices:** hide one real comp's sale price, estimate it from the others via the pipeline, compare to its actual sold price → report **median absolute % error over N live Calgary sales**. Gives a credible, real accuracy claim for the README.

## 8. Scope

- **v1 (decision):** residential, **Calgary-first** (best HonestDoor + parcel data; year-built present for the age filter).
- **Documented extensions:** Edmonton (thinner data — no year built), commercial borrowers (same workflow), real SOLD feeds via the `CompSource` interface.

## 9. Repo structure (proposed)

```
/                     README (value prop, data honesty, accuracy number, demo)
/mcp_server           FastMCP server: tools + CompSource + pure math (unit-tested)
  compsource/         HonestDoor adapter (+ pluggable interface, synthetic fallback)
  comps.py            filtering / widening ladder (pure)
  estimate.py         adjustment grid + reconciliation (pure)
/skill
  comp-analysis/
    SKILL.md            core methodology (always loaded)
    references/
      methodology.md    Sales Comparison Approach: adjustment grid + reconciliation + confidence
      house-rules.md    Sam's 5 + widening ladder + default rates (overridable policy)
    playbooks/          expandable, underwriter-contributed methods (capture loop)
      README.md         play template + how trigger-matching works
      <play-name>.md    one captured method each
/eval                 hold-one-out backtest harness
/tests                unit tests for pure functions
/docs/superpowers/specs   this spec
```

## 10. Open implementation questions (for the plan)

1. **Validate HonestDoor access early.** GraphQL backend confirmed (`core-backend.honestdoor.com/v2/graphql`) but **Cloudflare Turnstile** is present — test whether the GraphQL/page calls succeed programmatically; if blocked, stand up the Playwright fallback. De-risk this first; synthetic fallback if all paths fail.
2. Community-adjacency source for the 3 km radius (which neighbouring communities to also pull).
3. Whether the ~180-day recently-sold window can be extended toward 12 months (pagination/sort) or is capped — feeds the widening-ladder design.
4. Calibrate the §6.1 default coefficients (`age_rate`, `size_elast`, weighting `a–d`) against a few real HonestDoor anchors (formulas are already fixed).
```
