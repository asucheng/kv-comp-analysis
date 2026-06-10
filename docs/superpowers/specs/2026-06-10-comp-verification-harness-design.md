# Spec — Desktop-Faithful Comp Verification Harness

**Date:** 2026-06-10
**Author:** Allen (solo)
**Status:** Approved in brainstorming; ready for implementation plan
**Scope:** An automated, Claude-Code-driven regression test that reproduces the Claude Desktop comp-analysis experience over a fixed set of addresses and grades each result against HonestDoor's AVM — replacing manual Desktop verification.

---

## 1. Problem & goal

Every change to the `comp-analysis` skill or the `kv-comp-analysis` MCP currently has to be verified by hand in Claude Desktop: type an address, watch the agent find comps and estimate value, eyeball whether the number is sane. That's slow and unrepeatable.

**Goal:** one command in Claude Code runs the *same* experience automatically over a curated address set and reports pass/fail against a yardstick — so a regression shows up immediately, with no manual Desktop work.

**Two product changes that ship with this** (decided alongside the harness):
- **Keep** the `confidence` rating in the product output — it's derived from *our own* data quality (comp count, $/sqft dispersion, ladder depth, method strength), so it legitimately informs the user.
- **Remove** the AVM/assessment **cross-check from the product output and the skill workflow** — someone else's estimate shouldn't sit inside our conclusion. This *also* makes the harness honest (see §2): with `cross_check` out of the skill flow, the test agent never encounters the AVM while it works.

## 2. The governing principle — blind grading

The test agent must **never see the AVM (or that it is being graded)** while it performs the analysis. Otherwise it could anchor to the target and the test would be meaningless. Therefore:
- The agent runs a normal, blind comp analysis (no AVM, no "expected" value, no grading hints).
- The harness fetches the AVM and computes pass/fail **only after** the agent has produced its answer.

This is why dropping the AVM cross-check from the skill is load-bearing, not cosmetic.

## 3. Faithfulness (the "B" decision)

The harness reproduces Desktop as closely as practical, trading a little free judgment for reproducibility:
- **Same brain:** the test subagent is pinned to **`sonnet`** (Desktop runs Sonnet 4.6).
- **Same skill + tools:** it runs the real `comp-analysis` skill orchestrating the real `kv-comp-analysis` MCP — not a hand-coded tool sequence.
- **Constrained to execute the skill literally:** follow the documented method; pass the FULL comp set; rely on the engine's own outlier handling; no free-wheeling comp curation. Same address → same answer run-to-run.
- **Known, documented gap:** it is "Desktop-equivalent, not Desktop-exact" — identical skill/tools/model *family*, but LLM non-determinism remains. Acceptable for a regression guard.

## 4. The golden set

A fixed, version-controlled list of **10 real Calgary addresses** (in `eval/golden_set.json`), chosen to span the cases that stress the engine. Captured 2026-06-10 (AVMs are live and will drift slightly):

| # | Address | Type | Sqft | Yr | Garage | AVM (2026-06-10) |
|---|---|---|---|---|---|---|
| 1 | 138 Cranberry Place SE | detached | 1,416 | 2007 | 1 | $551,700 |
| 2 | 301 - 1500 7 Street SW | condo | 402 | 2013 | 1 | $265,300 |
| 3 | 2028 41 Avenue SW | detached | 3,268 | 1952 | 3 | $2,130,800 |
| 4 | 2319 24 Avenue SW | semi | 1,836 | 2014 | 2 | $905,300 |
| 5 | 2925 17 Street SW | townhouse | 1,506 | 2012 | 2 | $841,300 |
| 6 | 7132 36 Avenue NW | detached | 1,039 | 1978 | 0 | $656,000 |
| 7 | 4635 79 Street NW | semi | 1,827 | 1974 | 2 | $839,500 |
| 8 | 205 - 4512 75 Street NW | condo | 476 | 1978 | 0 | $112,500 |
| 9 | 1419 10 Street SW | townhouse | 1,502 | 1989 | 1 | $624,000 |
| 10 | 61 Auburn Meadows View SE | semi | 1,070 | 2014 | 0 | $484,000 |

Coverage: 3 detached / 3 semi / 2 townhouse / 2 condo; garages 0–3 (three no-garage, to exercise off-street→0); value $112k–$2.1M; vintage 1970s–2010s; dense suburb / downtown / inner-city / sparser NW. All 10 verified to resolve via `get_subject` and carry an AVM. The JSON stores `{address, label}` only — AVM and attributes are fetched live at run time.

## 5. Per-address flow

For each golden address the harness:
1. **Spawns a `sonnet` subagent** with the `comp-analysis` skill + `kv-comp-analysis` MCP and a **blind, user-style prompt**: *"Run a comp analysis on \<address\>."* plus minimal test-mode rules:
   - Follow the skill as written.
   - No human is present: if the resolved address clearly matches the input, proceed; if genuinely ambiguous, say so and stop.
   - End with exactly one machine-readable line: `RESULT: point=<int> low=<int> high=<int> resolved=<address> status=<ok|ambiguous|insufficient>`.
   - It is **not** told about the AVM or that it is being graded.
2. **Independently fetches the AVM** via `get_subject(address).hd_estimate` (separating the thing-under-test from the yardstick).
3. **Grades:**
   - `status=ok` and `|point − AVM| / AVM ≤ 0.10` → **PASS**.
   - `status=ok` and delta > 10% → **FAIL (drift)**.
   - `status=ambiguous|insufficient`, or no parseable `RESULT` line → **FAIL (flow broke)**.
   - Agent's `resolved` differs from the address the harness's AVM lookup resolved → **FLAG** (don't silently pass).
   - AVM missing/null → **INCONCLUSIVE** (not a pass or fail). (None expected for the golden set.)

## 6. Orchestration & trigger

- **Trigger:** a small **project-local skill** in `.claude/skills/comp-verify/` whose description fires on natural-language phrasing ("run the desktop-like comp tests", "verify the comps", "run the comp regression"). No slash-command ceremony required.
- **Fan-out:** the skill dispatches all 10 subagents **concurrently** (the `superpowers:dispatching-parallel-agents` pattern), posting a one-line progress note as each lands.
- **Dev tooling, not shipped:** the harness skill lives under `.claude/skills/` (Claude-Code-local), distinct from the shipped `skill/comp-analysis/` product skill.

## 7. Reporting

Chat only — no files written (run history is out of scope for v1). Final summary:

```
Comp verification (vs HonestDoor AVM, ±10% = pass) — 8/10 pass
 ✓  138 Cranberry Place SE   est $548k   AVM $552k   −0.7%   detached
 ✗  2028 41 Avenue SW        est $1.78M  AVM $2.13M  −16.4%  detached infill  ← over 10%
 …
 median |Δ| 4.2%  ·  fails: #3 (luxury infill), #8 (old condo)
```

For each FAIL, include the agent's own reasoning (a short quote from its analysis) so the cause is visible (e.g. "only 6 comps after widening; thin luxury market").

## 8. Prerequisite — wire the MCP into Claude Code

For subagents to run the skill as written, the `kv-comp-analysis` MCP must be connected in Claude Code. Add a project `.mcp.json` pointing at the console entry (`.venv/bin/kv-comp-analysis`, stdio), so the harness subagents inherit the tool. (It's already in `~/.claude.json` but not active in-session.)

## 9. Components

- **Create:** `eval/golden_set.json` (10 `{address, label}` entries).
- **Create:** `.claude/skills/comp-verify/SKILL.md` (the harness: load golden set → fan out blind subagents → fetch AVMs → grade → report).
- **Create:** `.mcp.json` (project-scoped `kv-comp-analysis` stdio server).
- **Modify (product changes):** `skill/comp-analysis/SKILL.md` — drop the `cross_check`/AVM step from the workflow (step 5) and the "Cross-check" item from the output "file"; keep the confidence line. The `cross_check` MCP **tool stays in `server.py`** (neutral, read-only, harmless) but is simply no longer invoked by the skill — retiring the tool itself is out of scope (avoids churn for no benefit).

## 10. Testing the harness itself

- A tiny unit test that the grading logic classifies correctly (pass within 10%, fail beyond, fail on missing RESULT, flag on resolved-mismatch) — pure function, no network.
- A `golden_set.json` schema/loadability test.
- The harness's end-to-end behavior is validated by running it once and sanity-checking the report.

## 11. Non-goals

- Not a replacement for `eval/backtest.py` (hold-one-out vs real *sold prices* — stronger ground truth, but tests the math in isolation). The two are complementary: backtest = accuracy of the math; this harness = end-to-end faithfulness of the whole Desktop experience.
- No run-history persistence, dashboards, or CI integration (v1 is chat-only, run on demand).
- Not Desktop-*exact* (LLM non-determinism remains; §3).
- The AVM is a convenience yardstick (a stand-in professional), not ground truth — a >10% delta is a prompt to investigate, not proof of error.
