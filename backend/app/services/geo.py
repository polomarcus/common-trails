"""Shared geometry helpers."""
import math


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Haversine distance in metres between two WGS-84 points (atan2 form)."""
    R = 6_371_000
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
