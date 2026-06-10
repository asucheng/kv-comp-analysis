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
