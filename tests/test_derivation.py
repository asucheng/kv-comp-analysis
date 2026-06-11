from datetime import date
from mcp_server.models import Subject, Comp
from mcp_server.derivation import (
    linreg_slope,
    derive_time_trend,
    derive_marginal_ppsf,
    derive_feature_unit,
    compute_disclosures,
)

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
    dv = derive_time_trend(_subject(sqft=2000), recent + older, as_of=AS_OF, clamp=0.02)
    assert dv.method in ("matched_pair", "grouping", "regression")
    assert dv.value > 0
    assert -0.02 <= dv.value <= 0.02


def test_time_trend_none_when_too_few():
    dv = derive_time_trend(_subject(), [_comp(800_000)], as_of=AS_OF, clamp=0.02)
    assert dv.method == "none" and dv.value == 0.0


def _subject(sqft=1800, beds=3, baths=2, garage=2, yb=1985):
    return Subject(address="S", lat=51.05, lng=-114.08, sqft=sqft, year_built=yb,
                   beds=beds, baths=baths, garage=garage)


def test_marginal_ppsf_grouping_recovers_rate():
    s = _subject(sqft=1800)
    # larger homes cost a bit more in total but less per sqft -> ~ $50/sqft marginal
    comps = [_comp(700_000, sqft=1800), _comp(705_000, sqft=1800),
             _comp(710_000, sqft=2000), _comp(715_000, sqft=2000)]
    prices = [c.sold_price for c in comps]
    dv = derive_marginal_ppsf(s, comps, prices)
    assert dv.method in ("matched_pair", "grouping", "regression")
    assert 20 <= dv.value <= 120     # sane marginal rate, well below avg ppsf (~$380)


def test_marginal_ppsf_none_without_size_spread():
    s = _subject(sqft=1800)
    comps = [_comp(700_000, sqft=1800), _comp(702_000, sqft=1800)]
    dv = derive_marginal_ppsf(s, comps, [c.sold_price for c in comps])
    assert dv.method == "none" and dv.value == 0.0


def test_feature_unit_garage_grouping():
    s = _subject(garage=2)
    # 2-car comps ~ $15k above 1-car comps (residuals already size/time-netted)
    comps = [_comp(700_000, garage=1), _comp(702_000, garage=1),
             _comp(716_000, garage=2), _comp(718_000, garage=2)]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "garage")
    assert dv.method in ("matched_pair", "grouping", "regression")
    assert 8000 <= dv.value <= 25000


def test_feature_unit_none_without_variation():
    s = _subject(baths=2)
    comps = [_comp(700_000, baths=2), _comp(705_000, baths=2)]
    dv = derive_feature_unit(s, comps, [c.sold_price for c in comps], "baths")
    assert dv.method == "none" and dv.value == 0.0


def test_disclosure_flags_older_comp_skew():
    s = _subject(yb=2015)
    comps = [_comp(700_000, yb=2005), _comp(700_000, yb=2006), _comp(700_000, yb=2007)]
    ds = compute_disclosures(s, comps, as_of=AS_OF)
    age = next(d for d in ds if d.factor == "age")
    assert age.direction == "understate"   # comps older -> may understate newer subject


def test_disclosure_quiet_when_balanced():
    s = _subject(yb=2010)
    comps = [_comp(700_000, yb=2009), _comp(700_000, yb=2011), _comp(700_000, yb=2010)]
    ds = compute_disclosures(s, comps, as_of=AS_OF)
    age = next((d for d in ds if d.factor == "age"), None)
    assert age is None or age.direction == "unknown"


def test_time_trend_not_fooled_by_recent_larger_comps():
    # Flat market, but recent comps are larger (lower $/sqft). Naive grouping would read
    # a fake decline; size control must keep the trend near zero, not pinned negative.
    s = _subject(sqft=1800)
    older = [Comp(address="o1", lat=51.05, lng=-114.08, sold_price=720_000, sold_date=date(2025,12,1),
                  sqft=1800, year_built=1985, beds=3, baths=2, garage=2),
             Comp(address="o2", lat=51.05, lng=-114.08, sold_price=716_000, sold_date=date(2026,1,1),
                  sqft=1790, year_built=1985, beds=3, baths=2, garage=2)]
    recent = [Comp(address="r1", lat=51.05, lng=-114.08, sold_price=730_000, sold_date=date(2026,5,1),
                   sqft=2000, year_built=1985, beds=3, baths=2, garage=2),
              Comp(address="r2", lat=51.05, lng=-114.08, sold_price=735_000, sold_date=date(2026,6,1),
                   sqft=2010, year_built=1985, beds=3, baths=2, garage=2)]
    dv = derive_time_trend(s, older + recent, as_of=AS_OF, clamp=0.02)
    assert dv.value > -0.02   # not pinned at the negative clamp rail
    assert abs(dv.value) < 0.015   # close to flat, not a fake double-digit decline


def test_time_disclosure_on_size_imbalance():
    s = _subject(sqft=1800)
    comps = [Comp(address="o1", lat=51.05, lng=-114.08, sold_price=720_000, sold_date=date(2025,12,1),
                  sqft=1800, year_built=1985, beds=3, baths=2, garage=2),
             Comp(address="o2", lat=51.05, lng=-114.08, sold_price=716_000, sold_date=date(2026,1,1),
                  sqft=1800, year_built=1985, beds=3, baths=2, garage=2),
             Comp(address="r1", lat=51.05, lng=-114.08, sold_price=730_000, sold_date=date(2026,5,1),
                  sqft=2100, year_built=1985, beds=3, baths=2, garage=2),
             Comp(address="r2", lat=51.05, lng=-114.08, sold_price=735_000, sold_date=date(2026,6,1),
                  sqft=2100, year_built=1985, beds=3, baths=2, garage=2)]
    ds = compute_disclosures(s, comps, as_of=AS_OF)
    assert any(d.factor == "time" for d in ds)


def test_feature_unit_matched_pair_isolates_one_unit():
    s = _subject(garage=2)
    # pairs alike except garage -> per-ONE-garage value (~$12k), never a 2-garage block
    comps = [_comp(700_000, sqft=1800, garage=1), _comp(712_000, sqft=1800, garage=2),
             _comp(705_000, sqft=1850, garage=1), _comp(718_000, sqft=1850, garage=2)]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "garage")
    assert dv.method == "matched_pair"
    assert 7_000 <= dv.value <= 20_000


def test_feature_unit_rejects_confounded_value():
    s = _subject(garage=2)
    # garage correlates with size/price and NO clean matched pair exists (sizes differ
    # >10%) -> the confounded ~$300k/garage grouping must be rejected, not applied.
    comps = [_comp(600_000, sqft=1700, garage=1), _comp(610_000, sqft=1700, garage=1),
             _comp(900_000, sqft=2200, garage=2), _comp(910_000, sqft=2200, garage=2)]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "garage")
    assert dv.method == "none"


def test_time_trend_emits_pair_traces():
    recent = [_comp(860_000, d=date(2026, 6, 1), addr="r1"),
              _comp(862_000, d=date(2026, 5, 1), addr="r2")]
    older = [_comp(800_000, d=date(2025, 12, 1), addr="o1"),
             _comp(804_000, d=date(2026, 1, 1), addr="o2")]
    dv = derive_time_trend(_subject(sqft=2000), recent + older, as_of=AS_OF, clamp=0.02)
    assert dv.method == "matched_pair"
    assert len(dv.pairs) >= 1
    assert dv.pairs[0].comp_a and dv.pairs[0].comp_b


def test_marginal_ppsf_uses_median_of_all_pairs_with_traces():
    s = _subject(sqft=1800)
    # three matched pairs alike except size (>=8% apart), implying ~$50-60/sqft
    comps = [_comp(700_000, sqft=1800, addr="a"), _comp(710_000, sqft=2000, addr="b"),
             _comp(702_000, sqft=1800, addr="c"), _comp(715_000, sqft=2000, addr="d"),
             _comp(704_000, sqft=1800, addr="e"), _comp(719_000, sqft=2000, addr="f")]
    prices = [c.sold_price for c in comps]
    dv = derive_marginal_ppsf(s, comps, prices)
    assert dv.method == "matched_pair"
    assert len(dv.pairs) >= 3          # all qualifying pairs recorded, not just the first
    from statistics import median
    assert dv.value == round(median([p.value for p in dv.pairs]), 2)
