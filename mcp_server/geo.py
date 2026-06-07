from __future__ import annotations
import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return round(2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a)), 3)


def bbox(lat: float, lng: float, radius_km: float) -> tuple[list[float], list[float]]:
    """Bounding box that fully contains the radius circle around (lat, lng).

    Returns (top_left, bottom_right) as [longitude, latitude] pairs, matching the
    HonestDoor GraphQL BboxInput convention:
      top_left     = north-west = [west_lon, north_lat]
      bottom_right = south-east = [east_lon, south_lat]
    """
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * math.cos(math.radians(lat)))
    top_left = [lng - dlng, lat + dlat]
    bottom_right = [lng + dlng, lat - dlat]
    return top_left, bottom_right
