from __future__ import annotations
import html
import re
from mcp_server.models import ReportPayload, CoefficientTrace, Estimate, Subject

CONF_COLOR = {"high": "#1a7f37", "medium": "#9a6700", "low": "#b42318"}

# Project-level disclaimers — identical every run, so they live here, not in the payload.
PROJECT_WARNINGS = [
    ("Baseline value only",
     "This figure is a comps-derived baseline. It excludes condition, rehab/repair, deferred "
     "maintenance and transaction fees, and may need further feature-level adjustments. Adjust "
     "it up or down for property-specific factors before relying on it."),
    ("No location / community adjustment",
     "This analysis assumes location does not affect value within the 3 km search radius. In real "
     "North American markets community matters a great deal; a per-community adjustment is a focus "
     "of future work."),
    ("Market scope: AB / BC",
     "The system is calibrated on Alberta (Calgary) sales. It can run on other regions, but "
     "accuracy outside AB / BC may be lower than tested."),
    ("Data limitations",
     "Sold history is drawn from a ~180-day, per-community window, and only sold transactions are "
     "used as comps. Any third-party AVM is an estimate, not a sale."),
]


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "—"


def _pct(v) -> str:
    return f"{v*100:+.2f}%" if v is not None else "—"


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "report"


def _subject_section(s: Subject) -> str:
    prov = s.provenance or {}
    rows = [
        ("Community", s.community, "community"),
        ("Property type", s.property_type, "property_type"),
        ("Size", f"{s.sqft:,.0f} sqft" if s.sqft else None, "sqft"),
        ("Year built", s.year_built, "year_built"),
        ("Beds", s.beds, "beds"),
        ("Baths", s.baths, "baths"),
        ("Garage", s.garage, "garage"),
        ("Lot", f"{s.lot_sf:,.0f} sf" if s.lot_sf else None, "lot_sf"),
    ]
    body = "".join(
        f"<tr><th>{_esc(label)}</th><td>{_esc(val) if val is not None else '—'}</td>"
        f"<td class='prov'>{_esc(prov.get(key, ''))}</td></tr>"
        for label, val, key in rows)
    return f"<section><h2>Subject</h2><table class='kv'>{body}</table></section>"


def _warnings_section(target: list[str]) -> str:
    tgt = "".join(f"<div class='warn target'>{_esc(w)}</div>" for w in target)
    proj = "".join(
        f"<div class='warn project'><strong>{_esc(t)}.</strong> {_esc(b)}</div>"
        for t, b in PROJECT_WARNINGS)
    return f"<section class='warnings'><h2>Warnings</h2>{tgt}{proj}</section>"


def _comp_row(rc) -> str:
    c = rc.comp
    dist = f"{c.distance_km:.1f} km" if c.distance_km is not None else "—"
    return (f"<tr><td>{_esc(c.address)}</td><td>{_money(c.sold_price)}</td>"
            f"<td>{_esc(c.sold_date)}</td><td>{c.sqft:,.0f}</td>"
            f"<td>${c.price_per_sqft:,.0f}</td><td>{dist}</td>"
            f"<td>{_esc(c.include_reason or '')}</td></tr>")


def _comps_section(comps) -> str:
    kept = [rc for rc in comps if rc.kept]
    kept.sort(key=lambda rc: (rc.comp.distance_km is None, rc.comp.distance_km or 0))
    excluded = [rc for rc in comps if not rc.kept]
    head = ("<thead><tr><th>Address</th><th>Sold</th><th>Date</th><th>Sqft</th>"
            "<th>$/sqft</th><th>Dist</th><th>Why included</th></tr></thead>")
    top = "".join(_comp_row(rc) for rc in kept[:10])
    out = [f"<section><h2>Comparable sales</h2>"
           f"<p class='muted'>{len(kept)} comps used (closest 10 shown).</p>"
           f"<table class='comps'>{head}<tbody>{top}</tbody></table>"]
    if len(kept) > 10:
        rest = "".join(_comp_row(rc) for rc in kept[10:])
        out.append(f"<details><summary>Show {len(kept)-10} more comps</summary>"
                   f"<table class='comps'>{head}<tbody>{rest}</tbody></table></details>")
    if excluded:
        exrows = "".join(
            f"<tr><td>{_esc(rc.comp.address)}</td><td>{_money(rc.comp.sold_price)}</td>"
            f"<td>${rc.comp.price_per_sqft:,.0f}</td><td>{_esc(rc.exclude_reason or '')}</td></tr>"
            for rc in excluded)
        out.append(f"<details><summary>Excluded ({len(excluded)})</summary>"
                   f"<table class='comps'><thead><tr><th>Address</th><th>Sold</th>"
                   f"<th>$/sqft</th><th>Reason excluded</th></tr></thead>"
                   f"<tbody>{exrows}</tbody></table></details>")
    return "".join(out) + "</section>"


def _trace_table(c: CoefficientTrace) -> str:
    if c.pairs:
        rows = "".join(
            f"<tr><td>{_esc(p.comp_a)}</td><td>{_esc(p.comp_b)}</td><td>{_esc(p.detail)}</td>"
            f"<td>{_pct(p.value) if c.is_pct else _money(p.value)}</td></tr>" for p in c.pairs)
        return ("<table class='trace'><thead><tr><th>Comp A</th><th>Comp B</th>"
                f"<th>Arithmetic</th><th>Implies</th></tr></thead><tbody>{rows}</tbody></table>")
    src = c.groups or c.regression
    if src:
        rows = "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in src.items())
        return f"<table class='trace'><tbody>{rows}</tbody></table>"
    return f"<p class='muted'>{_esc(c.summary)}</p>"


def _applied_table(factor: str, per_comp) -> str:
    rows = []
    for ca in per_comp:
        for a in ca.adjustments:
            if a.factor == factor and (a.value_dollar or a.value_pct):
                amt = _pct(a.value_pct) if a.value_pct is not None else _money(a.value_dollar)
                rows.append(f"<tr><td>{_esc(ca.address)}</td><td>{amt}</td>"
                            f"<td>{_esc(a.evidence)}</td></tr>")
    if not rows:
        return ""
    return ("<h4>Applied to comps</h4><table class='trace'><thead><tr><th>Comp</th>"
            "<th>Adjustment</th><th>Basis</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def _coeff_tile(c: CoefficientTrace, per_comp) -> str:
    if c.value == 0:
        val = "not adjusted"
    elif c.is_pct:
        val = f"{c.value*100:+.3f}%/mo"
    else:
        val = f"${c.value:,.0f}"
    chip = f"<span class='chip {_esc(c.confidence)}'>{_esc(c.confidence)}</span>"
    body = (f"<p class='eq'>{_esc(c.equation)}</p>{_trace_table(c)}"
            f"<p class='agg'>{_esc(c.aggregate)}</p>{_applied_table(c.factor, per_comp)}")
    return (f"<details class='tile'><summary><span class='factor'>{_esc(c.factor)}</span>"
            f"<span class='val'>{_esc(val)}</span><span class='method'>{_esc(c.method)}</span>"
            f"{chip}</summary><div class='tilebody'>{body}</div></details>")


def _adjustments_section(est: Estimate) -> str:
    tiles = "".join(_coeff_tile(c, est.per_comp) for c in est.coefficients)
    return (f"<section><h2>Adjustments</h2>"
            f"<p class='muted'>Click a tile to see the comps and arithmetic behind each number.</p>"
            f"<div class='tiles'>{tiles}</div></section>")


def _disclosures_section(est: Estimate) -> str:
    if not est.disclosures:
        return ""
    items = "".join(
        f"<div class='disc'><strong>{_esc(d.factor)}</strong> — {_esc(d.skew)} "
        f"(<em>likely {_esc(d.direction)}</em>). {_esc(d.caveat)}</div>" for d in est.disclosures)
    return f"<section><h2>Disclosures</h2>{items}</section>"


def _list_section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{_esc(x)}</li>" for x in items)
    # title is trusted developer-supplied text; escape only special HTML chars that
    # would break markup, but preserve apostrophes so test tokens match verbatim.
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<section><h2>{safe_title}</h2><ul>{lis}</ul></section>"


_CSS = """
:root{--ink:#1f2933;--muted:#667085;--line:#e4e7ec;--bg:#f5f6f8;--card:#fff;--accent:#1e3a5f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:32px 20px 64px}
header.hero{background:var(--accent);color:#fff;border-radius:14px;padding:28px 32px;margin-bottom:24px}
header.hero .addr{font-size:14px;opacity:.85;letter-spacing:.02em}
header.hero .label{font-size:12px;opacity:.7;text-transform:uppercase;letter-spacing:.06em;margin-top:14px}
header.hero .value{font-size:42px;font-weight:700;margin:4px 0}
header.hero .range{font-size:15px;opacity:.9}
.badge{display:inline-block;padding:4px 12px;border-radius:999px;font-size:13px;font-weight:600;
color:#fff;margin-top:10px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px 24px;margin:16px 0}
h2{font-size:18px;margin:0 0 14px}h4{margin:14px 0 6px;font-size:14px}
.muted{color:var(--muted);font-size:13px;margin:0 0 12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
table.kv th{width:130px;color:var(--muted);font-weight:500}.prov{color:var(--muted);font-size:11px}
.warnings .warn{border-radius:9px;padding:11px 14px;margin:8px 0;font-size:13px}
.warn.target{background:#fff4ed;border:1px solid #f9b98a;color:#8a3b12}
.warn.project{background:#eef4fb;border:1px solid #c5d9ef;color:#234a73}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
details.tile{border:1px solid var(--line);border-radius:10px;background:#fafbfc;overflow:hidden}
details.tile>summary{list-style:none;cursor:pointer;padding:12px 14px;display:flex;
flex-wrap:wrap;align-items:center;gap:8px}
details.tile>summary::-webkit-details-marker{display:none}
.factor{font-weight:600;text-transform:capitalize}.val{font-variant-numeric:tabular-nums;
font-weight:700;color:var(--accent)}.method{font-size:11px;color:var(--muted)}
.chip{margin-left:auto;font-size:11px;padding:2px 9px;border-radius:999px;color:#fff}
.chip.high{background:#1a7f37}.chip.medium{background:#9a6700}.chip.low{background:#b42318}
.tilebody{padding:0 14px 14px;border-top:1px solid var(--line)}
.eq{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:#f2f4f7;
padding:8px 10px;border-radius:7px;color:#344054}
.agg{font-weight:600;font-size:13px;margin:10px 0}
table.trace td:last-child{font-variant-numeric:tabular-nums;white-space:nowrap}
.disc{font-size:13px;padding:9px 0;border-bottom:1px solid var(--line)}.disc:last-child{border:0}
ul{margin:0;padding-left:20px;font-size:13px}li{margin:4px 0}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:20px}
@media print{body{background:#fff}section,details.tile{break-inside:avoid}}
"""


def render_report_html(payload: ReportPayload) -> str:
    s, est = payload.subject, payload.estimate
    color = CONF_COLOR.get(est.confidence, "#667085")
    addr = s.resolved_address or s.address
    drivers = est.method_notes[-1] if est.method_notes else ""
    hero = (f"<header class='hero'><div class='addr'>{_esc(addr)}</div>"
            f"<div class='label'>Baseline value</div>"
            f"<div class='value'>{_money(est.point)}</div>"
            f"<div class='range'>Range {_money(est.low)} – {_money(est.high)} "
            f"(25th–75th percentile of adjusted comps)</div>"
            f"<span class='badge' style='background:{color}'>"
            f"{_esc(est.confidence)} confidence</span></header>")
    conf = (f"<section><h2>Confidence &amp; reasoning</h2>"
            f"<p>{_esc(payload.confidence_reasoning)}</p>"
            f"<p class='muted'>{_esc(drivers)}</p></section>")
    body = "".join([
        hero,
        _warnings_section(payload.target_warnings),
        _subject_section(s),
        conf,
        _comps_section(payload.comps),
        _adjustments_section(est),
        _disclosures_section(est),
        _list_section("Not in this number", [
            "Condition, rehab and deferred maintenance are out of scope.",
            "Mark the baseline down for repairs, or up for recent renovation, before use.",
        ]),
        _list_section("What I'd verify next", payload.verify_next),
        f"<footer>Source: HonestDoor sold history (~180-day window). "
        f"Generated {_esc(payload.as_of)}. Scope: AB / BC, calibrated on Calgary.</footer>",
    ])
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Comp report — {_esc(addr)}</title><style>{_CSS}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")
