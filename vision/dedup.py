"""Дедупликация — бір ақауды бірнеше рет жібермеу.

Мәселе: көлік шұңқырға жақындағанда ол ондаған кадрда көрінеді.
Дедупликациясыз бір шұңқырдан 30 бөлек өтінім кетер еді — оператор
жүйені бірден жауып тастайды.

Ереже: СОЛ санаттағы ақау, СОЛ жерден (әдепкі 15 м) және СОЛ уақыт
терезесінде (әдепкі 2 мин) — жаңа құжат құрылмайды.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .config import Config
from .geo import haversine_m

log = logging.getLogger(__name__)


@dataclass
class SeenEvent:
    class_key: str
    lat: float
    lon: float
    ts: float
    event_id: str


class Deduplicator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._events: list[SeenEvent] = []
        self._lock = threading.Lock()
        self.suppressed_count = 0

    def _prune(self, now: float) -> None:
        # dedup_window_sec = 0 => СЕССИЯ БОЙЫ ЕСТЕ САҚТАЙМЫЗ.
        #
        # Неге әдепкі осы: бір шұңқыр бір сапарда БІР РЕТ қана тіркелуі
        # керек. Уақыт терезесі болса, сол көшеден кері қайтқанда немесе
        # ақау бір сәтке көрінбей қалып, қайта көрінгенде екінші құжат
        # құрылып кетеді — оператор бір ақауды екі рет қарайды.
        if self.cfg.dedup_window_sec <= 0:
            return
        horizon = now - self.cfg.dedup_window_sec
        self._events = [e for e in self._events if e.ts >= horizon]

    def check(self, class_key: str, lat: float, lon: float) -> bool:
        """True = бұл жаңа ақау (құжат құруға болады)."""
        now = time.time()
        with self._lock:
            self._prune(now)
            for event in self._events:
                if event.class_key != class_key:
                    continue
                distance = haversine_m(lat, lon, event.lat, event.lon)
                if distance <= self.cfg.dedup_radius_m:
                    self.suppressed_count += 1
                    log.debug(
                        "Қайталанған ақау еленбеді: %s (%.0f м, %.0f сек бұрын)",
                        class_key, distance, now - event.ts,
                    )
                    return False
        return True

    def remember(self, class_key: str, lat: float, lon: float, event_id: str) -> None:
        with self._lock:
            self._events.append(
                SeenEvent(class_key=class_key, lat=lat, lon=lon, ts=time.time(), event_id=event_id)
            )

    @property
    def tracked(self) -> int:
        with self._lock:
            return len(self._events)
