"""GPS — координатаны кадрдың НАҚТЫ уақытына сәйкестендіру.

Неге бұл маңызды
----------------
MJPEG ағыны 0.5-2 секунд кідіріспен келеді. 60 км/сағ = 17 м/сек.
Егер детекция болған сәтте GPS-ті "дәл қазір" сұрасақ, координата
20-30 метрге жылжып кетеді — жөндеу бригадасына қате мекенжай барады.

Шешім: GPS-ті бөлек ағында (thread) үздіксіз буферге жазып отырамыз,
ал координата керек болғанда — кадрдың уақытына сәйкес екі фикстің
арасын интерполяциялаймыз.

Уақыт туралы: интерполяция үшін телефонның өз уақыт белгісін емес,
ноутбуктің time.time() мәнін қолданамыз. Себебі телефон мен ноутбуктің
сағаты бірнеше секундқа айырмашылық жасауы мүмкін, ал бізге керегі —
бір ғана сағаттағы салыстырмалы уақыт.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .config import Config
from .geo import lerp_coord

log = logging.getLogger(__name__)


@dataclass
class GpsFix:
    lat: float
    lon: float
    ts: float                      # ноутбуктің time.time() мәні
    accuracy_m: float = 0.0
    speed_mps: float = 0.0
    bearing_deg: float = 0.0
    source: str = "phone"          # phone | fallback | interpolated
    trusted: bool = True           # False => координата болжам, құжатта ескертіледі

    def as_dict(self) -> dict:
        return {
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "accuracy_m": round(self.accuracy_m, 1),
            "speed_mps": round(self.speed_mps, 2),
            "bearing_deg": round(self.bearing_deg, 1),
            "source": self.source,
            "trusted": self.trusted,
        }


def _extract_gps_json(payload: dict) -> Optional[tuple]:
    """IP Webcam /gps.json нүктесінен GPS оқу.

    Қосымшаның жаңа нұсқаларында GPS дәл осы жерде тұрады (sensors.json
    бос қайтады). Пішімі:
        {"gps":     {"latitude":42.25, "longitude":69.72,
                     "altitude":567.0, "accuracy":6.5},
         "network": {"latitude":..., "longitude":..., "accuracy":300.0}}

    «gps» — жерсеріктен (дәлдігі бірнеше метр), «network» — ұялы
    желіден (жүздеген метр). Сондықтан алдымен «gps» алынады, ол
    болмаса ғана «network».
    """
    for key in ("gps", "network"):
        node = payload.get(key)
        if not isinstance(node, dict):
            continue
        try:
            lat = float(node["latitude"])
            lon = float(node["longitude"])
        except (KeyError, TypeError, ValueError):
            continue

        if lat == 0.0 and lon == 0.0:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        try:
            accuracy = float(node.get("accuracy") or 0.0)
        except (TypeError, ValueError):
            accuracy = 0.0

        try:
            speed = float(node.get("speed") or 0.0)
        except (TypeError, ValueError):
            speed = 0.0

        try:
            bearing = float(node.get("bearing") or 0.0)
        except (TypeError, ValueError):
            bearing = 0.0

        return lat, lon, accuracy, speed, bearing

    return None


def _extract_gps(payload: dict) -> Optional[tuple]:
    """IP Webcam /sensors.json ішінен GPS мәнін шығару.

    Қосымшаның нұсқасына қарай формат сәл өзгереді, сондықтан
    бірнеше нұсқаны да қарап шығамыз:
        {"gps": {"data": [[ts_ms, [lat, lon, alt, bearing, speed, acc]]]}}
        {"gps": {"data": [ts_ms, [lat, lon]]}}
        {"location": {...}}
    """
    for key in ("gps", "location", "gps_location", "fused_location"):
        node = payload.get(key)
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not data:
            continue

        # [[ts, [lat, lon, ...]], ...] — соңғы жазбаны аламыз
        if isinstance(data[0], list) and len(data[0]) >= 2 and isinstance(data[0][1], list):
            values = data[-1][1]
        # [ts, [lat, lon, ...]]
        elif len(data) >= 2 and isinstance(data[1], list):
            values = data[1]
        elif isinstance(data[0], (int, float)) and len(data) >= 2:
            values = data
        else:
            continue

        try:
            lat = float(values[0])
            lon = float(values[1])
        except (TypeError, ValueError, IndexError):
            continue

        if lat == 0.0 and lon == 0.0:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        bearing = float(values[3]) if len(values) > 3 else 0.0
        speed = float(values[4]) if len(values) > 4 else 0.0
        acc = float(values[5]) if len(values) > 5 else 0.0
        return lat, lon, acc, speed, bearing

    return None


class GpsProvider:
    """Базалық интерфейс."""

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def fix_at(self, ts: float) -> Optional[GpsFix]:
        raise NotImplementedError

    @property
    def latest(self) -> Optional[GpsFix]:
        raise NotImplementedError


class StaticGps(GpsProvider):
    """Тұрақты координата — файлдан оқығанда немесе GPS жоқ кезде."""

    def __init__(self, lat: float, lon: float):
        self._fix = GpsFix(lat=lat, lon=lon, ts=time.time(), source="fallback", trusted=False)

    def fix_at(self, ts: float) -> GpsFix:
        return GpsFix(
            lat=self._fix.lat, lon=self._fix.lon, ts=ts,
            source="fallback", trusted=False,
        )

    @property
    def latest(self) -> GpsFix:
        return self._fix


class PhoneGps(GpsProvider):
    """IP Webcam қосымшасының /sensors.json нүктесінен GPS оқиды."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        # Екі нүктені де қолдаймыз: қосымшаның нұсқасына қарай GPS
        # /gps.json ішінде де, /sensors.json ішінде де болуы мүмкін.
        # Қайсысы жұмыс істейтінін бірінші сәтті оқудан кейін есте сақтаймыз.
        self.gps_url = cfg.phone_base + "/gps.json"
        self.sensors_url = cfg.sensors_url
        self.url = self.gps_url
        self._working_url: Optional[str] = None
        self.interval = 1.0 / max(0.2, cfg.gps_poll_hz)
        self._buffer: deque[GpsFix] = deque(maxlen=600)   # ~5 мин @ 2 Гц
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fail_count = 0
        self._session = requests.Session()

    def refresh_urls(self) -> None:
        """Телефонның мекенжайы ауысса — GPS нүктелерін де жаңарту.

        Видео көзі USB модем қайта қосылғанда телефонды автоматты
        іздеп табады да, cfg.phone_host өзгереді. GPS сол ескі
        мекенжайда қалып қоймауы керек.
        """
        gps_url = self.cfg.phone_base + "/gps.json"
        if gps_url == self.gps_url:
            return
        self.gps_url = gps_url
        self.sensors_url = self.cfg.sensors_url
        self.url = self.gps_url
        self._working_url = None
        log.info("GPS мекенжайы жаңарды: %s", self.gps_url)

    # ---------- өмірлік цикл ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gps", daemon=True)
        self._thread.start()
        log.info("GPS ағыны іске қосылды: %s (%.1f Гц)", self.url, self.cfg.gps_poll_hz)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                parsed = None
                sources = (
                    [(self._working_url, None)] if self._working_url
                    else [(self.gps_url, _extract_gps_json),
                          (self.sensors_url, _extract_gps)]
                )

                for url, parser in sources:
                    resp = self._session.get(url, timeout=2.0)
                    resp.raise_for_status()
                    payload = resp.json()
                    # Қай нүкте екені белгілі болса — екі талдағышты да қолданамыз
                    candidate = (
                        parser(payload) if parser
                        else (_extract_gps_json(payload) or _extract_gps(payload))
                    )
                    if candidate:
                        parsed = candidate
                        if self._working_url != url:
                            self._working_url = url
                            log.info("GPS дереккөзі: %s", url)
                        break

                if parsed:
                    lat, lon, acc, speed, bearing = parsed
                    fix = GpsFix(
                        lat=lat, lon=lon, ts=time.time(),
                        accuracy_m=acc, speed_mps=speed, bearing_deg=bearing,
                        source="phone", trusted=True,
                    )
                    with self._lock:
                        self._buffer.append(fix)
                    if self._fail_count:
                        log.info("GPS қалпына келді (%.5f, %.5f)", lat, lon)
                    self._fail_count = 0
                else:
                    self._note_failure("sensors.json ішінде GPS деректері жоқ")
            except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
                self._note_failure(str(exc))

            elapsed = time.time() - started
            self._stop.wait(max(0.05, self.interval - elapsed))

    def _note_failure(self, reason: str) -> None:
        self._fail_count += 1
        # Логты толтырып жібермеу үшін: 1-ші, 10-шы, 50-ші... қателерді ғана жазамыз
        if self._fail_count in (1, 10, 50) or self._fail_count % 100 == 0:
            log.warning("GPS оқылмады (%d-рет): %s", self._fail_count, reason)

    # ---------- сұрау ----------

    @property
    def latest(self) -> Optional[GpsFix]:
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def fix_at(self, ts: float) -> Optional[GpsFix]:
        """Берілген уақытқа сәйкес координатаны интерполяциялап қайтарады."""
        with self._lock:
            samples = list(self._buffer)

        if not samples:
            return None

        if len(samples) == 1:
            only = samples[0]
            gap = abs(ts - only.ts)
            return GpsFix(
                lat=only.lat, lon=only.lon, ts=ts,
                accuracy_m=only.accuracy_m, speed_mps=only.speed_mps,
                bearing_deg=only.bearing_deg, source="phone",
                trusted=gap <= self.cfg.gps_max_extrapolate_sec,
            )

        # Сұралған уақыт буферден бұрын / кейін болса — шетіндегісін аламыз
        if ts <= samples[0].ts:
            edge = samples[0]
        elif ts >= samples[-1].ts:
            edge = samples[-1]
        else:
            edge = None

        if edge is not None:
            gap = abs(ts - edge.ts)
            return GpsFix(
                lat=edge.lat, lon=edge.lon, ts=ts,
                accuracy_m=edge.accuracy_m, speed_mps=edge.speed_mps,
                bearing_deg=edge.bearing_deg, source="phone",
                trusted=gap <= self.cfg.gps_max_extrapolate_sec,
            )

        # Сұралған уақытты қоршайтын екі фикстің арасын интерполяциялаймыз
        for older, newer in zip(samples, samples[1:]):
            if older.ts <= ts <= newer.ts:
                span = newer.ts - older.ts
                t = 0.0 if span <= 0 else (ts - older.ts) / span
                lat, lon = lerp_coord(older.lat, older.lon, newer.lat, newer.lon, t)
                return GpsFix(
                    lat=lat, lon=lon, ts=ts,
                    accuracy_m=max(older.accuracy_m, newer.accuracy_m),
                    speed_mps=(older.speed_mps + newer.speed_mps) / 2.0,
                    bearing_deg=newer.bearing_deg,
                    source="interpolated",
                    trusted=span <= self.cfg.gps_max_extrapolate_sec,
                )

        return samples[-1]

    # ---------- диагностика ----------

    def stats(self) -> dict:
        with self._lock:
            n = len(self._buffer)
            first = self._buffer[0] if n else None
            last = self._buffer[-1] if n else None
        return {
            "samples": n,
            "fail_count": self._fail_count,
            "span_sec": round(last.ts - first.ts, 1) if n > 1 else 0.0,
            "last": last.as_dict() if last else None,
        }


class TrackGps(GpsProvider):
    """Жазылған GPS трегін видеомен қатар «ойнату».

    Не үшін: видеофайлдан тексергенде телефон жоқ, сондықтан координата
    да жоқ. Ал симулятор (немесе нақты сапарда жазылған трек) уақыт
    бойынша координата тізбегін береді. Соны видеоның өз уақытымен
    сәйкестендіріп, әр кадрға шынайы координата береміз.

    Формат (jsonl): {"t": 12.5, "lat": 42.31, "lon": 69.59}
    """

    def __init__(self, path: str | Path, fallback: tuple[float, float]):
        self.path = Path(path)
        self.fallback = fallback
        self.points: list[tuple[float, float, float]] = []   # (t, lat, lon)
        self._started_at: Optional[float] = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            log.warning("GPS трегі табылмады: %s", self.path)
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                self.points.append(
                    (float(item["t"]), float(item["lat"]), float(item["lon"]))
                )
            self.points.sort(key=lambda p: p[0])
            log.info(
                "GPS трегі жүктелді: %d нүкте, %.0f сек (%s)",
                len(self.points),
                self.points[-1][0] if self.points else 0,
                self.path.name,
            )
        except Exception as exc:
            log.warning("GPS трегі оқылмады (%s): %s", self.path, exc)
            self.points = []

    def start(self) -> None:
        self._started_at = time.time()

    def stop(self) -> None:
        return None

    def _at(self, elapsed: float) -> tuple[float, float]:
        if not self.points:
            return self.fallback
        if elapsed <= self.points[0][0]:
            return self.points[0][1], self.points[0][2]
        if elapsed >= self.points[-1][0]:
            return self.points[-1][1], self.points[-1][2]

        for (t0, lat0, lon0), (t1, lat1, lon1) in zip(self.points, self.points[1:]):
            if t0 <= elapsed <= t1:
                span = t1 - t0
                k = 0.0 if span <= 0 else (elapsed - t0) / span
                return lerp_coord(lat0, lon0, lat1, lon1, k)
        return self.points[-1][1], self.points[-1][2]

    def fix_at(self, ts: float) -> GpsFix:
        started = self._started_at or ts
        lat, lon = self._at(max(0.0, ts - started))
        return GpsFix(
            lat=lat, lon=lon, ts=ts,
            accuracy_m=5.0, source="track", trusted=bool(self.points),
        )

    @property
    def latest(self) -> GpsFix:
        return self.fix_at(time.time())


def create_gps(cfg: Config) -> GpsProvider:
    """Видео көзіне қарай сәйкес GPS провайдерін таңдау.

    Егер координата қолмен көрсетілсе (--lat/--lon), телефон GPS-і
    қолданылмайды. Бұл — далалық тестте телефон GPS-і жұмыс істемей
    қалған жағдайдағы сақтандыру.
    """
    # Жазылған трек — видеофайлмен бірге ойнатылады (симулятор немесе
    # нақты сапарда жазылған GPS журналы)
    if cfg.gps_track:
        log.info("GPS трегі қолданылады: %s", cfg.gps_track)
        return TrackGps(cfg.path(cfg.gps_track),
                        (cfg.fallback_lat, cfg.fallback_lon))

    if cfg.manual_lat is not None and cfg.manual_lon is not None:
        log.info("Координата қолмен берілді: %.5f, %.5f (телефон GPS-і қолданылмайды)",
                 cfg.manual_lat, cfg.manual_lon)
        return StaticGps(cfg.manual_lat, cfg.manual_lon)

    if cfg.source == "phone":
        return PhoneGps(cfg)
    return StaticGps(cfg.fallback_lat, cfg.fallback_lon)
