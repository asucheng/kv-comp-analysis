from datetime import date
from mcp_server.models import Subject, Comp
from mcp_server.derivation import linreg_slope, derive_time_trend

AS_OF = date(2026, 6, 1)


def _comp(price, sqft=2000, d=date(2026, 5, 1), beds=3, baths=2, garage=2, yb=1985, addr="c"):
    return Comp(address=addr, lat=51.05, lng=-114.08, sold_price=price, sold_date=d,
                sqft=sqft, year_built=yb, beds=beds, baths=baths, garage=garage)


def test_linreg_slope_basic():
    assert linreg_slope([0, 1, 2, 3], [0, 2, 4, 6]) == 2.0
    assert linreg_slope([1, 1, 1], [1, 2, 3]) is None  # zero x-variance


def test_time_trend_grouping_detects_rising_market():
    # recent 3 (0-1 mo) ~ $430/sqft; older 3 (4-6 mo) ~ $405/sqft -> positive trend
    recent = [_comp(860_000, d=date(2026, 6, 1)), _comp(850_000, d=date(2026, 5, 1)),
              _comp(840_000, d=date(2026, 5, 1))]
    older = [_comp(800_000, d=date(2025, 12, 1)), _comp(820_000, d=date(2026, 1, 1)),
             _comp(810_000, d=date(2026, 2, 1))]
    dv = derive_time_trend(recent + older, as_of=AS_OF, clamp=0.02)
    assert dv.method in ("grouping", "regression")
    assert dv.value > 0
    assert -0.02 <= dv.value <= 0.02


def test_time_trend_none_when_too_few():
    dv = derive_time_trend([_comp(800_000)], as_of=AS_OF, clamp=0.02)
    assert dv.method == "none" and dv.value == 0.0
