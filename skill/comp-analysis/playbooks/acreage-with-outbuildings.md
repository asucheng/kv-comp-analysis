---
name: acreage-with-outbuildings
when: subject is an acreage / non-standard rural lot, or has significant outbuildings
author: example   date: 2026-06-07   status: shared
validated: "illustrative example — not yet backtested"
---
Trigger:   community-boundary comp search returns poor comps for rural/acreage subjects
Method:    1. widen radius early (criteria.radius_km up to 8) and search along road/river
              corridors rather than relying on a single community
           2. weight lot size and land value more heavily; note outbuildings explicitly
           3. add an outbuilding premium as a line item when reconciling
Rationale: rural value is driven by land + structures the standard $/sqft grid underweights
