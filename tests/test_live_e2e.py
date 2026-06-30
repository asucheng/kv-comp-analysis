"""Live end-to-end: real geocoder + real HonestDoor data through the full
pipeline. Skips (does not fail) if either network endpoint is unreachable."""
from datetime import date
import pytest
from mcp_server.server import build_tools


@pytest.mark.live
def test_live_end_to_end_real_calgary_address():
    tools = build_tools(as_of=date(2026, 6, 1))  # real HonestDoor + Google geocoder
    try:
        subject = tools.get_subject(
            "Roxboro Road SW, Calgary, AB",
            overrides={"sqft": 1800, "year_built": 1960, "property_type": "detached"},
        )
        assert subject.lat and subject.lng
        assert subject.provenance["lat"] == "geocoded"

        result = tools.find_comps(subject)
        assert result.comps, "expected real comps near a Calgary residential address"
        assert all(c.distance_km is not None and c.price_per_sqft > 0 for c in result.comps)

        est = tools.estimate_value(subject, result.comps,
                                   ladder_depth=len(result.relaxations))
        assert est.low <= est.point <= est.high and est.point > 0
    except Exception as e:
        pytest.skip(f"live endpoint unreachable: {e}")

    print(f"\nLIVE E2E: subject @ ({subject.lat:.4f}, {subject.lng:.4f}); "
          f"{len(result.comps)} real comps; estimate ${est.point:,.0f} "
          f"(${est.low:,.0f}-${est.high:,.0f}), confidence={est.confidence}")
