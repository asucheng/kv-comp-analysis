# KV Capital — Residential Comp-Analysis Agent

A local MCP server + a `comp-analysis` Skill that turns Claude Desktop into a residential
comp-analysis assistant for the Calgary market: given a subject property, it finds
comparable recent sales (KV's house rules), adjusts them, and produces a transparent,
underwriter-style value estimate — and can learn each underwriter's own methods.

## Why this shape
- **Augments an existing workflow** (Claude Desktop) — no new app to adopt.
- **Deterministic tools + judgment Skill:** four neutral read-only MCP tools do the
  mechanics; the Skill carries the underwriter methodology and orchestration.
- **Expandable:** underwriters teach it new methods via playbooks ("make my way into a skill").

## Data & honesty
Alberta sold prices are confidential on MLS, and municipal assessments lack sqft/beds/baths
and are valuations, not sales. The demo sources comps from **HonestDoor public data**
(real sold price/date + attributes), with a **synthetic, real-grounded fallback** for thin
areas / new construction. The HonestDoor headline price is an **AVM estimate, not a sale** —
the agent only treats Sold History as a real transaction. The data source is **pluggable**
(`CompSource`): KV can swap in MLS/DDF, Land Titles, or internal deal records.

## Install (local, no hosting)
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

Register with Claude Desktop (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "kv-comp-analysis": { "command": "kv-comp-analysis" }
  }
}
```
Copy `skill/comp-analysis/` into your Claude Desktop skills directory. Restart Claude Desktop.

## Use
> "Run a comp analysis on 123 Maple Dr, Roxboro, Calgary — it's a 2,000 sqft detached built 1985."

The agent resolves the subject, finds and curates comps, estimates value with an adjustment
grid, cross-checks against the AVM/assessment, and walks you through the file.

## Accuracy
Hold-one-out backtest against real sold prices:
```bash
python -c "from datetime import date; from eval.backtest import hold_one_out; \
from mcp_server.compsource.synthetic import SyntheticCompSource; \
r=hold_one_out(SyntheticCompSource(seed=1), community='Roxboro', as_of=date(2026,6,1)); \
print(f'median abs error {r.median_abs_pct_error}% over {r.n} sales')"
```
(Swap in `HonestDoorCompSource()` for a live real-data number — a representative sample-run figure.)

## Tests
```bash
pytest -q
```

## Scope
v1: residential, Calgary-first. Documented extensions: Edmonton, commercial, real SOLD feeds
via `CompSource`. See `docs/superpowers/specs/2026-06-06-kv-comp-analysis-design.md`.
