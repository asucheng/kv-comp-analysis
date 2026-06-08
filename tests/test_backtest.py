from datetime import date
from eval.backtest import hold_one_out
from tests.stubs import StubCompSource


def test_hold_one_out_reports_median_error():
    result = hold_one_out(StubCompSource(), lat=51.05, lng=-114.07,
                          as_of=date(2026, 6, 1))
    assert result.n >= 4
    assert 0 <= result.median_abs_pct_error < 60
    assert len(result.per_property) == result.n
