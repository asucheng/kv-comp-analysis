from eval.verify import load_golden_set


def test_golden_set_loads_ten_addressed_entries():
    rows = load_golden_set()
    assert len(rows) == 10
    assert all(r["address"] and r["label"] for r in rows)
    assert all("Calgary" in r["address"] for r in rows)


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
