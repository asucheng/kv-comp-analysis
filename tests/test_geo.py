from mcp_server.geo import haversine_km


def test_haversine_zero_distance():
    assert haversine_km(51.05, -114.07, 51.05, -114.07) == 0.0


def test_haversine_known_distance():
    # Calgary downtown to Calgary airport ~ 12-15 km
    d = haversine_km(51.045, -114.057, 51.131, -114.010)
    assert 9 < d < 16


from mcp_server.geo import bbox


def test_bbox_returns_lon_lat_corners_containing_radius():
    lat, lng, r = 51.03, -114.06, 3.0
    top_left, bottom_right = bbox(lat, lng, r)
    # [lon, lat] order
    west_lon, north_lat = top_left
    east_lon, south_lat = bottom_right
    assert west_lon < lng < east_lon          # subject between east/west
    assert south_lat < lat < north_lat        # subject between north/south
    # box must fully contain the radius circle: corner distance >= r along each axis
    from mcp_server.geo import haversine_km
    assert haversine_km(lat, lng, north_lat, lng) >= r - 0.01   # north edge >= r away
    assert haversine_km(lat, lng, lat, east_lon) >= r - 0.01    # east edge >= r away


def test_bbox_wider_in_longitude_than_latitude_degrees():
    # at 51N, a degree of longitude is shorter than a degree of latitude,
    # so the lon half-span (in degrees) must be larger than the lat half-span
    (wl, nl), (el, sl) = bbox(51.0, -114.0, 3.0)
    lat_span = nl - sl
    lon_span = el - wl
    assert lon_span > lat_span
