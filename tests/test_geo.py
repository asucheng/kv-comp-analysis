from mcp_server.geo import haversine_km


def test_haversine_zero_distance():
    assert haversine_km(51.05, -114.07, 51.05, -114.07) == 0.0


def test_haversine_known_distance():
    # Calgary downtown to Calgary airport ~ 12-15 km
    d = haversine_km(51.045, -114.057, 51.131, -114.010)
    assert 9 < d < 16
