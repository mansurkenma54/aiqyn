"""Жөндеу аймақтары мен жабық жолдар.

МӘСЕЛЕ: жол жөнделіп жатқан учаскеде ақау көп — бірақ ол туралы хабарлаудың
қажеті жоқ, жұмыс бұрыннан жүріп жатыр. Жүйе ондай жерден ондаған өтінім
жіберсе, оператор да, жауапты орган да оны бірден өшіріп тастайды.

ШЕШІМ: оператор сайттағы картадан жөндеу аймағын белгілейді. AIQYN Vision
сол тізімді жүктеп алады да, аймақ ішіндегі ақаулар бойынша ҚҰЖАТ ҚҰРМАЙДЫ
(бірақ санағын жүргізеді — статистика мен есеп үшін).

Аймақ түрлері:
    repair — жөндеу жүріп жатыр (уақытша, valid_until болуы мүмкін)
    closed — жабық жол
    ignore — қандай да бір себеппен есепке алынбайтын аймақ

Сайт өшік болса да жұмыс істейді: соңғы жүктелген тізім жергілікті
файлда сақталады.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from .config import Config
from .geo import haversine_m

log = logging.getLogger(__name__)

CACHE_NAME = "zones_cache.json"


@dataclass
class Zone:
    id: int
    name: str
    kind: str                      # repair | closed | ignore
    shape: str                     # circle | polygon
    lat: Optional[float] = None
    lon: Optional[float] = None
    radius_m: float = 100.0
    polygon: Optional[list] = None   # [[lat, lon], ...]
    corridor_m: float = 20.0          # polyline орталығынан екі жаққа дейінгі қашықтық
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    note: str = ""
    boundary_verified: bool = False

    # ---------- мерзімі ----------

    @property
    def is_expired(self) -> bool:
        """Жөндеу аймағының мерзімі өтті ме."""
        if not self.valid_until:
            return False
        try:
            text = str(self.valid_until).replace("Z", "+00:00")
            # YYYY-MM-DD мерзімі сол күннің соңына дейін жарамды.
            if "T" not in text and " " not in text:
                deadline = datetime.fromisoformat(text).date()
                return deadline < datetime.now().date()
            deadline = datetime.fromisoformat(text)
            if deadline.tzinfo is None:
                return deadline.timestamp() < time.time()
            return deadline.timestamp() < time.time()
        except (ValueError, TypeError):
            return False

    @property
    def is_not_started(self) -> bool:
        if not self.valid_from:
            return False
        try:
            text = str(self.valid_from).replace("Z", "+00:00")
            if "T" not in text and " " not in text:
                return datetime.fromisoformat(text).date() > datetime.now().date()
            starts = datetime.fromisoformat(text)
            return starts.timestamp() > time.time()
        except (ValueError, TypeError):
            return False

    # ---------- геометрия ----------

    def contains(self, lat: float, lon: float) -> bool:
        if self.is_expired or self.is_not_started:
            return False

        if self.shape == "circle":
            if self.lat is None or self.lon is None:
                return False
            return haversine_m(lat, lon, self.lat, self.lon) <= self.radius_m

        if self.shape == "polygon" and self.polygon and len(self.polygon) >= 3:
            return _point_in_polygon(lat, lon, self.polygon)

        if self.shape == "polyline" and self.polygon and len(self.polygon) >= 2:
            return _distance_to_polyline_m(lat, lon, self.polygon) <= self.corridor_m

        return False

    @classmethod
    def from_dict(cls, data: dict) -> "Zone":
        return cls(
            id=int(data.get("id", 0)),
            name=data.get("name", "Аймақ"),
            kind=data.get("kind", "repair"),
            shape=data.get("shape", "circle"),
            lat=data.get("lat"),
            lon=data.get("lon"),
            radius_m=float(data.get("radius_m") or 100.0),
            polygon=data.get("points") or data.get("polygon"),
            corridor_m=float(
                data.get("corridor_m") or data.get("radius_m") or 20.0
            ),
            valid_from=data.get("valid_from"),
            valid_until=data.get("valid_until"),
            note=data.get("note", "") or "",
            boundary_verified=bool(data.get("boundary_verified", False)),
        )


def _point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """Сәуле әдісі (ray casting) — нүкте көпбұрыштың ішінде ме.

    Қалалық масштабта (бірнеше шақырым) градустарды жазық координата
    ретінде қарастыруға болады — қателік елеусіз.
    """
    inside = False
    count = len(polygon)

    j = count - 1
    for i in range(count):
        try:
            lat_i, lon_i = float(polygon[i][0]), float(polygon[i][1])
            lat_j, lon_j = float(polygon[j][0]), float(polygon[j][1])
        except (TypeError, ValueError, IndexError):
            return False

        if (lon_i > lon) != (lon_j > lon):
            denominator = lon_j - lon_i
            if abs(denominator) > 1e-12:
                crossing = (lat_j - lat_i) * (lon - lon_i) / denominator + lat_i
                if lat < crossing:
                    inside = not inside
        j = i

    return inside


def _distance_to_segment_m(
    lat: float,
    lon: float,
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
) -> float:
    """Жергілікті equirectangular жазықтығында нүктеден сегментке дейін."""
    earth_radius_m = 6_371_000.0
    ref_lat = math.radians((lat + lat_a + lat_b) / 3.0)

    px = math.radians(lon) * math.cos(ref_lat) * earth_radius_m
    py = math.radians(lat) * earth_radius_m
    ax = math.radians(lon_a) * math.cos(ref_lat) * earth_radius_m
    ay = math.radians(lat_a) * earth_radius_m
    bx = math.radians(lon_b) * math.cos(ref_lat) * earth_radius_m
    by = math.radians(lat_b) * earth_radius_m

    ab_x, ab_y = bx - ax, by - ay
    ap_x, ap_y = px - ax, py - ay
    length_sq = ab_x * ab_x + ab_y * ab_y
    if length_sq <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, (ap_x * ab_x + ap_y * ab_y) / length_sq))
    closest_x = ax + t * ab_x
    closest_y = ay + t * ab_y
    return math.hypot(px - closest_x, py - closest_y)


def _distance_to_polyline_m(lat: float, lon: float, points: list) -> float:
    distances: list[float] = []
    for first, second in zip(points, points[1:]):
        try:
            lat_a, lon_a = float(first[0]), float(first[1])
            lat_b, lon_b = float(second[0]), float(second[1])
        except (TypeError, ValueError, IndexError):
            continue
        distances.append(_distance_to_segment_m(lat, lon, lat_a, lon_a, lat_b, lon_b))
    return min(distances) if distances else float("inf")


class ZoneRegistry:
    """Аймақтарды сайттан жүктеп, жадта ұстайды."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.endpoint = cfg.portal_url.rstrip("/") + "/api/zones"
        self.cache_file = cfg.path(CACHE_NAME)
        self._zones: list[Zone] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._session = requests.Session()

        self.skipped_count = 0
        self._load_cache()

    # ---------- жүктеу ----------

    def _load_cache(self) -> None:
        if not self.cache_file.exists():
            return
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            zones = [Zone.from_dict(item) for item in data.get("zones", [])]
            with self._lock:
                self._zones = zones
            if zones:
                log.info("Аймақтар жергілікті файлдан жүктелді: %d", len(zones))
        except Exception as exc:
            log.warning("Аймақтар кэші оқылмады: %s", exc)

    def _save_cache(self, zones: list[dict]) -> None:
        try:
            self.cache_file.write_text(
                json.dumps({"zones": zones}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            log.debug("Аймақтар кэші жазылмады: %s", exc)

    def refresh(self) -> bool:
        """Сайттан аймақтарды жүктеу."""
        try:
            response = self._session.get(self.endpoint, timeout=6.0)
            response.raise_for_status()
            payload = response.json()
            raw = payload.get("zones", [])
            zones = [Zone.from_dict(item) for item in raw]

            with self._lock:
                previous = len(self._zones)
                self._zones = zones

            self._save_cache(raw)
            if len(zones) != previous:
                log.info("Аймақтар жаңарды: %d белсенді", len(zones))
            return True

        except requests.RequestException as exc:
            log.debug("Аймақтар жүктелмеді (%s): %s", self.endpoint, exc)
            return False

    # ---------- фондық жаңарту ----------

    def start(self, interval: float = 60.0) -> None:
        self.refresh()

        def loop():
            while not self._stop.wait(interval):
                self.refresh()

        self._stop.clear()
        self._thread = threading.Thread(target=loop, name="zones", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    # ---------- тексеру ----------

    def find(self, lat: float, lon: float) -> Optional[Zone]:
        """Нүкте қай аймаққа кіретінін қайтарады (кірмесе — None)."""
        with self._lock:
            zones = list(self._zones)

        for zone in zones:
            if zone.contains(lat, lon):
                return zone
        return None

    def should_skip(self, lat: float, lon: float) -> Optional[Zone]:
        """Осы координатадағы ақауды есепке алмау керек пе."""
        if not self.cfg.skip_in_zones:
            return None
        zone = self.find(lat, lon)
        # Жуық ескі шеңберлер мен әлі тексерілмеген сызықтар ақауды
        # үнсіз жасыра алмайды. Тоқтату тек оператор шекараны растағанда
        # іске қосылады.
        if zone is not None and not zone.boundary_verified:
            return None
        if zone is not None:
            self.skipped_count += 1
        return zone

    @property
    def count(self) -> int:
        with self._lock:
            return sum(
                1 for zone in self._zones
                if not zone.is_expired and not zone.is_not_started
            )
