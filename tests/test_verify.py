from eval.verify import load_golden_set


def test_golden_set_loads_ten_addressed_entries():
    rows = load_golden_set()
    assert len(rows) == 10
    assert all(r["address"] and r["label"] for r in rows)
    assert all("Calgary" in r["address"] for r in rows)
