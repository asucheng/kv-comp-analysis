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
