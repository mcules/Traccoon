"""Arithmetic on the sphere — as little as geofences and maps need.

PostGIS is not available: the image `postgres:16-alpine` does not bring the extension along,
and the tests run against SQLite. For the questions asked here — how far is this point from
the last one, does it stand inside this circle — the haversine formula in Python is entirely
enough: with a handful of places per person the loop over them is faster than a database could
even accept the query.
"""
from __future__ import annotations

import math

# Mean earth radius (IUGG). The error against the real ellipsoid is 0.3 % — with a fence of
# 150 m radius that is 45 cm, and the GPS of a phone scatters by about
# Zwanzigfache davon.
ERDRADIUS_M = 6_371_008.8


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Entfernung zweier Punkte in Metern."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * ERDRADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def frame(lat: float, lon: float, meter: float) -> tuple[float, float, float, float]:
    """The rectangle around a point: (lat_min, lat_max, lon_min, lon_max).

    For the preselection in SQL, once there are more points than one cares to walk through. The
    lines of longitude move together towards the poles, hence the cosine; right at the pole the
    arithmetic degenerates, and there the rectangle is opened to the full width.
    """
    # A touch more than was asked for. The frame is a preselection; were it accurate to the
    # metre, floating point rounding would occasionally cut off exactly the point that is still
    # inside. A per mille plus a metre costs nothing and prevents that.
    meter = meter * 1.001 + 1.0
    d_lat = math.degrees(meter / ERDRADIUS_M)
    kos = math.cos(math.radians(lat))
    d_lon = 180.0 if abs(kos) < 1e-9 else math.degrees(meter / (ERDRADIUS_M * abs(kos)))
    return (lat - d_lat, lat + d_lat, lon - d_lon, lon + d_lon)
