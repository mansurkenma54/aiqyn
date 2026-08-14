"""Жергілікті видеофайл — тест, дебаг және ЖЮРИ АЛДЫНДАҒЫ ДЕМО үшін.

Бұл режим — сақтандыру полисі: көрсетілім кезінде телефон қосылмай
қалса, алдын ала жазылған далалық видеомен дәл сол тізбек жұмыс істейді.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import cv2

from ..config import Config
from .base import Frame, VideoSource

log = logging.getLogger(__name__)


class VideoFileSource(VideoSource):
    name = "file"
    is_live = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.path = Path(cfg.video_path)
        self._cap: Optional[cv2.VideoCapture] = None
        self._fps = 25.0
        self._counter = 0
        self._start_wall = 0.0

    def open(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Видеофайл табылмады: {self.path}")

        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            raise ConnectionError(f"Видеофайл ашылмады: {self.path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps and 1.0 < fps < 121.0:
            self._fps = float(fps)

        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._cap = cap
        self._start_wall = time.time()
        log.info(
            "Видеофайл ашылды: %s (%.1f FPS, %d кадр, ~%.0f сек)",
            self.path.name, self._fps, total, total / self._fps if self._fps else 0,
        )

    def read(self) -> Optional[Frame]:
        if self._cap is None:
            return None

        ok, image = self._cap.read()
        if not ok or image is None:
            log.info("Видеофайл аяқталды (%d кадр оқылды).", self._counter)
            return None

        self._counter += 1

        # Нақты уақыт жылдамдығымен ойнату — тірі ағынға ұқсас мінез-құлық
        if self.cfg.file_realtime:
            target = self._start_wall + (self._counter / self._fps)
            delay = target - time.time()
            if delay > 0:
                time.sleep(min(delay, 0.5))

        # Уақыт белгісі ВИДЕОНЫҢ өз уақыт шкаласымен жүреді (нақты
        # сағатпен емес). Сонда --fast режимінде де GPS трегі кадрмен
        # дұрыс сәйкестенеді: 30-шы секундтағы кадрға трегтің 30-шы
        # секундтағы координатасы келеді.
        return Frame(
            image=image,
            ts=self._start_wall + self._counter / max(1.0, self._fps),
            index=self._counter,
        )

    def close(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    @property
    def fps(self) -> float:
        return self._fps
