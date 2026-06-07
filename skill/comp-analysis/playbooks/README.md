# Playbooks — capturing underwriter expertise

Each playbook is a small, reusable method an underwriter taught the agent. The baseline
(`../references/`) is the floor; playbooks raise the ceiling.

## How matching works
Before an analysis, scan this folder and read each play's `when:` line. If one matches the
subject, apply its method and tell the user you used it.

## Capture loop — "make my way into a skill"
When the user departs from the baseline, gets a better result, and asks you to remember it:
1. **Reflect** — what differed from the baseline, and why.
2. **Generalize** — into a reusable method (not a transcript of this one property).
3. **Confirm** — draft the play, show it, get approval/edits. Never save silently.
4. **Write** — `playbooks/<kebab-name>.md` using the template below.
5. **Apply** — on future runs whose subject matches `when:`.

Default to a new playbook in THIS skill (expand). Only propose a separate skill if the
method is a genuinely different domain (e.g. commercial).

## Template
```markdown
---
name: <kebab-name>
when: <trigger conditions that make this play apply>
author: <underwriter>   date: <YYYY-MM-DD>   status: personal | shared
validated: "<optional hold-one-out result>"
---
Trigger:   <what situation this addresses>
Method:    1. <step> (which tool params to change, e.g. criteria/rules)
           2. <step>
Rationale: <why this beats the baseline here>
```
