# KV Capital — Residential Comp-Analysis Agent

A local MCP server + a `comp-analysis` Skill that turns Claude Desktop into a residential
comp-analysis assistant for the Calgary market: given a subject property, it finds
comparable recent sales (KV's house rules), adjusts them, and produces a transparent,
underwriter-style value estimate.

## Demo
▶️ **[Watch the demo (Loom)](https://www.loom.com/share/e63c037ec62143cea927f392e2473ac7)** — a
full run in Claude Desktop: enter an address, find comps under KV's house rules, derive the
adjustments, and open the interactive HTML report.

## The problem
When a home builder borrows from KV Capital, KV — the lender — needs to know **how much the
project is really worth** before lending against it. Establishing that value is the job of
**underwriting**, and within underwriting the most time-consuming step is **finding comparable
sales (comps)** and reasoning from them. This project uses AI to take that bottleneck off the
underwriter's desk — not to replace their judgment, but to do the legwork and lay the evidence
out for them.


## Approach
Most valuation tools on the internet hand back a single estimated number and quietly ask you
to trust it. **This one shows its work.** It doesn't just produce a baseline value — it walks
through *how* it got there: which comps it used, why those, and how the comps themselves imply
each adjustment across **time, size, and features**. The underwriter sees the reasoning, not a
black box.

**It lives where the underwriter already works — Claude Desktop.** No new app, web platform, or
system to learn: the project ships as an **MCP server + a Skill** installed into Claude Desktop.
The underwriter prompts Claude with a target address; Claude resolves the property, finds the
comps, derives the adjustments, and estimates the value. If the fetched property details are
wrong — or missing, as with new construction — the user **overrides** them inline before the
analysis runs. At the end, Claude returns a **link to a self-contained HTML report**: paste it
into a browser to explore every comp and every adjustment, with the evidence and arithmetic
behind the baseline value.

The comp-selection rules come from **Sam** — radius, size, recency, and age
bands plus a secondary match — encoded as the tool defaults the agent applies (and an
underwriter can override).

**How it handles the hard parts:**

- **Real, attributed data.** Comps come from **HonestDoor's public GraphQL backend** (real sold
  price + date sourced from Land Titles, plus living area / beds / baths / year). The headline
  HonestDoor figure is an **AVM estimate, not a sale** — only Sold History counts as a real
  transaction.
- **Fuzzy subject resolution.** Address search returns ranked guesses and never flags an exact
  match, so the agent **confirms the resolved address with the user before valuing** and offers
  alternates when the top hit looks off — a fuzzy neighbour never silently drives a valuation.
- **Adjustments derived from the comps, not invented.** A **two-tier** method: *quantifiable*
  factors (time, size, beds, full/half baths, garage) are **dollar-adjusted with magnitudes
  read from the comp set** via named appraisal methods — **matched pairs → grouping →
  regression**, strongest first — each tagged whether it came from the **cited source** or
  **our own judgment**; *bracketed* factors (age, location) are filtered but **not adjusted**,
  with the comp set's imbalance **disclosed** as a directional caveat instead.
- **Sparse or noisy comp sets.** When comps are thin, a **widening ladder** relaxes one rule at
  a time (time → radius → size → age), logs each relaxation, and lowers confidence accordingly.
  Implausible or confounded derivations are rejected by **plausibility caps** so the report
  shows an honest "not adjusted" rather than a fabricated number, and outliers are trimmed
  before reconciliation.
- **Explainable confidence, no borrowed anchors.** The high/medium/low rating is computed from
  *our own* data quality (comp count, $/sqft dispersion, ladder depth, method strength), and the
  AVM/assessment cross-check is deliberately kept **out of the valuation** so the conclusion
  never leans on someone else's estimate.
- **Tested, not just demoed.** A **blind golden-set regression harness** reruns the full Desktop
  experience over fixed Calgary addresses in Claude Code and grades each result against the AVM
  *after* the agent has answered — catching regressions without manual Desktop checks.

**Expandable:** when an underwriter teaches it a better method, the Skill captures it as a
reusable *playbook* ("make my way into a skill") — raising the ceiling for every future run
without new code. See **Data & honesty** below for the data source and how provenance is kept.

## Data & honesty
Alberta sold prices are confidential on MLS, and municipal assessments lack sqft/beds/baths
and are valuations, not sales. Comps come from **real HonestDoor public data** via its
GraphQL backend — unauthenticated, with real sold price/date + living area/beds/baths/year.
The subject's own attributes (sqft/beds/baths/year/coordinates) resolve from its address via
HonestDoor's **`getMultiSearch`** (the site's own address search). That search is fuzzy and
ranked — it always returns its closest guesses and never flags an exact match — so the tool
takes the top hit but the **agent confirms `resolved_address` with the user (a human-approve
gate) before valuing**, and offers `match_candidates` when the top hit looks wrong. This
keeps a fuzzy neighbour from silently driving a valuation. A free **OpenStreetMap/Nominatim
geocoder** remains as a coordinate fallback when nothing matches. The HonestDoor headline
price (`predictedValue`) is an **AVM estimate, not a sale** — the agent only treats Sold
History as a real transaction. 

## Setup on Claude Desktop (step by step)

Local, no hosting — the MCP server runs as a subprocess that Claude Desktop launches for
you. Three pieces have to be wired up: the **server code** (installed from this repo), the
**MCP registration** (in your Desktop config), and the **skill** (copied into your skills
folder). Follow all six steps once, then it just works.

### Prerequisites (dependencies)
- **Python ≥ 3.11** — check with `python3 --version`
- **git**
- **Claude Desktop** installed and signed in
- The Python packages `fastmcp`, `pydantic`, `httpx` — installed automatically in step 2,
  no manual action needed. Network access is required at runtime (the server calls
  HonestDoor's public GraphQL API and OpenStreetMap/Nominatim).

### 1. Clone the repo
```bash
git clone https://github.com/asucheng/kv-comp-analysis.git
cd kv-comp-analysis
```

### 2. Create a virtual environment and install
```bash
# macOS / Linux
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```
```powershell
# Windows (PowerShell)
py -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e .
```
`pip install -e .` pulls in all dependencies and creates the `kv-comp-analysis` launcher
inside `.venv`. (Editable install means Claude always runs your current code — `git pull`
updates take effect on the next Desktop restart, no reinstall.)

### 3. Get the absolute path to the launcher
Claude Desktop does **not** see your activated venv, so you must register the launcher by
its **full path**, not its bare name. Print it (venv still activated):
```bash
which kv-comp-analysis      # macOS / Linux  → e.g. /home/you/kv-comp-analysis/.venv/bin/kv-comp-analysis
```
```powershell
where.exe kv-comp-analysis  # Windows        → e.g. C:\Users\you\kv-comp-analysis\.venv\Scripts\kv-comp-analysis.exe
```
Copy that path for the next step.

### 4. Register the MCP server in your Claude Desktop config
Open (create if missing) `claude_desktop_config.json`:
| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Add the server, using the **path from step 3** as `command` (keep any existing
`mcpServers` entries):
```json
{
  "mcpServers": {
    "kv-comp-analysis": {
      "command": "/absolute/path/from/step/3/.venv/bin/kv-comp-analysis"
    }
  }
}
```
> Windows: use double backslashes in the path, e.g.
> `"C:\\Users\\you\\kv-comp-analysis\\.venv\\Scripts\\kv-comp-analysis.exe"`.

### 5. Install the skill
Claude Desktop loads skills from its own store, **not** from a folder on disk — so you upload
a packaged skill zip through the Desktop UI (a file copy won't appear).

First **build the zip** (it isn't checked in — `dist/` is git-ignored, so a fresh clone won't
have it):
```bash
./scripts/package-skill.sh        # writes dist/comp-analysis.zip
```
Then, in Claude Desktop: **Settings → Capabilities → Skills → Import** (or the skill
**＋ / Upload** control), choose the `dist/comp-analysis.zip` you just built, and confirm. The
`comp-analysis` skill should now appear in your skills list. (Re-run the script and re-import
whenever the skill changes.)

### 6. Fully restart Claude Desktop
**Quit completely** (not just close the window — on Linux/Windows it keeps running in the
background) and relaunch, so it spawns the server and loads the skill.

### Verify it worked
In a **new** chat, ask:
> "Run a comp analysis on 122 Auburn Bay Heights SE, Auburn Bay, Calgary."

It should resolve the subject (sqft, beds, baths, year) and proceed to comps — not ask you
for the square footage. If it does ask, see **Troubleshooting** below.

### Troubleshooting
- **"MCP server failed to start" / tools missing** — the `command` path is wrong or not
  absolute. Re-run step 3 and paste the exact path; confirm the file exists.
- **It keeps asking for square footage** — usually the running process predates a code
  change, or the chat cached a pre-fix result. Fully restart Desktop (step 6) **and** start
  a **new** conversation (an existing chat reuses the subject it already resolved).
- **Skill not triggering** — confirm `comp-analysis` shows up under Settings → Capabilities →
  Skills (re-import `dist/comp-analysis.zip` if not) and that you restarted Desktop.

## Use
> "Run a comp analysis on 123 Maple Dr, Roxboro, Calgary — it's a 2,000 sqft detached built 1985."

The agent resolves the subject, finds and curates comps, estimates value with an adjustment
grid, cross-checks against the AVM/assessment, and walks you through the file.

## Accuracy
Hold-one-out backtest against real HonestDoor sold prices (live network call):
```bash
python -c "from datetime import date; from eval.backtest import hold_one_out; \
from mcp_server.compsource.honestdoor import HonestDoorCompSource; \
r=hold_one_out(HonestDoorCompSource(), lat=51.05, lng=-114.07, as_of=date.today()); \
print(f'median abs error {r.median_abs_pct_error}% over {r.n} sales')"
```
(A representative sample-run figure — comps refresh live, so it is not byte-reproducible.)

## Tests
```bash
pytest -q
```

## Scope
- **Residential property only** — single-family and attached homes. Commercial and
  multi-family are out of scope (documented extensions).
- **Best in Alberta & BC.** Subject *search* is nationwide, so any Canadian address resolves —
  but **comp coverage** is strongest in **AB/BC** and sparse elsewhere (e.g. Ontario). Where
  there are no comps, the agent says so rather than guessing.
- **Verified on Calgary.** Accuracy is validated only for **Calgary**, and the golden-set
  regression addresses and the hold-one-out backtest are all **Calgary-based**. Other metros
  run, but aren't accuracy-checked yet.

See `docs/superpowers/specs/2026-06-06-kv-comp-analysis-design.md`.

## What I'd build next
The baseline value is deliberately *before* property-specific condition and neighbourhood
quality. The next two steps fold those in:

- **Photo-informed condition.** Take in listing or inspection **photos** to read condition,
  finish, and renovation level — moving from a comps-only baseline toward an accurate as-is
  value instead of leaving condition for the underwriter to mark down by hand.
- **Location & community signals.** Source neighbourhood data that genuinely moves price —
  **household income levels, school districts, crime rates, proximity to major roads (noise),
  and nearby value-adding amenities** — and factor it into both the estimate and the report.

Further out: richer, authoritative sold feeds through the pluggable `CompSource` (MLS/DDF, Land
Titles, or KV's internal deal records) for denser comps, and pooled feature derivation so sparse
comp sets can still value individual features instead of leaving them unadjusted.
