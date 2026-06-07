# KV / Sam's house rules

Default comp-selection criteria (the `criteria` defaults in `find_comps`):

- **Radius:** within 3 km of the subject.
- **Size:** within ±20% of subject sqft.
- **Recency:** sold within the last 6–12 months.
- **Price/sqft:** the primary normalizer and ranking metric.
- **Age:** within ±10 years of the subject.

**Widening ladder** when comps are sparse — relax one step at a time, in this order, logging
each: time (12→18→24 mo) → radius (3→5→8 km) → size (±20→30→40%) → age (±10→20→30 yr).

**When to override:** unique/luxury/rural subjects may justify different bands or weighting.
Prefer fixing inputs first (correct sqft/year), then overriding `criteria`/`rules`, then
capturing a playbook if the override recurs.
