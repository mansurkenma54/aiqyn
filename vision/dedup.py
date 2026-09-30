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
from typing import Optional

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

    def check(self, class_key: str, lat: float, lon: float,
              trusted: bool = True, ts: Optional[float] = None) -> bool:
        """True = бұл жаңа ақау (құжат құруға болады).

        trusted=False — координата ШЫНАЙЫ емес (GPS жоқ, fallback нүкте).
        Ондай жағдайда «сол жерден» деген шарттың мәні жоғалады: бүкіл
        сапардағы ақаудың бәрі БІР нүктеде тұрады, сондықтан бұл сүзгі
        бірінші ақаудан кейінгінің бәрін жұтып қояды.

        Өлшенді (test.MOV, GPS жоқ): 7 шын ақау -> 3 құжат қалатын.

        Координата жоқ болғанда бір ақау = бір оқиға деген шешімді
        УАҚЫТ БОЙЫНША РАСТАУ қабаты (detectors/confirm.py) шығарады —
        ол нысанды кадрдан кадрға бақиды әрі әр нысанды бір-ақ рет
        қайтарады. Сондықтан бұл жерде тек класс + уақыт терезесімен
        шектелеміз, орын бойынша сүзбейміз.
        """
        # Уақыт КАДРДЫҢ уақытымен өлшенеді (нақты сағатпен емес): сонда
        # видеофайлды --fast режимде жүргізгенде де нәтиже бірдей болады.
        now = time.time() if ts is None else ts
        if not trusted:
            with self._lock:
                for event in self._events:
                    if event.class_key != class_key:
                        continue
                    if now - event.ts <= self.cfg.dedup_untrusted_gap_sec:
                        self.suppressed_count += 1
                        log.debug(
                            "Қайталанған ақау еленбеді (координатасыз, %.1f сек): %s",
                            now - event.ts, class_key,
                        )
                        return False
            return True

        with self._lock:
            self._prune(now)
            for event in self._events:
                # Класс аралық: бір жөндеу учаскесіндегі «торлы жарық» пен
                # «шұңқыр» — бригада үшін БІР жұмыс. Модельдің оларды қалай
                # атағаны тапсырыс санын өзгертпеуі керек.
                if not self.cfg.dedup_cross_class and event.class_key != class_key:
                    continue

                # ЖАҢА ҒАНА тіркелген ақау — бұл «сол жерге қайта келу»
                # емес. Оның бөлек нысан екенін уақыт бойынша растау қабаты
                # (detectors/confirm.py) кадрдан кадрға бақылап шешті, ал ол
                # GPS-тен дәлірек: бір көшеде 25 метр ішінде бірнеше бөлек
                # шұңқыр болуы қалыпты жағдай, әрқайсысы жеке жөндеу нысаны.
                #
                # Орын бойынша сүзу тек СОҢЫНАН — сол көшеден қайта өткенде
                # немесе бір ақау қайта табылғанда — керек.
                if now - event.ts < self.cfg.dedup_min_gap_sec:
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

    def remember(self, class_key: str, lat: float, lon: float, event_id: str,
                 ts: Optional[float] = None) -> None:
        with self._lock:
            self._events.append(
                SeenEvent(class_key=class_key, lat=lat, lon=lon,
                          ts=time.time() if ts is None else ts, event_id=event_id)
            )

    @property
    def tracked(self) -> int:
        with self._lock:
            return len(self._events)
