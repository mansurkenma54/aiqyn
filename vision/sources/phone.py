"""Телефон камерасы — IP Webcam (Android) арқылы.

Қосылу тізбегі:
    1. Телефонда IP Webcam қосымшасын ашып, "Start server" басу
    2. Телефон: Settings → Hotspot & tethering → USB tethering (ҚОСУ)
    3. Ноутбукта жаңа желі интерфейсі пайда болады, телефон әдетте
       192.168.42.129 мекенжайын алады
    4. Браузерден http://192.168.42.129:8080 ашылатынын тексеру

Қолданылатын нүктелер (endpoints):
    /video        — MJPEG ағыны (негізгі кіріс)
    /shot.jpg     — жоғары ажыратымдылықты жеке кадр (дәлел фотосы)
    /sensors.json — GPS және басқа сенсорлар (gps.py оқиды)
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np
import requests

from ..config import Config
from .base import ThreadedCaptureSource

log = logging.getLogger(__name__)


class PhoneIPWebcamSource(ThreadedCaptureSource):
    name = "phone"
    is_live = True

    def __init__(self, cfg: Config):
        super().__init__(url=cfg.video_url, name="phone", reconnect=True)
        self.cfg = cfg
        self._session = requests.Session()
        self._shot_failures = 0

    def open(self) -> None:
        self._probe()
        # Мекенжай ауысқан болса, ағын да, GPS те жаңасына бағытталуы керек
        self.url = self.cfg.video_url
        super().open()

    def _probe(self) -> None:
        """Ағынды ашпай тұрып телефонға қол жеткізуді тексеру.

        USB модемді қайта қосқан сайын телефонға ЖАҢА IP беріледі.
        Сондықтан баптаудағы мекенжай жауап бермесе, жүйе желіні өзі
        қарап шығып, телефонды табады — қолмен түзетудің қажеті жоқ.
        """
        try:
            resp = self._session.get(self.cfg.phone_base, timeout=3.0)
            resp.raise_for_status()
            return
        except requests.RequestException:
            pass

        log.warning("%s жауап бермеді — телефон желіден ізделуде...", self.cfg.phone_base)
        try:
            from ..main import scan_for_phone
            found = scan_for_phone()
        except Exception:
            found = []

        if found:
            host, _, port = found[0].partition(":")
            self.cfg.phone_host = host
            if port.isdigit():
                self.cfg.phone_port = int(port)
            log.info("Телефон табылды: %s", self.cfg.phone_base)
            return

        try:
            resp = self._session.get(self.cfg.phone_base, timeout=3.0)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ConnectionError(
                f"Телефонға қосылу мүмкін болмады: {self.cfg.phone_base}\n"
                f"  Себебі: {exc}\n"
                f"  Тексеріңіз:\n"
                f"    1) IP Webcam қосымшасында 'Start server' басылған ба?\n"
                f"    2) Телефонда USB tethering қосулы ма?\n"
                f"    3) Қосымша көрсеткен мекенжай {self.cfg.phone_host} пе?\n"
                f"       Басқа болса: --phone-host <мекенжай>\n"
                f"  Толық тексеру үшін: python -m vision.main doctor"
            ) from exc

    def high_res_shot(self) -> Optional[np.ndarray]:
        """/shot.jpg арқылы анық кадр алу (дәлел фотосы үшін)."""
        if not self.cfg.use_shot_jpg or self._shot_failures > 5:
            return None
        try:
            resp = self._session.get(self.cfg.shot_url, timeout=3.0)
            resp.raise_for_status()
            buf = np.frombuffer(resp.content, dtype=np.uint8)
            image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("JPEG декодталмады")
            self._shot_failures = 0
            return image
        except Exception as exc:
            self._shot_failures += 1
            if self._shot_failures <= 2:
                log.warning("/shot.jpg алынбады: %s (ағындағы кадр қолданылады)", exc)
            return None
