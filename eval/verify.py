from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_set.json"


def load_golden_set(path: Path = GOLDEN_PATH) -> list[dict]:
    return json.loads(Path(path).read_text())


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
