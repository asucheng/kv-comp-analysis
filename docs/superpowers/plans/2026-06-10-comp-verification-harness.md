# Comp Verification Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A one-command, Claude-Code-driven regression test that reproduces the Claude Desktop comp-analysis experience over a fixed 10-address golden set and grades each result against HonestDoor's AVM (±10%), replacing manual Desktop verification.

**Architecture:** Deterministic, unit-tested grading logic lives in `eval/verify.py` (pure parse/grade/format + a live AVM fetch + a CLI). A project-local Claude Code skill `.claude/skills/comp-verify/` orchestrates: it fans out one **blind** `sonnet` subagent per golden address (each runs the real `comp-analysis` skill + `kv-comp-analysis` MCP), collects each agent's `RESULT:` line, then calls `eval/verify.py` to fetch AVMs and grade. The agents never see the AVM (blind grading), which is also why the AVM cross-check is removed from the product skill.

**Tech Stack:** Python 3.14, Pydantic v2, pytest; Claude Code skills + `.mcp.json`; the existing `mcp_server` package. Spec: `docs/superpowers/specs/2026-06-10-comp-verification-harness-design.md`.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `eval/golden_set.json` | The 10 fixed test addresses (`{address, label}`) | **Create** |
| `eval/verify.py` | Pure grading logic (parse/grade/format) + live `fetch_avm` + CLI `main` | **Create** |
| `tests/test_verify.py` | Unit tests for the pure functions + golden-set loadability | **Create** |
| `.claude/skills/comp-verify/SKILL.md` | The harness skill (orchestration: fan-out → collect → grade → report) | **Create** |
| `.mcp.json` | Wire the `kv-comp-analysis` stdio MCP into Claude Code | **Create** |
| `skill/comp-analysis/SKILL.md` | Remove the AVM cross-check from workflow + output (keep confidence) | **Modify** |

**Interface contract (locked here; later tasks must match these names):**

```python
# eval/verify.py
@dataclass
class Result:   point: float|None; low: float|None; high: float|None; resolved: str|None; status: str
@dataclass
class Verdict:  address: str; label: str; point: float|None; avm: float|None; delta_pct: float|None; verdict: str; note: str

def load_golden_set(path=GOLDEN_PATH) -> list[dict]
def parse_result_line(text: str) -> Result | None
def grade(address, label, result: Result|None, avm: float|None, avm_resolved: str|None=None, *, tol=0.10) -> Verdict
def fetch_avm(address: str, *, tools=None) -> tuple[float|None, str|None]
def format_report(verdicts: list[Verdict], *, tol=0.10) -> str
def main(results_path: str) -> int     # CLI: read [{address,label,output}] -> print report
```

---

## Task 1: Golden set + loadability test

**Files:**
- Create: `eval/golden_set.json`
- Test: `tests/test_verify.py`

- [ ] **Step 1: Create `eval/golden_set.json`**

```json
[
  {"address": "138 Cranberry Place SE Calgary",     "label": "detached / suburban / 2007 / 1-gar"},
  {"address": "301 - 1500 7 Street SW Calgary",     "label": "condo / downtown / underground"},
  {"address": "2028 41 Avenue SW Calgary",          "label": "detached infill / large / 3-gar"},
  {"address": "2319 24 Avenue SW Calgary",          "label": "semi / Killarney / 2-gar"},
  {"address": "2925 17 Street SW Calgary",          "label": "townhouse / Marda Loop / 2-gar"},
  {"address": "7132 36 Avenue NW Calgary",          "label": "detached / Bowness / 1978 / no garage"},
  {"address": "4635 79 Street NW Calgary",          "label": "semi / Bowness / 2-gar"},
  {"address": "205 - 4512 75 Street NW Calgary",    "label": "condo / Bowness / old / low-value"},
  {"address": "1419 10 Street SW Calgary",          "label": "townhouse / Beltline / 1989 / 1-gar"},
  {"address": "61 Auburn Meadows View SE Calgary",  "label": "semi / Auburn Bay / no garage (pad)"}
]
```

- [ ] **Step 2: Write the failing test** — create `tests/test_verify.py`:

```python
from eval.verify import load_golden_set


def test_golden_set_loads_ten_addressed_entries():
    rows = load_golden_set()
    assert len(rows) == 10
    assert all(r["address"] and r["label"] for r in rows)
    assert all("Calgary" in r["address"] for r in rows)
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_verify.py -q`
Expected: FAIL (`eval.verify` does not exist yet).

- [ ] **Step 4: Add the minimal loader to `eval/verify.py`** (create the file with just enough to pass):

```python
from __future__ import annotations
import json
from pathlib import Path

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_set.json"


def load_golden_set(path: Path = GOLDEN_PATH) -> list[dict]:
    return json.loads(Path(path).read_text())
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_verify.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add eval/golden_set.json eval/verify.py tests/test_verify.py
git commit -m "feat(verify): golden-set of 10 Calgary addresses + loader"
```

---

## Task 2: `parse_result_line`

**Files:**
- Modify: `eval/verify.py`
- Test: `tests/test_verify.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_verify.py`:

```python
from eval.verify import parse_result_line, Result


def test_parse_result_line_reads_last_result_line():
    text = ("...the analysis file...\n"
            "RESULT: point=532000 low=498000 high=559000 resolved=122 Auburn Bay Heights SE Calgary AB status=ok")
    r = parse_result_line(text)
    assert isinstance(r, Result)
    assert r.point == 532000.0 and r.low == 498000.0 and r.high == 559000.0
    assert r.resolved == "122 Auburn Bay Heights SE Calgary AB" and r.status == "ok"


def test_parse_result_line_strips_dollars_and_commas():
    r = parse_result_line("RESULT: point=$1,780,000 low=$1,600,000 high=$1,900,000 resolved=2028 41 Ave SW status=ok")
    assert r.point == 1780000.0


def test_parse_result_line_none_when_absent_or_malformed():
    assert parse_result_line("no result here") is None
    assert parse_result_line("RESULT: garbage") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_verify.py -q`
Expected: FAIL (`parse_result_line`/`Result` undefined).

- [ ] **Step 3: Add to `eval/verify.py`** (after the loader; add the imports `import re`, `from dataclasses import dataclass`, `from typing import Optional` at the top):

```python
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Result:
    point: Optional[float]
    low: Optional[float]
    high: Optional[float]
    resolved: Optional[str]
    status: str


def _to_float(s) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").replace("$", "").strip())
    except (ValueError, AttributeError):
        return None


_RESULT_RE = re.compile(
    r"point=(?P<point>\S+)\s+low=(?P<low>\S+)\s+high=(?P<high>\S+)\s+"
    r"resolved=(?P<resolved>.+?)\s+status=(?P<status>\w+)\s*$")


def parse_result_line(text: str) -> Optional[Result]:
    """Find the LAST line containing 'RESULT:' and parse it; None if absent/malformed."""
    for line in reversed(text.splitlines()):
        if "RESULT:" in line:
            m = _RESULT_RE.search(line)
            if not m:
                return None
            return Result(_to_float(m["point"]), _to_float(m["low"]), _to_float(m["high"]),
                          m["resolved"].strip(), m["status"].strip().lower())
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_verify.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add eval/verify.py tests/test_verify.py
git commit -m "feat(verify): parse the agent RESULT line"
```

---

## Task 3: `grade`

**Files:**
- Modify: `eval/verify.py`
- Test: `tests/test_verify.py`

- [ ] **Step 1: Write the failing tests** — append:

```python
from eval.verify import grade, Verdict


def _ok(point, resolved="61 Auburn Meadows View SE Calgary AB"):
    return Result(point, point*0.95, point*1.05, resolved, "ok")


def test_grade_pass_within_tolerance():
    v = grade("a", "lbl", _ok(484_000), 484_000, "61 Auburn Meadows View SE Calgary AB")
    assert v.verdict == "PASS" and abs(v.delta_pct) < 0.001


def test_grade_fail_when_beyond_tolerance():
    v = grade("a", "lbl", _ok(1_780_000), 2_130_800)  # ~ -16%; omit avm_resolved -> delta path
    assert v.verdict == "FAIL" and v.delta_pct < -0.10


def test_grade_fail_on_missing_or_nonok_result():
    assert grade("a", "l", None, 500_000).verdict == "FAIL"
    assert grade("a", "l", Result(None, None, None, "x", "ambiguous"), 500_000).verdict == "FAIL"


def test_grade_inconclusive_without_avm():
    assert grade("a", "l", _ok(500_000), None).verdict == "INCONCLUSIVE"


def test_grade_flags_resolved_mismatch():
    v = grade("a", "l", _ok(500_000, "999 Other St"), 500_000, "61 Auburn Meadows View SE Calgary AB")
    assert v.verdict == "FLAG"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_verify.py -q`
Expected: FAIL (`grade`/`Verdict` undefined).

- [ ] **Step 3: Add to `eval/verify.py`**:

```python
@dataclass
class Verdict:
    address: str
    label: str
    point: Optional[float]
    avm: Optional[float]
    delta_pct: Optional[float]   # signed fraction, e.g. -0.07
    verdict: str                 # PASS | FAIL | FLAG | INCONCLUSIVE
    note: str


def _norm(a: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (a or "").lower()).strip().rstrip(",")


def grade(address: str, label: str, result: Optional[Result], avm: Optional[float],
          avm_resolved: Optional[str] = None, *, tol: float = 0.10) -> Verdict:
    if result is None:
        return Verdict(address, label, None, avm, None, "FAIL", "no parseable RESULT line")
    if result.status != "ok":
        return Verdict(address, label, result.point, avm, None, "FAIL", f"agent status={result.status}")
    if result.point is None:
        return Verdict(address, label, None, avm, None, "FAIL", "RESULT had no point value")
    if avm is None:
        return Verdict(address, label, result.point, None, None, "INCONCLUSIVE", "no AVM to grade against")
    if avm_resolved and result.resolved and _norm(avm_resolved) != _norm(result.resolved):
        return Verdict(address, label, result.point, avm, None, "FLAG",
                       f"agent resolved '{result.resolved}' != AVM lookup '{avm_resolved}'")
    delta = (result.point - avm) / avm
    if abs(delta) <= tol:
        return Verdict(address, label, result.point, avm, delta, "PASS", "")
    return Verdict(address, label, result.point, avm, delta, "FAIL",
                   f"{delta*100:+.1f}% vs AVM exceeds +/-{tol*100:.0f}%")
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_verify.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add eval/verify.py tests/test_verify.py
git commit -m "feat(verify): grade a result vs AVM (pass/fail/flag/inconclusive)"
```

---

## Task 4: `fetch_avm`, `format_report`, and the CLI `main`

**Files:**
- Modify: `eval/verify.py`
- Test: `tests/test_verify.py`

- [ ] **Step 1: Write the failing tests** — append (these cover the pure parts; `fetch_avm`/`main` hit the network and are exercised by the Task 8 smoke run, not unit-tested):

```python
from eval.verify import format_report, fetch_avm


def test_format_report_has_header_and_rows():
    vs = [
        Verdict("138 Cranberry Place SE", "detached", 548_000, 552_000, -0.007, "PASS", ""),
        Verdict("2028 41 Avenue SW", "infill", 1_780_000, 2_130_800, -0.164, "FAIL", "-16.4% vs AVM exceeds +/-10%"),
    ]
    out = format_report(vs)
    assert "1/2 pass" in out
    assert "138 Cranberry Place SE" in out and "2028 41 Avenue SW" in out
    assert "median |delta|" in out


def test_fetch_avm_uses_injected_tools():
    class _Subj:
        hd_estimate = 484_000
        resolved_address = "61 Auburn Meadows View SE Calgary AB"
    class _Tools:
        def get_subject(self, addr): return _Subj()
    avm, resolved = fetch_avm("61 Auburn Meadows View SE Calgary", tools=_Tools())
    assert avm == 484_000 and resolved.startswith("61 Auburn Meadows View")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_verify.py -q`
Expected: FAIL (`format_report`/`fetch_avm` undefined).

- [ ] **Step 3: Add to `eval/verify.py`** (no new top-level imports needed — `json`/`Path` came in Task 1; `sys`, `date`, and `build_tools` are imported lazily where used, so importing `eval.verify` stays network-free for the unit tests):

```python
def fetch_avm(address: str, *, tools=None) -> tuple[Optional[float], Optional[str]]:
    """Live: resolve the subject and return (AVM, resolved_address). Inject `tools` in tests."""
    if tools is None:
        from datetime import date
        from mcp_server.server import build_tools
        tools = build_tools(as_of=date.today())
    s = tools.get_subject(address)
    return s.hd_estimate, s.resolved_address


def _money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.0f}k"


def format_report(verdicts: list[Verdict], *, tol: float = 0.10) -> str:
    passes = sum(1 for v in verdicts if v.verdict == "PASS")
    mark = {"PASS": "✓", "FAIL": "✗", "FLAG": "⚑", "INCONCLUSIVE": "?"}
    lines = [f"Comp verification (vs HonestDoor AVM, +/-{tol*100:.0f}% = pass) — {passes}/{len(verdicts)} pass"]
    for v in verdicts:
        d = "—" if v.delta_pct is None else f"{v.delta_pct*100:+.1f}%"
        lines.append(f" {mark.get(v.verdict, '?')}  {v.address:32} est {_money(v.point):>8}  "
                     f"AVM {_money(v.avm):>8}  {d:>7}  {v.label}"
                     + (f"  <- {v.note}" if v.note else ""))
    deltas = sorted(abs(v.delta_pct) for v in verdicts if v.delta_pct is not None)
    if deltas:
        lines.append(f" median |delta| {deltas[len(deltas)//2]*100:.1f}%")
    return "\n".join(lines)


def main(results_path: str) -> int:
    """CLI: read a JSON list of {address,label,output}, grade each (live AVM fetch), print report."""
    rows = json.loads(Path(results_path).read_text())
    verdicts = []
    for row in rows:
        res = parse_result_line(row.get("output", ""))
        avm, avm_resolved = fetch_avm(row["address"])
        verdicts.append(grade(row["address"], row.get("label", ""), res, avm, avm_resolved))
    print(format_report(verdicts))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1]))
```

- [ ] **Step 4: Run to verify pass + whole suite**

Run: `.venv/bin/python -m pytest tests/test_verify.py -q` then `.venv/bin/python -m pytest -q`
Expected: PASS (verify tests green; full suite still green).

- [ ] **Step 5: Commit**

```bash
git add eval/verify.py tests/test_verify.py
git commit -m "feat(verify): live AVM fetch, chat report formatter, CLI"
```

---

## Task 5: Remove the AVM cross-check from the product skill (keep confidence)

**Files:**
- Modify: `skill/comp-analysis/SKILL.md`

- [ ] **Step 1: Remove the cross_check workflow step.** In `skill/comp-analysis/SKILL.md`, replace:

```markdown
5. **`cross_check(subject, estimate.point)`** — compare to the HonestDoor AVM and the
   municipal assessment. Material divergence → investigate and explain; don't trust blindly.
6. **Present the file** (format below).
```

with:

```markdown
5. **Present the file** (format below).
```

- [ ] **Step 2: Remove the Cross-check output item.** Replace:

```markdown
6. **Not in this number** — condition/rehab/deferred maintenance are out of scope; suggest the
   user mark the baseline down for them.
7. **Cross-check** — vs AVM and assessment.
8. **What I'd verify next.**
```

with:

```markdown
6. **Not in this number** — condition/rehab/deferred maintenance are out of scope; suggest the
   user mark the baseline down for them.
7. **What I'd verify next.**
```

- [ ] **Step 3: Verify confidence is still present and cross_check is gone**

Run: `grep -n "confidence" skill/comp-analysis/SKILL.md && ! grep -n "cross_check\|Cross-check" skill/comp-analysis/SKILL.md && echo "OK: confidence kept, cross-check removed"`
Expected: prints the confidence lines and `OK: confidence kept, cross-check removed`.

(Note: the `cross_check` MCP tool in `mcp_server/server.py` stays — it's just no longer referenced by the skill. The "Honesty" judgment rule about never presenting an AVM as a comp stays unchanged.)

- [ ] **Step 4: Commit**

```bash
git add skill/comp-analysis/SKILL.md
git commit -m "feat(skill): drop AVM cross-check from comp-analysis output (keep confidence)"
```

---

## Task 6: Wire the MCP into Claude Code (`.mcp.json`)

**Files:**
- Create: `.mcp.json`

- [ ] **Step 1: Create `.mcp.json`** at the repo root:

```json
{
  "mcpServers": {
    "kv-comp-analysis": {
      "type": "stdio",
      "command": "/home/allen/Documents/KV_hackathon/.venv/bin/kv-comp-analysis",
      "args": []
    }
  }
}
```

- [ ] **Step 2: Verify the console entry exists and boots**

Run: `ls -l .venv/bin/kv-comp-analysis && timeout 3 .venv/bin/kv-comp-analysis </dev/null; echo "exit $?"`
Expected: the binary exists; it starts a stdio server and exits when stdin closes (a non-crash exit / timeout is fine — we're only confirming it launches).

- [ ] **Step 3: Commit**

```bash
git add .mcp.json
git commit -m "chore: wire kv-comp-analysis MCP into Claude Code (.mcp.json)"
```

Note: after this commit, **Claude Code must be restarted** (or the project re-opened) for the new MCP server to connect in-session. The Task 8 smoke run assumes it's connected.

---

## Task 7: The `comp-verify` harness skill

**Files:**
- Create: `.claude/skills/comp-verify/SKILL.md`

- [ ] **Step 1: Create `.claude/skills/comp-verify/SKILL.md`** with this exact content:

````markdown
---
name: comp-verify
description: Run the Desktop-faithful comp-analysis regression test over the golden set and report pass/fail vs HonestDoor's AVM. Use whenever the user asks to "run the desktop-like comp tests", "verify the comps", "run the comp regression", "test the comp analysis end to end", or sanity-check the comp engine after a change.
---

# Comp Verification Harness

Reproduce the Claude Desktop comp-analysis experience automatically over a fixed golden set,
and grade each result against HonestDoor's AVM (±10%). The test agents run **blind** — they
must never see the AVM or know they are being graded.

## Procedure

1. **Load the golden set.** Run:
   `.venv/bin/python -c "import json; print(json.dumps(json.load(open('eval/golden_set.json'))))"`
   This yields a list of `{address, label}` (10 entries).

2. **Fan out one blind subagent per address, in parallel.** Dispatch all of them in a SINGLE
   message (one `Agent` tool call each, `model: sonnet`, `subagent_type: general-purpose`).
   Use this EXACT prompt for each, substituting the address:

   ```
   You are running a comparable-sales valuation exactly as a user would in Claude Desktop.

   Use the `comp-analysis` skill to run a full comp analysis on this property:
     <ADDRESS>

   Rules:
   - Follow the comp-analysis skill as written, using the kv-comp-analysis MCP tools.
   - No human is available to confirm the address. If get_subject's resolved_address clearly
     refers to the same property, proceed. If it is genuinely ambiguous or resolves to a
     different property, do NOT guess — set status=ambiguous and stop.
   - If there are too few comps to value even after the widening ladder, set status=insufficient.
   - Do your normal analysis and present the file.
   - Do NOT look up, mention, or compare against any third-party AVM, "estimate", or assessment.
     Value the property purely from the comps.
   - As the VERY LAST line of your reply, output exactly one machine-readable line:
     RESULT: point=<integer dollars> low=<integer> high=<integer> resolved=<resolved address> status=<ok|ambiguous|insufficient>
     (plain integers, no $ or commas; status=ok only when you produced a point value.)
   ```

   Post a one-line progress note as each subagent returns.

3. **Collect + grade.** Assemble a JSON array of `{address, label, output}` where `output` is
   each subagent's full final text. Write it to `/tmp/comp_verify_results.json`, then run:
   `.venv/bin/python -m eval.verify /tmp/comp_verify_results.json`
   It fetches each AVM live and prints the report.

4. **Report in chat.** Relay the printed report. For each `FAIL`/`FLAG`, add a one-sentence quote
   from that agent's reasoning explaining the drift (e.g. "only 6 comps after widening").

## Notes
- Requires the `kv-comp-analysis` MCP connected in this session (see `.mcp.json`). If the
  subagents can't reach the MCP tools, stop and tell the user to restart Claude Code.
- This is a regression guard, not ground truth: a >10% delta is a prompt to investigate, not
  proof of error. The AVM is a stand-in professional.
````

- [ ] **Step 2: Verify the skill file is well-formed**

Run: `head -5 .claude/skills/comp-verify/SKILL.md && grep -c "RESULT: point=" .claude/skills/comp-verify/SKILL.md`
Expected: frontmatter present; the RESULT-line template appears (count ≥ 1).

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/comp-verify/SKILL.md
git commit -m "feat(comp-verify): harness skill — blind fan-out + AVM grading"
```

---

## Task 8: End-to-end smoke (manual verification)

**Files:** none (verification only). Requires Claude Code restarted so the MCP is connected (Task 6).

- [ ] **Step 1: Confirm the MCP is reachable in-session.** Check that `kv-comp-analysis` tools (`get_subject`, `find_comps`, `estimate_value`) are available. If not, restart Claude Code / re-open the project, then retry.

- [ ] **Step 2: Dry-run the grading path without agents.** Build a tiny fake results file and confirm the CLI fetches AVMs and grades:

```bash
.venv/bin/python - <<'PY'
import json, tempfile, subprocess
rows = [
  {"address": "61 Auburn Meadows View SE Calgary", "label": "semi / no garage",
   "output": "...\nRESULT: point=484000 low=460000 high=505000 resolved=61 Auburn Meadows View SE Calgary AB status=ok"},
  {"address": "2028 41 Avenue SW Calgary", "label": "infill",
   "output": "...\nRESULT: point=1780000 low=1600000 high=1900000 resolved=2028 41 Avenue SW Calgary AB status=ok"},
]
p = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump(rows, p); p.close()
subprocess.run([".venv/bin/python", "-m", "eval.verify", p.name])
PY
```
Expected: a report printed — `61 Auburn Meadows View` near 0% (PASS), `2028 41 Avenue` likely >10% (FAIL), with live AVMs filled in. This proves parse→fetch→grade→format works end-to-end against live data.

- [ ] **Step 3: Run the real harness on a 2-address subset.** Invoke the `comp-verify` skill but, for the smoke, fan out only the first 2 golden addresses (spawn 2 `sonnet` subagents with the Task-7 prompt). Confirm: each subagent used the `comp-analysis` skill + MCP, ended with a parseable `RESULT:` line, and the grading report renders. Fix any prompt/parse mismatch found.

- [ ] **Step 4: Full run + final suite.**

Run: `.venv/bin/python -m pytest -q`
Expected: all green. Then do one full 10-address `comp-verify` run and eyeball the report for sanity (most within ±10%; extremes #3/#8 may legitimately drift — note, don't "fix" the engine to chase the AVM).

- [ ] **Step 5: Commit (if any smoke-driven prompt/parse fixes were made)**

```bash
git add -A && git commit -m "fix(comp-verify): smoke-test adjustments to prompt/parsing" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Blind grading is sacred:** never put the AVM (or "you're being graded") into a subagent prompt. The agent values from comps only; grading happens after, in `eval/verify.py`.
- **Faithfulness:** subagents are pinned to `sonnet` (Desktop's model) and told to follow the skill literally — same address should give the same answer. LLM non-determinism remains (documented limitation).
- **The AVM is a yardstick, not truth.** Don't tune the engine to pass the AVM; a persistent >10% on a normal property is a signal to investigate the *reasoning*, not to chase the number.
- **MCP restart:** `.mcp.json` only takes effect after Claude Code reloads — Task 8 depends on it.
