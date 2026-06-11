from __future__ import annotations
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from mcp_server.models import (
    Subject, FindCompsResult, Estimate, CrossCheck, Criteria, AdjustmentRules, Overrides,
    ReportComp, ReportPayload,
)
from mcp_server.compsource.base import CompSource, PropertyRecord
from mcp_server.compsource.honestdoor import HonestDoorCompSource
from mcp_server.geocode import Geocoder, NominatimGeocoder
from mcp_server.comps import find_with_ladder
from mcp_server.estimate import reconcile
from mcp_server.report import render_report_html, slug

# Fetch the candidate pool at Sam's HARD limits. Radius is never widened (3 km is a
# hard limit), and recency is fetched at the ladder's 12-month cap so the only
# sanctioned widen (6 -> 12 mo) has data. Every other filter is a subset applied
# locally in comps.py, so this fetch is complete for all of them.
FETCH_RADIUS_KM = 3.0
FETCH_LOOKBACK_MONTHS = 12

_SUBJECT_FIELDS = ["community", "lat", "lng", "sqft", "year_built",
                   "beds", "baths", "garage", "parking_type", "lot_sf", "property_type"]


@dataclass
class Tools:
    """Plain callables holding the business logic — wrapped by FastMCP below
    and reused directly in tests (no transport needed)."""
    source: CompSource
    as_of: date
    geocoder: Optional[Geocoder] = None
    # In-process handoff: estimate_value stashes {subject, comps, estimate} here under an
    # id; render_report looks it up by id so the agent never re-emits the big payload.
    _cache: dict = field(default_factory=dict)

    def get_subject(self, address: str, overrides: Optional[dict] = None) -> Subject:
        overrides = overrides or {}
        # Fuzzy text search => ranked candidates (best first). Take the top hit's
        # attributes; the agent confirms `resolved_address` before valuing.
        candidates = self.source.search_subject(address)
        top = candidates[0] if candidates else None
        # Enrich the chosen subject from its own MLS listing (garage, property type,
        # parking, more-reliable bed/bath) — the search result alone is sparse on these.
        if top is not None:
            top = self.source.enrich_subject(top)
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
            lookback_months=FETCH_LOOKBACK_MONTHS, as_of=self.as_of)
        return find_with_ladder(subject, candidates, criteria, as_of=self.as_of)

    def estimate_value(self, subject: Subject, comps: list, *,
                       rules: Optional[AdjustmentRules] = None,
                       overrides: Optional[dict] = None,
                       ladder_depth: int = 0) -> Estimate:
        self._require(subject, ["sqft"])
        ov = Overrides(**overrides) if overrides else None
        est = reconcile(subject, comps, rules or AdjustmentRules(),
                        as_of=self.as_of, ladder_depth=ladder_depth, overrides=ov)
        # Cache the full bundle under a content-derived id and hand back only the id on the
        # estimate. render_report rebuilds the report from the cache — the model passes the
        # id, not ~170K tokens of estimate+comps it would otherwise have to re-serialize.
        est.estimate_id = "est_" + hashlib.sha1(est.model_dump_json().encode()).hexdigest()[:8]
        self._cache[est.estimate_id] = {
            "subject": subject, "comps": list(comps), "estimate": est, "as_of": self.as_of}
        return est

    def render_from_estimate(self, estimate_id: str, *, confidence_reasoning: str = "",
                             target_warnings: Optional[list] = None,
                             verify_next: Optional[list] = None,
                             exclusions: Optional[list] = None,
                             out_dir: Optional[str] = None) -> str:
        """Render the report from a cached estimate_id plus the agent's small narrative.

        `exclusions` is a list of {"address", "reason"} to mark specific cached comps as
        curated-out — the agent names the comp, not the whole comp set."""
        bundle = self._cache.get(estimate_id)
        if bundle is None:
            raise ValueError(
                f"Estimate '{estimate_id}' not found — its cached comps/estimate are gone "
                "(server restarted, or estimate_value wasn't called this session). Re-run "
                "estimate_value, then call render_report with the new estimate_id.")
        excl = {e["address"]: e.get("reason") for e in (exclusions or [])}
        comps = [ReportComp(comp=c, kept=c.address not in excl,
                            exclude_reason=excl.get(c.address)) for c in bundle["comps"]]
        payload = ReportPayload(
            subject=bundle["subject"], comps=comps, estimate=bundle["estimate"],
            confidence_reasoning=confidence_reasoning, target_warnings=target_warnings or [],
            verify_next=verify_next or [], as_of=bundle["as_of"])
        return self.render_report(payload, out_dir=out_dir)

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

    def render_report(self, payload: ReportPayload, out_dir: Optional[str] = None) -> str:
        """Write the self-contained HTML report to disk; return its absolute path.

        The default output dir is ABSOLUTE ($KV_COMP_REPORTS_DIR, else ~/kv-comp-reports),
        never CWD-relative — Claude Desktop launches the stdio server from a non-writable
        working directory, so a relative "reports/" would fail with PermissionError. Falls
        back to the system temp dir if the primary location can't be written.
        """
        name = slug(payload.subject.resolved_address or payload.subject.address)[:80].rstrip("-")
        fname = f"{name}-{payload.as_of}.html"
        content = render_report_html(payload)
        candidates = ([out_dir] if out_dir is not None
                      else [_reports_dir(), os.path.join(tempfile.gettempdir(), "kv-comp-reports")])
        last_err: Optional[OSError] = None
        for d in candidates:
            try:
                os.makedirs(d, exist_ok=True)
                path = os.path.abspath(os.path.join(d, fname))
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return path
            except OSError as e:
                last_err = e
        raise last_err  # type: ignore[misc]  # candidates is non-empty, so last_err is set


def _reports_dir() -> str:
    """Absolute, writable default dir for generated reports — independent of the server's
    CWD (Claude Desktop launches stdio servers from a non-writable directory)."""
    return os.path.expanduser(os.environ.get("KV_COMP_REPORTS_DIR") or "~/kv-comp-reports")


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

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True,
                           "title": "Resolve subject"})
    def get_subject(address: str, overrides: Optional[dict] = None) -> dict:
        """Resolve a residential subject from an address by searching the data source.
        Auto-fills attributes from the best-match property and marks each field's
        provenance (user|honestdoor|missing). Search is fuzzy: ALWAYS confirm the
        returned `resolved_address` matches the user's intended address before valuing —
        if it differs, is ambiguous, or is null, ask the user to approve or correct it
        (`match_candidates` lists other near matches). Returns attributes, not a value."""
        return tools.get_subject(address, overrides).model_dump()

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True,
                           "title": "Find comps"})
    def find_comps(subject: dict, criteria: Optional[dict] = None) -> dict:
        """Find comparable recent sales near a subject and filter/rank by KV's house
        rules (radius, size, recency, age; ranked by similarity). Sam's hard limits
        (radius/size/age) never widen; if too few comps, it relaxes recency 6->12mo
        then the secondary match toggles. Takes the subject object from get_subject."""
        crit = Criteria(**criteria) if criteria else Criteria()
        return tools.find_comps(Subject(**subject), crit).model_dump(by_alias=True)

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True,
                           "openWorldHint": False, "title": "Estimate value from comps"})
    def estimate_value(subject: dict, comps: list, rules: Optional[dict] = None,
                       overrides: Optional[dict] = None, ladder_depth: int = 0) -> dict:
        """Estimate the subject's value from comps via market-derived adjustments
        (paired-sales/grouping/regression) blended by median. Pure computation, no
        network. Each adjustment reports its method, evidence and confidence; Tier-2
        dimensions (age, location) come back as `disclosures`, not adjustments. Pass
        `overrides` (e.g. {"garage_value": 10000}) to replace a derived coefficient.
        Takes comps from find_comps; pass the FULL comp set, not a display subset.
        Returns an `estimate_id` — pass THAT (not this whole object) to render_report."""
        r = AdjustmentRules(**rules) if rules else AdjustmentRules()
        from mcp_server.models import Comp
        cs = [Comp(**c) for c in comps]
        return tools.estimate_value(Subject(**subject), cs, rules=r,
                                    overrides=overrides, ladder_depth=ladder_depth).model_dump()

    @mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True,
                           "title": "Cross-check estimate"})
    def cross_check(subject: dict, estimate_point: float) -> dict:
        """Sanity-check an estimate against the HonestDoor AVM and municipal assessment.
        Returns deltas and a verdict (consistent|review|divergent)."""
        return tools.cross_check(Subject(**subject), estimate_point).model_dump()

    @mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": True,
                           "openWorldHint": False, "title": "Render HTML report"})
    def render_report(estimate_id: str, confidence_reasoning: str = "",
                      target_warnings: Optional[list] = None,
                      verify_next: Optional[list] = None,
                      exclusions: Optional[list] = None) -> dict:
        """Render the self-contained, interactive HTML comp report to disk; return its
        absolute path. Call this as the FINAL step, once the value is settled.

        Pass ONLY the `estimate_id` returned by estimate_value (the server still holds the
        subject, comps and full estimate for it) plus your small narrative — do NOT re-send
        the estimate or comps. `confidence_reasoning`: your one-paragraph why. `target_warnings`:
        subject-specific cautions, shown first. `verify_next`: what you'd check next.
        `exclusions`: list of {"address","reason"} to curate specific comps out of the report.
        Surface the returned path and a file:// link so the user can open it in a browser."""
        return {"path": tools.render_from_estimate(
            estimate_id, confidence_reasoning=confidence_reasoning,
            target_warnings=target_warnings or [], verify_next=verify_next or [],
            exclusions=exclusions or [])}

    mcp.run()  # stdio transport — local, no hosting


if __name__ == "__main__":
    main()
