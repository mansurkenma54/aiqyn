"""Оқиғаны нақты жол сегментіне байлау — кеңістіктік байланыс.

ТЗ талабы: «SDR және ЦС ГГ НИПД — ақаулардың, жөндеу учаскелерінің және
жол оқиғаларының кеңістіктік байланысы үшін».

Мәселе: жалаң координата (42.3156, 69.5869) — жүйе үшін мағынасыз нүкте.
Оны бір нәрсеге БАЙЛАУ керек: қай көше, қай сегмент, қай аудан. Сонда ғана
«Қаражал көшесінде 4 ақау» деп есептеуге, қайталанғанын танып, жөндеу
учаскесімен салыстыруға болады.

Шешім: нүктені OpenStreetMap-тегі ең жақын жол сызығына проекциялаймыз.
OSM way id — тұрақты, ашық және тексерілетін идентификатор. Ол ЦС ГГ НИПД
нысанының орнын баспайды, бірақ ресми тізілім ашылғанда сол өріске
ауыстыруға дайын құрылым береді.

Не шынайы:
    * геометрия OpenStreetMap-тен (Overpass API), ойдан құрылмайды;
    * нәтиже дискіде кэштеледі — интернетсіз де жұмыс істейді;
    * жол табылмаса, өріс бос қалады (жалған байланыс жасалмайды).

Не әлі жоқ:
    * ЦС ГГ НИПД-тің өз нысан идентификаторы (ресми қолжетімділік қажет);
    * SDR-ге тікелей жазу (адаптер дайын, эндпойнт жоқ).
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("portal.spatial")

ROOT = Path(__file__).resolve().parent
# Кэш екі қабатты:
#   SEED_PATH  — репозиториймен бірге жүретін дайын байланыстар. Serverless
#                ортада (Vercel) әр суық старт таза басталады, сондықтан
#                бұл файл болмаса экспортта кеңістіктік байланыс жоғалады.
#   CACHE_PATH — жазылатын қабат: жаңа байланыстар осында қосылады.
SEED_PATH = ROOT / "spatial_cache.json"
CACHE_PATH = Path(
    os.getenv("AIQYN_SPATIAL_CACHE")
    or (Path(os.getenv("AIQYN_DB_PATH")).parent / "spatial_cache.json"
        if os.getenv("AIQYN_DB_PATH") else SEED_PATH)
)

OVERPASS = os.getenv(
    "AIQYN_OVERPASS_URL", "https://overpass-api.de/api/interpreter"
).strip()
SEARCH_RADIUS_M = float(os.getenv("AIQYN_SPATIAL_RADIUS", "60"))
TIMEOUT_SEC = float(os.getenv("AIQYN_SPATIAL_TIMEOUT", "12"))

EARTH_R = 6_371_000.0

# Кэш кілті — координатаның дөңгелектенген мәні (~11 м тор).
# Бір көшедегі көрші оқиғалар бір сұраумен шешіледі.
_GRID = 4

_lock = threading.Lock()
_cache: dict[str, dict] = {}
_cache_loaded = False

# Жол түрлерінің қазақша атауы — есепте адам оқитын күйде керек
ROAD_CLASS_KK = {
    "motorway": "автомагистраль",
    "trunk": "магистральдық жол",
    "primary": "негізгі даңғыл",
    "secondary": "аудандық көше",
    "tertiary": "жергілікті көше",
    "residential": "тұрғын аймақ көшесі",
    "unclassified": "жіктелмеген жол",
    "service": "қызметтік жол",
    "living_street": "тұрғын аймақ",
    "track": "топырақ жол",
}


def _load_cache() -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    # Алдымен репозиторийдегі дайын байланыстар, сосын жазылатын қабат
    for path in dict.fromkeys([SEED_PATH, CACHE_PATH]):
        try:
            if path.exists():
                _cache.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Кеңістіктік кэш оқылмады (%s): %s", path, exc)
    if _cache:
        log.info("Кеңістіктік кэш: %d байланыс", len(_cache))


def _save_cache() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(_cache, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("Кеңістіктік кэш жазылмады: %s", exc)


def _key(lat: float, lon: float) -> str:
    return f"{round(lat, _GRID)},{round(lon, _GRID)}"


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def _point_to_segment_m(lat, lon, alat, alon, blat, blon) -> tuple[float, float]:
    """Нүктеден кесіндіге дейінгі қашықтық (м) және кесінді бойындағы орны 0..1."""
    # Қала масштабында жалпақ жуықтау жеткілікті
    k = math.cos(math.radians(lat))
    px, py = lon * k, lat
    ax, ay = alon * k, alat
    bx, by = blon * k, blat
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return _haversine(lat, lon, alat, alon), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + dx * t, ay + dy * t
    return _haversine(lat, lon, cy, cx / k if k else lon), t


def _query_overpass(lat: float, lon: float, radius: float) -> Optional[dict]:
    import requests

    query = (
        f"[out:json][timeout:{int(TIMEOUT_SEC)}];"
        f'way(around:{int(radius)},{lat},{lon})["highway"]'
        f'["highway"!~"footway|path|cycleway|steps|pedestrian|corridor"];'
        f"out geom tags;"
    )
    response = requests.post(
        OVERPASS, data={"data": query}, timeout=TIMEOUT_SEC,
        headers={"User-Agent": "AIQYN-ZHOL/1.0 (Smart City Shymkent)"},
    )
    response.raise_for_status()
    return response.json()


def _pick_nearest(lat: float, lon: float, data: dict) -> Optional[dict]:
    best = None
    for element in data.get("elements", []):
        geometry = element.get("geometry") or []
        if len(geometry) < 2:
            continue
        for i in range(len(geometry) - 1):
            a, b = geometry[i], geometry[i + 1]
            distance, t = _point_to_segment_m(
                lat, lon, a["lat"], a["lon"], b["lat"], b["lon"]
            )
            if best is None or distance < best["distance_m"]:
                tags = element.get("tags") or {}
                best = {
                    "osm_way_id": element.get("id"),
                    "distance_m": round(distance, 1),
                    "segment_index": i,
                    "segment_pos": round(t, 3),
                    "road_name": tags.get("name") or tags.get("name:kk")
                                 or tags.get("name:ru"),
                    "road_class": tags.get("highway"),
                    "road_class_kk": ROAD_CLASS_KK.get(tags.get("highway", ""),
                                                       tags.get("highway")),
                    "lanes": tags.get("lanes"),
                    "surface": tags.get("surface"),
                    "maxspeed": tags.get("maxspeed"),
                    "segment_start": {"lat": a["lat"], "lon": a["lon"]},
                    "segment_end": {"lat": b["lat"], "lon": b["lon"]},
                }
    return best


def bind(lat: float, lon: float, *, allow_network: bool = True) -> dict:
    """Координатаны жол сегментіне байлау.

    Қайтарады: `bound` (сәтті ме), `osm_way_id`, `road_name`, `road_class`,
    `distance_m`, `segment_*`, `source`.

    Желі жоқ болса немесе жол табылмаса — `bound: False`. Жалған байланыс
    ешқашан қайтарылмайды.
    """
    if lat is None or lon is None:
        return {"bound": False, "reason": "координата жоқ"}

    with _lock:
        _load_cache()
        cached = _cache.get(_key(lat, lon))
    if cached is not None:
        return {**cached, "source": "cache"}

    if not allow_network:
        return {"bound": False, "reason": "кэште жоқ, желі өшірулі"}

    try:
        data = _query_overpass(lat, lon, SEARCH_RADIUS_M)
    except Exception as exc:                      # noqa: BLE001 — желі кез келген себеппен құлауы мүмкін
        log.info("Overpass жауап бермеді (%s) — байланыс кейінге қалдырылды", exc)
        return {"bound": False, "reason": "желі қолжетімсіз"}

    best = _pick_nearest(lat, lon, data or {})
    if best is None or best["distance_m"] > SEARCH_RADIUS_M:
        result = {"bound": False, "reason": "жақын жол табылмады"}
    else:
        result = {
            "bound": True,
            "spatial_id": f"OSM:way/{best['osm_way_id']}",
            "bound_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            **best,
        }

    with _lock:
        _cache[_key(lat, lon)] = result
        _save_cache()
    return {**result, "source": "overpass"}


def summary_kk(binding: dict) -> str:
    """Оператор көретін бір жолдық сипаттама."""
    if not binding or not binding.get("bound"):
        return "Кеңістіктік байланыс жоқ"
    name = binding.get("road_name") or "атауы жоқ жол"
    klass = binding.get("road_class_kk") or ""
    distance = binding.get("distance_m")
    tail = f" · осьтен {distance} м" if distance is not None else ""
    return f"{name}{' · ' + klass if klass else ''}{tail}"
