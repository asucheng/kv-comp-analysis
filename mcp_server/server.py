from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Optional
from mcp_server.models import (
    Subject, FindCompsResult, Estimate, CrossCheck, Criteria, AdjustmentRules,
)
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.honestdoor import HonestDoorCompSource
from mcp_server.geocode import Geocoder, NominatimGeocoder
from mcp_server.comps import find_with_ladder
from mcp_server.estimate import reconcile

# Fetch candidates out to the widening ladder's max radius so relaxation has data.
FETCH_RADIUS_KM = 8.0

_SUBJECT_FIELDS = ["community", "lat", "lng", "sqft", "year_built",
                   "beds", "baths", "garage", "lot_sf", "property_type"]


@dataclass
class Tools:
    """Plain callables holding the business logic — wrapped by FastMCP below
    and reused directly in tests (no transport needed)."""
    source: CompSource
    as_of: date
    geocoder: Optional[Geocoder] = None

    def get_subject(self, address: str, overrides: Optional[dict] = None) -> Subject:
        overrides = overrides or {}
        # Fuzzy text search => ranked candidates (best first). Take the top hit's
        # attributes; the agent confirms `resolved_address` before valuing.
        candidates = self.source.search_subject(address)
        top = candidates[0] if candidates else None
        rec = top or PropertyRecord(address=address)
        data = {"address": address}
        provenance: dict[str, str] = {}
        for f in _SUBJECT_FIELDS:
            if f in overrides and overrides[f] is not None:
                data[f] = overrides[f]; provenance[f] = "user"
            elif getattr(rec, f, None) is not None:
                data[f] = getattr(rec, f); provenance[f] = "honestdoor"
            else:
                provenance[f] = "missing"
        # Fall back to the geocoder for coordinates when the source had no match.
        if (self.geocoder and provenance.get("lat") == "missing"
                and provenance.get("lng") == "missing"):
            coords = self.geocoder.geocode(address)
            if coords:
                data["lat"], data["lng"] = coords
                provenance["lat"] = provenance["lng"] = "geocoded"
        data["hd_estimate"] = rec.hd_estimate
        data["provenance"] = provenance
        data["resolved_address"] = top.resolved_address if top else None
        data["match_candidates"] = [c.resolved_address for c in candidates[1:5]
                                    if c.resolved_address]
        return Subject(**data)

    @staticmethod
    def _require(subject: Subject, fields: list[str]) -> None:
        missing = [f for f in fields if getattr(subject, f) is None]
        if missing:
            raise ValueError(
                "Subject is missing required field(s): "
                + ", ".join(missing)
                + ". Ask the user to provide them (or correct the address)."
            )

    def find_comps(self, subject: Subject, criteria: Optional[Criteria] = None) -> FindCompsResult:
        criteria = criteria or Criteria()
        self._require(subject, ["lat", "lng", "sqft"])
        candidates = self.source.recent_sales(
            lat=subject.lat, lng=subject.lng, radius_km=FETCH_RADIUS_KM,
            lookback_months=criteria.lookback_months, as_of=self.as_of)
        return find_with_ladder(subject, candidates, criteria, as_of=self.as_of)

    def estimate_value(self, subject: Subject, comps: list, *,
                       rules: Optional[AdjustmentRules] = None, ladder_depth: int = 0) -> Estimate:
        self._require(subject, ["sqft"])
        return reconcile(subject, comps, rules or AdjustmentRules(),
                         as_of=self.as_of, ladder_depth=ladder_depth)

    def cross_check(self, subject: Subject, estimate_point: float) -> CrossCheck:
        rec = self.source.get_property(subject.address)
        notes, vs_avm, vs_assess = [], None, None
        if rec.hd_estimate:
            vs_avm = round((estimate_point - rec.hd_estimate) / rec.hd_estimate * 100, 1)
            notes.append(f"{vs_avm:+.1f}% vs HonestDoor AVM (estimate, not a sale)")
        if rec.assessed_value:
            vs_assess = round((estimate_point - rec.assessed_value) / rec.assessed_value * 100, 1)
            notes.append(f"{vs_assess:+.1f}% vs municipal assessment")
        worst = max((abs(v) for v in (vs_avm, vs_assess) if v is not None), default=0.0)
        verdict = "consistent" if worst <= 10 else ("review" if worst <= 20 else "divergent")
        return CrossCheck(hd_avm=rec.hd_estimate, assessed_value=rec.assessed_value,
                          vs_avm_pct=vs_avm, vs_assessment_pct=vs_assess,
                          verdict=verdict, notes=notes)


def build_tools(source: Optional[CompSource] = None, geocoder: Optional[Geocoder] = None,
                as_of: Optional[date] = None) -> Tools:
    return Tools(
        source=source or HonestDoorCompSource(),
        geocoder=geocoder if geocoder is not None else NominatimGeocoder(),
        as_of=as_of or date.today(),
    )


def main() -> None:
    """Console entry point: register the tools with FastMCP over stdio."""
    from fastmcp import FastMCP
    tools = build_tools()  # live HonestDoor data + Nominatim geocoder
    mcp = FastMCP("kv-comp-analysis")

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True})
    def get_subject(address: str, overrides: Optional[dict] = None) -> dict:
        """Resolve a residential subject from an address by searching the data source.
        Auto-fills attributes from the best-match property and marks each field's
        provenance (user|honestdoor|missing). Search is fuzzy: ALWAYS confirm the
        returned `resolved_address` matches the user's intended address before valuing —
        if it differs, is ambiguous, or is null, ask the user to approve or correct it
        (`match_candidates` lists other near matches). Returns attributes, not a value."""
        return tools.get_subject(address, overrides).model_dump()

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True})
    def find_comps(subject: dict, criteria: Optional[dict] = None) -> dict:
        """Find comparable recent sales near a subject and filter/rank by KV's house
        rules (radius, size, recency, age; ranked by similarity). Applies a widening
        ladder if too few. Takes the subject object from get_subject."""
        crit = Criteria(**criteria) if criteria else Criteria()
        return tools.find_comps(Subject(**subject), crit).model_dump(by_alias=True)

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False})
    def estimate_value(subject: dict, comps: list, rules: Optional[dict] = None,
                       ladder_depth: int = 0) -> dict:
        """Estimate the subject's value from comps via the adjustment grid + weighted
        reconciliation. Pure computation, no network. Takes comps from find_comps."""
        r = AdjustmentRules(**rules) if rules else AdjustmentRules()
        from mcp_server.models import Comp
        cs = [Comp(**c) for c in comps]
        return tools.estimate_value(Subject(**subject), cs, rules=r, ladder_depth=ladder_depth).model_dump()

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True})
    def cross_check(subject: dict, estimate_point: float) -> dict:
        """Sanity-check an estimate against the HonestDoor AVM and municipal assessment.
        Returns deltas and a verdict (consistent|review|divergent)."""
        return tools.cross_check(Subject(**subject), estimate_point).model_dump()

    mcp.run()  # stdio transport — local, no hosting


if __name__ == "__main__":
    main()
