"""Координатаны ЕҢ ЖАҚЫН ЖОЛҒА түсіру (snap to road).

МӘСЕЛЕ: телефонның GPS-і 5-15 метрге қателеседі. Соның салдарынан
картадағы белгі жолдың үстінде емес, шөпте, ауладa немесе ғимаратта
тұрып қалады. Ресми құжатта да, картада да бұл ұсқынсыз әрі жөндеу
бригадасын шатастырады.

ШЕШІМ: координатаны айналасындағы көшенің осіне ПРОЕКЦИЯЛАЙМЫЗ.
Жол геометриясы 2ГИС-тен алынады (көше сызықтары), нәтиже кэштеледі.

Адалдық ережесі: түпнұсқа координата да құжатта сақталады
(`gps_raw_lat`/`gps_raw_lon`) және жылжу қашықтығы жазылады. Яғни
«түзетілген» дерек шынайы өлшемді жасырмайды.

Егер жол табылмаса немесе жылжу тым үлкен болса (әдепкі 25 м-ден
артық), координата ӨЗГЕРМЕЙДІ — қате «түзету» жоқтан жаман.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import dataclass
from typing import Optional

import requests

from .config import Config

log = logging.getLogger(__name__)

EARTH_R = 6_371_000.0


@dataclass
class SnapResult:
    lat: float
    lon: float
    snapped: bool = False
    distance_m: float = 0.0
    street: str = ""

    def as_dict(self) -> dict:
        return {
            "snapped": self.snapped,
            "distance_m": round(self.distance_m, 1),
            "street": self.street,
        }


def _parse_wkt_lines(wkt: str) -> list[list[tuple[float, float]]]:
    """WKT (lon lat) -> [[(lat, lon), ...], ...]"""
    if not wkt:
        return []
    lines: list[list[tuple[float, float]]] = []
    for chunk in re.findall(r"\(([-0-9.,\s]+)\)", wkt):
        points: list[tuple[float, float]] = []
        for pair in chunk.split(","):
            parts = pair.split()
            if len(parts) < 2:
                continue
            try:
                lon, lat = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                points.append((lat, lon))
        if len(points) >= 2:
            lines.append(points)
    return lines


def _project_on_segment(
    lat: float, lon: float,
    lat_a: float, lon_a: float,
    lat_b: float, lon_b: float,
) -> tuple[float, float, float]:
    """Нүктені кесіндіге проекциялау.

    Жергілікті жазық координатаға (метр) ауыстырып есептейміз —
    қалалық қашықтықта бұл жеткілікті дәл.
    Қайтарады: (проекция_lat, проекция_lon, қашықтық_м)
    """
    ref = math.radians((lat + lat_a + lat_b) / 3.0)
    cos_ref = math.cos(ref)

    def to_xy(la: float, lo: float) -> tuple[float, float]:
        return (math.radians(lo) * cos_ref * EARTH_R, math.radians(la) * EARTH_R)

    def to_ll(x: float, y: float) -> tuple[float, float]:
        return (math.degrees(y / EARTH_R), math.degrees(x / (cos_ref * EARTH_R)))

    px, py = to_xy(lat, lon)
    ax, ay = to_xy(lat_a, lon_a)
    bx, by = to_xy(lat_b, lon_b)

    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-9:
        return (lat_a, lon_a, math.hypot(px - ax, py - ay))

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    cx, cy = ax + t * dx, ay + t * dy
    snapped_lat, snapped_lon = to_ll(cx, cy)
    return (snapped_lat, snapped_lon, math.hypot(px - cx, py - cy))


class RoadSnapper:
    """Координатаны жол осіне түсіреді (2ГИС геометриясы бойынша)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._session = requests.Session()
        self._cache: dict[tuple, list] = {}   # тор ұяшығы -> көше сызықтары
        self._lock = threading.Lock()
        self._failures = 0

        self.snapped_count = 0
        self.checked_count = 0

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.snap_to_road and self.cfg.gis2_key and self._failures < 5)

    @staticmethod
    def _cell(lat: float, lon: float) -> tuple:
        # ~250 метрлік тор: жақын нүктелер бір сұрауды бөліседі
        return (round(lat, 3), round(lon, 3))

    def _load_roads(self, lat: float, lon: float) -> list:
        cell = self._cell(lat, lon)
        with self._lock:
            if cell in self._cache:
                return self._cache[cell]

        roads: list = []
        try:
            response = self._session.get(
                "https://catalog.api.2gis.com/3.0/items/geocode",
                params={
                    "lat": f"{lat:.6f}",
                    "lon": f"{lon:.6f}",
                    "radius": str(int(self.cfg.snap_search_radius_m)),
                    "type": "street",
                    "fields": "items.geometry.selection",
                    "key": self.cfg.gis2_key,
                },
                timeout=self.cfg.geocode_timeout,
            )
            response.raise_for_status()
            items = (response.json().get("result") or {}).get("items") or []
            for item in items:
                wkt = ((item.get("geometry") or {}).get("selection")) or ""
                for line in _parse_wkt_lines(wkt):
                    roads.append((item.get("name") or item.get("full_name") or "", line))
            self._failures = 0
        except requests.RequestException as exc:
            self._failures += 1
            if self._failures <= 2:
                log.warning("Жол геометриясы алынбады: %s", exc)

        with self._lock:
            self._cache[cell] = roads
        return roads

    def snap(self, lat: float, lon: float) -> SnapResult:
        """Координатаны ең жақын жолға түсіру."""
        if not self.enabled:
            return SnapResult(lat=lat, lon=lon)

        self.checked_count += 1
        roads = self._load_roads(lat, lon)
        if not roads:
            return SnapResult(lat=lat, lon=lon)

        best: Optional[tuple[float, float, float, str]] = None

        for street, line in roads:
            for (lat_a, lon_a), (lat_b, lon_b) in zip(line, line[1:]):
                snapped_lat, snapped_lon, distance = _project_on_segment(
                    lat, lon, lat_a, lon_a, lat_b, lon_b
                )
                if best is None or distance < best[2]:
                    best = (snapped_lat, snapped_lon, distance, street)

        if best is None:
            return SnapResult(lat=lat, lon=lon)

        snapped_lat, snapped_lon, distance, street = best

        # Тым алыс болса — түзетпейміз. Мүмкін ақау шынымен жолдан
        # тыс (тротуар, аула) немесе GPS қатты адасқан.
        if distance > self.cfg.snap_max_distance_m:
            log.debug("Жолға түсірілмеді: %.0f м тым алыс (%s)", distance, street)
            return SnapResult(lat=lat, lon=lon, distance_m=distance, street=street)

        self.snapped_count += 1
        log.debug("Жолға түсірілді: %.1f м жылжыды (%s)", distance, street)
        return SnapResult(
            lat=snapped_lat, lon=snapped_lon,
            snapped=True, distance_m=distance, street=street,
        )
