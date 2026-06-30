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
    dv = derive_time_trend(_subject(sqft=2000), recent + older, as_of=AS_OF)
    assert dv.method in ("matched_pair", "grouping", "regression")
    assert dv.value > 0
    assert -0.02 <= dv.value <= 0.02


def test_time_trend_none_when_too_few():
    dv = derive_time_trend(_subject(), [_comp(800_000)], as_of=AS_OF)
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
    dv = derive_time_trend(s, older + recent, as_of=AS_OF)
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
    dv = derive_time_trend(_subject(sqft=2000), recent + older, as_of=AS_OF)
    assert dv.method == "matched_pair"
    assert len(dv.pairs) >= 1
    assert dv.pairs[0].comp_a and dv.pairs[0].comp_b


def test_time_trend_pairs_must_be_feature_identical():
    # A time matched-pair must isolate time: same beds/baths/garage/type, differing only in
    # sale date (size already controlled by $/sqft + the ±5% size match). Size-matched but
    # feature-different sales must NOT form a time pair — else a bed/bath premium leaks in as
    # "appreciation". (a/b are 3-bed twins; c/d are 4-bed twins; cross pairs are forbidden.)
    s = _subject(sqft=2000)
    comps = [
        _comp(800_000, sqft=2000, d=date(2025, 12, 1), beds=3, baths=2, garage=2, addr="a3_old"),
        _comp(860_000, sqft=2000, d=date(2026, 5, 1),  beds=3, baths=2, garage=2, addr="b3_new"),
        _comp(900_000, sqft=2000, d=date(2026, 5, 1),  beds=4, baths=3, garage=2, addr="c4_new"),
        _comp(840_000, sqft=2000, d=date(2025, 12, 1), beds=4, baths=3, garage=2, addr="d4_old"),
    ]
    dv = derive_time_trend(s, comps, as_of=AS_OF)
    assert dv.method == "matched_pair"
    by_addr = {c.address: c for c in comps}
    feats = lambda c: (c.beds, c.baths, c.garage, c.property_type)
    for p in dv.pairs:
        assert feats(by_addr[p.comp_a]) == feats(by_addr[p.comp_b]), \
            f"time pair mixes features: {p.comp_a} vs {p.comp_b}"
    assert len(dv.pairs) >= 2          # still finds the legit 3-bed and 4-bed twin pairs


def test_time_trend_not_clamped_in_hot_market():
    # A genuinely hot market (>2%/mo) must come through, not be pinned at an artificial rail.
    # Identical homes 6 mo apart, $400 -> $500/sqft = +25% over 6 mo ≈ +4.2%/mo.
    s = _subject(sqft=2000)
    comps = [
        _comp(800_000, sqft=2000, d=date(2025, 12, 1), beds=3, baths=2, garage=2, addr="o1"),
        _comp(802_000, sqft=2000, d=date(2025, 12, 1), beds=3, baths=2, garage=2, addr="o2"),
        _comp(1_000_000, sqft=2000, d=date(2026, 6, 1), beds=3, baths=2, garage=2, addr="r1"),
        _comp(1_004_000, sqft=2000, d=date(2026, 6, 1), beds=3, baths=2, garage=2, addr="r2"),
    ]
    dv = derive_time_trend(s, comps, as_of=AS_OF)      # no clamp argument anymore
    assert dv.method == "matched_pair"
    assert dv.value > 0.02                              # would have been pinned at 0.02 before


def test_size_pair_uses_small_size_gaps():
    # No 8% floor: feature-identical comps only ~4% apart in size still form a size pair
    # (previously dropped, forcing a fall to grouping).
    s = _subject(sqft=1500)
    comps = [_comp(p, sqft=sq, beds=3, baths=2, garage=2, addr=a) for a, p, sq in [
        ("a", 500_000, 1500), ("b", 512_000, 1560), ("c", 503_000, 1510), ("d", 509_000, 1545)]]
    dv = derive_marginal_ppsf(s, comps, [c.sold_price for c in comps])
    assert dv.method == "matched_pair"
    assert dv.value > 0


def test_size_pair_keeps_negative_slopes_two_sided():
    # Two-sided: a pair where the bigger home sold for less (negative slope) is KEPT, so the
    # median reflects all the evidence rather than only the positive pairs.
    s = _subject(sqft=1500)
    comps = [_comp(p, sqft=sq, beds=3, baths=2, garage=2, addr=a) for a, p, sq in [
        ("a", 500_000, 1500), ("b", 540_000, 1700), ("c", 560_000, 1900), ("d", 555_000, 2000)]]
    dv = derive_marginal_ppsf(s, comps, [c.sold_price for c in comps])
    assert dv.method == "matched_pair"
    assert dv.value > 0
    assert any(p.value < 0 for p in dv.pairs)          # a negative pair survived into the set


def test_feature_unit_keeps_negative_pair_two_sided():
    # Features mirror size: per-pair cap removed (negatives kept); the sanity cap applies to
    # the FINAL median, not each pair.
    s = _subject(beds=3, baths=2, garage=2)
    comps = [_comp(0, sqft=1500, beds=3, baths=2, garage=g, addr=a)
             for a, g in [("g1", 1), ("g2", 2), ("g3", 3)]]
    residuals = [100_000, 115_000, 110_000]            # g2->g3 implies a negative per-garage value
    dv = derive_feature_unit(s, comps, residuals, "garage")
    assert dv.method == "matched_pair"
    assert any(p.value < 0 for p in dv.pairs)          # negative pair kept (two-sided)
    assert dv.value > 0                                # final median positive & within cap


def _c(price, sqft, addr, *, beds=3, baths=2, garage=2, d=date(2026, 5, 1)):
    # property_type is always known here so the STRICT path can fire on the known attributes;
    # the unknown axis under test is passed explicitly (garage=None / baths=None).
    return Comp(address=addr, lat=51.05, lng=-114.08, sold_price=price, sold_date=d,
                sqft=sqft, year_built=1985, beds=beds, baths=baths, garage=garage,
                property_type="detached")


def test_size_strict_drops_two_unknowns():
    # Two comps both with unknown garage must NOT pair as "matching" when strict pairs are
    # plentiful (the old tuple compare wrongly treated None == None as equal).
    s = _subject(sqft=1500)
    comps = [_c(500_000, 1400, "k1"), _c(520_000, 1500, "k2"), _c(540_000, 1600, "k3"),
             _c(505_000, 1450, "u1", garage=None), _c(545_000, 1650, "u2", garage=None)]
    dv = derive_marginal_ppsf(s, comps, [c.sold_price for c in comps])
    assert dv.method == "matched_pair"
    pair_sets = [{p.comp_a, p.comp_b} for p in dv.pairs]
    assert {"u1", "u2"} not in pair_sets          # two-unknown pair dropped under strict


def test_size_relaxes_to_nullsafe_when_strict_sparse():
    # Only ONE all-known feature-identical pair exists (<3), so it relaxes to null-safe and
    # forms an unknown-vs-known pair it would otherwise drop.
    s = _subject(sqft=1500)
    comps = [_c(500_000, 1400, "k1"), _c(540_000, 1600, "k2"),
             _c(520_000, 1500, "u1", garage=None), _c(560_000, 1700, "u2", garage=None)]
    dv = derive_marginal_ppsf(s, comps, [c.sold_price for c in comps])
    assert dv.method == "matched_pair"
    pair_sets = [{p.comp_a, p.comp_b} for p in dv.pairs]
    assert {"k1", "u1"} in pair_sets              # unknown-vs-known pair formed only by relaxing


def test_time_trend_relaxes_to_nullsafe_when_strict_sparse():
    # Same strict-then-relax rule on time pairs: one strict pair (<3) -> relax -> a one-sided
    # unknown-garage pair forms.
    s = _subject(sqft=2000)
    comps = [_c(800_000, 2000, "ko", d=date(2025, 12, 1)), _c(860_000, 2000, "kr", d=date(2026, 6, 1)),
             _c(804_000, 2000, "uo", garage=None, d=date(2025, 12, 1)),
             _c(862_000, 2000, "ur", garage=None, d=date(2026, 6, 1))]
    dv = derive_time_trend(s, comps, as_of=AS_OF)
    assert dv.method == "matched_pair"
    pair_sets = [{p.comp_a, p.comp_b} for p in dv.pairs]
    assert {"ko", "ur"} in pair_sets or {"kr", "uo"} in pair_sets


def test_feature_unit_strict_drops_unknown_when_pairs_plentiful():
    # Features now go strict-first too: with >=3 all-known garage pairs, comps with unknown
    # baths are dropped (was always null-safe before, so they used to be included).
    s = _subject(garage=2)
    comps = [_c(0, 1500, "a", garage=1), _c(0, 1500, "b", garage=2),
             _c(0, 1500, "e", garage=1), _c(0, 1500, "f", garage=2),
             _c(0, 1500, "c", garage=1, baths=None), _c(0, 1500, "d", garage=2, baths=None)]
    residuals = [100_000, 115_000, 101_000, 116_000, 103_000, 118_000]
    dv = derive_feature_unit(s, comps, residuals, "garage")
    assert dv.method == "matched_pair"
    addrs = {p.comp_a for p in dv.pairs} | {p.comp_b for p in dv.pairs}
    assert "c" not in addrs and "d" not in addrs  # unknown-baths comps dropped under strict


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
    # fixture uses integer-divisor sqft gaps, so median-of-rounded == round-of-median here
    assert dv.value == round(median([p.value for p in dv.pairs]), 2)


def test_feature_unit_emits_pair_traces():
    s = _subject(garage=2)
    comps = [_comp(700_000, sqft=1800, garage=1, addr="a"),
             _comp(712_000, sqft=1800, garage=2, addr="b"),
             _comp(705_000, sqft=1850, garage=1, addr="c"),
             _comp(718_000, sqft=1850, garage=2, addr="d")]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "garage")
    assert dv.method == "matched_pair"
    assert len(dv.pairs) >= 1
    assert "garage" in dv.pairs[0].detail


def test_half_bath_value_capped_tightly():
    # a confounded half-bath (~$20k/half) must be rejected by the tight half-bath cap, not applied.
    from mcp_server.derivation import derive_feature_unit, _FEATURE_CAP
    assert _FEATURE_CAP["half_baths"] <= 15000 < _FEATURE_CAP["full_baths"]
    s = _subject(baths=2.1)   # 2 full + 1 half
    comps = [_comp(0, sqft=1500, beds=3, baths=2.0, garage=2, addr="h0a"),
             _comp(0, sqft=1500, beds=3, baths=2.0, garage=2, addr="h0b"),
             _comp(0, sqft=1500, beds=3, baths=2.1, garage=2, addr="h1a"),
             _comp(0, sqft=1500, beds=3, baths=2.1, garage=2, addr="h1b")]
    residuals = [500_000, 502_000, 520_000, 522_000]   # implies ~$20k per half-bath
    dv = derive_feature_unit(s, comps, residuals, "half_baths")
    assert dv.method == "none"


def test_half_bath_never_exceeds_full_bath():
    # reconcile guard: if half derives above full, the half-bath isn't adjusted.
    from datetime import date
    from mcp_server.models import Subject, Comp, AdjustmentRules
    from mcp_server.estimate import reconcile
    s = Subject(address="S", lat=51.0, lng=-114.0, sqft=1500, year_built=2010, beds=3, baths=2.1, garage=2)
    # full-bath pairs imply a small $; half-bath pairs imply a large $ (> full) -> half dropped.
    comps = [
        Comp(address="a", lat=51, lng=-114, sold_price=500_000, sold_date=date(2026,4,1), sqft=1500, beds=3, baths=2.0, garage=2),  # full2 half0
        Comp(address="b", lat=51, lng=-114, sold_price=508_000, sold_date=date(2026,4,1), sqft=1500, beds=3, baths=3.0, garage=2),  # full3 half0
        Comp(address="c", lat=51, lng=-114, sold_price=525_000, sold_date=date(2026,4,1), sqft=1500, beds=3, baths=2.1, garage=2),  # full2 half1
        Comp(address="d", lat=51, lng=-114, sold_price=533_000, sold_date=date(2026,4,1), sqft=1500, beds=3, baths=3.1, garage=2),  # full3 half1
    ]
    est = reconcile(s, comps, AdjustmentRules(), as_of=date(2026,6,1))
    hb = next(c for c in est.coefficients if c.factor == "half_baths")
    fb = next(c for c in est.coefficients if c.factor == "full_baths")
    assert not (hb.value and fb.value and hb.value > fb.value)   # invariant holds


def test_feature_unit_year_built_recovers_rate():
    # comps alike except year built; residuals ~ $2,000 per year newer
    s = _subject(yb=2010)
    comps = [_comp(700_000, yb=1980), _comp(702_000, yb=1980),
             _comp(760_000, yb=2010), _comp(762_000, yb=2010)]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "year_built")
    assert dv.method in ("matched_pair", "grouping", "regression")
    assert 1000 <= dv.value <= 4000


def test_feature_unit_year_built_capped_when_implausible():
    # $10,000/yr is above the $4,000 cap -> rejected -> none
    s = _subject(yb=2010)
    comps = [_comp(700_000, yb=1980), _comp(701_000, yb=1980),
             _comp(1_000_000, yb=2010), _comp(1_001_000, yb=2010)]
    residuals = [c.sold_price for c in comps]
    dv = derive_feature_unit(s, comps, residuals, "year_built")
    assert dv.method == "none" and dv.value == 0.0


def test_feature_unit_year_built_none_without_variation():
    s = _subject(yb=2000)
    comps = [_comp(700_000, yb=2000), _comp(705_000, yb=2000)]
    dv = derive_feature_unit(s, comps, [c.sold_price for c in comps], "year_built")
    assert dv.method == "none" and dv.value == 0.0
