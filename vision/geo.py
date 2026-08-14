"""Геометрия/география көмекшілері."""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Екі координата арасындағы қашықтық (метр)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def lerp_coord(
    lat1: float, lon1: float, lat2: float, lon2: float, t: float
) -> tuple[float, float]:
    """Екі нүкте арасын сызықты интерполяциялау.

    Қалалық қашықтықта (ондаған метр) сызықты интерполяция жеткілікті дәл —
    үлкен шеңберлік есептеудің қажеті жоқ.
    """
    t = max(0.0, min(1.0, t))
    return (lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """1-нүктеден 2-нүктеге қарай азимут (0..360, солтүстіктен сағат тілімен)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def format_coord(lat: float, lon: float) -> str:
    """Мокаптағы форматқа сай: 42.3156° N  69.5869° E"""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}° {ns}  {abs(lon):.4f}° {ew}"


def google_maps_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"


def osm_link(lat: float, lon: float, zoom: int = 18) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map={zoom}/{lat:.6f}/{lon:.6f}"


def two_gis_link(lat: float, lon: float, zoom: int = 18) -> str:
    """2ГИС — Шымкентте ең жиі қолданылатын карта."""
    return f"https://2gis.kz/shymkent/geo/{lon:.6f},{lat:.6f}?m={lon:.6f},{lat:.6f}/{zoom}"
