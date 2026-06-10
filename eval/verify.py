from __future__ import annotations
import json
from pathlib import Path

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_set.json"


def load_golden_set(path: Path = GOLDEN_PATH) -> list[dict]:
    return json.loads(Path(path).read_text())
