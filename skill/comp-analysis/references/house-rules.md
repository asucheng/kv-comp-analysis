# KV / Sam's house rules

Default comp-selection criteria (the `criteria` defaults in `find_comps`):

- **Radius:** within 3 km of the subject.
- **Size:** within ±20% of subject sqft.
- **Recency:** sold within the last 6 months (relaxes to 12 if sparse; never beyond 12).
- **Price/sqft:** the primary normalizer and ranking metric.
- **Age:** within ±10 years of the subject.

**Widening ladder** when comps are sparse — relax one step at a time, in this order, logging
each: time (6→12 mo, capped at 12) → radius (3→5→8 km) → size (±20→30→40%) → age (±10→20→30 yr).

**Optional exact-match toggles** (all off by default): `match_type`, `match_beds`,
`match_baths`, `match_garage`. Each restricts comps to the subject's exact value, but only
when *both* the subject and the comp report it — a missing value never silently drops a comp.
Garage (HonestDoor `garageSpaces`) is frequently unknown, so a garage match is often skipped
and flagged rather than enforced.

**When to override:** unique/luxury/rural subjects may justify different bands or weighting.
Prefer fixing inputs first (correct sqft/year), then overriding `criteria`/`rules`, then
capturing a playbook if the override recurs.

## Adjusted vs bracketed
- **Adjusted** (magnitude derived from the comps): recency/time, size, beds, baths, garage.
- **Bracketed** (filtered, not adjusted; imbalance disclosed): age (±10yr), radius/location (3km).
  $/sqft remains the normalizer, not an adjustment.
