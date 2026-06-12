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
History as a real transaction. The data source is **pluggable** (`CompSource`): KV can swap
in MLS/DDF, Land Titles, or internal deal records.

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
Claude Desktop loads skills from its own store, **not** from a folder on disk — so you
upload the packaged skill zip through the Desktop UI (a file copy won't appear). The repo
ships a prebuilt zip at `dist/comp-analysis.zip`; rebuild it any time the skill changes with
`./scripts/package-skill.sh`.

In Claude Desktop: **Settings → Capabilities → Skills → Import** (or the skill **＋ / Upload**
control), choose `dist/comp-analysis.zip` from this repo, and confirm. The `comp-analysis`
skill should now appear in your skills list.

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
v1: residential, Calgary-validated. Subject **search** (`getMultiSearch`) is nationwide, so
addresses anywhere in Canada resolve; **comp coverage**, however, varies by region (Alberta/BC
strong, Ontario sparse) and accuracy is validated only for Calgary — where there are no comps,
the agent says so. Documented extensions: per-market comp validation, commercial, real SOLD
feeds via `CompSource`. See `docs/superpowers/specs/2026-06-06-kv-comp-analysis-design.md`.
