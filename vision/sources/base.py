"""Видео көзінің абстракциясы.

Жобаның ең маңызды архитектуралық шешімі осы жерде: бүкіл AI-процесс
(детекция → дәлел → құжат) видео ҚАЙДАН келгенін білмейді. Бүгін —
телефон, ертең — Sergek/CCTV немесе дрон: тек осы файлдағы кластың
жаңа іске асырылуы қосылады, қалған код өзгермейді.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Frame:
    image: np.ndarray
    ts: float          # кадр АЛЫНҒАН сәт (ноутбуктің time.time())
    index: int

    @property
    def height(self) -> int:
        return self.image.shape[0]

    @property
    def width(self) -> int:
        return self.image.shape[1]


class VideoSource(ABC):
    """Кез келген видео көзінің ортақ интерфейсі."""

    name: str = "source"
    is_live: bool = True

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def read(self) -> Optional[Frame]:
        """Келесі кадр немесе None (ағын аяқталды/үзілді)."""

    @abstractmethod
    def close(self) -> None: ...

    @property
    def fps(self) -> float:
        return 25.0

    def high_res_shot(self) -> Optional[np.ndarray]:
        """Жоғары ажыратымдылықты жеке кадр (бар болса).

        Дәлел фотосы үшін қолданылады — ағындағы кадр әдетте қысылған,
        ал мұндай кадр анағұрлым анық болады.
        """
        return None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()


class ThreadedCaptureSource(VideoSource):
    """cv2.VideoCapture-ды бөлек ағында оқитын база.

    Неге бөлек ағын керек: MJPEG/RTSP ағыны буферге жиналады. Егер
    негізгі цикл AI-ға 200 мс жұмсаса, буферде ескі кадрлар үйіліп,
    "тірі" көрініс 5-10 секундқа артта қалады. Сондықтан бөлек ағын
    үздіксіз оқып, тек ЕҢ СОҢҒЫ кадрды ұстап тұрады, ескісін тастайды.
    """

    def __init__(self, url: str, name: str = "stream", reconnect: bool = True):
        self.url = url
        self.name = name
        self.reconnect = reconnect
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self._latest: Optional[Frame] = None
        self._last_served = -1
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._counter = 0
        self._fps = 25.0
        self._opened_at = 0.0

    # ---------- өмірлік цикл ----------

    def _create_capture(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        # Ішкі буферді минимумға түсіру (барлық backend қолдамайды, бірақ зияны жоқ)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def open(self) -> None:
        cap = self._create_capture()
        if not cap.isOpened():
            raise ConnectionError(
                f"Видео көзі ашылмады: {self.url}\n"
                f"Тексеріңіз: телефондағы IP Webcam қосулы ма, USB tethering қосулы ма."
            )
        self._cap = cap
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps and 1.0 < fps < 121.0:
            self._fps = float(fps)
        self._opened_at = time.time()

        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"cap-{self.name}", daemon=True)
        self._thread.start()
        log.info("Видео көзі ашылды: %s (%.1f FPS)", self.url, self._fps)

    def _run(self) -> None:
        misses = 0
        while not self._stop.is_set():
            cap = self._cap
            if cap is None:
                break

            ok, image = cap.read()
            now = time.time()

            if not ok or image is None:
                misses += 1
                if misses in (1, 25) or misses % 100 == 0:
                    log.warning("Кадр оқылмады (%d-рет): %s", misses, self.name)
                if misses > 25 and self.reconnect:
                    self._try_reconnect()
                    misses = 0
                else:
                    self._stop.wait(0.05)
                continue

            misses = 0
            self._counter += 1
            frame = Frame(image=image, ts=now, index=self._counter)
            with self._lock:
                self._latest = frame

    def _try_reconnect(self) -> None:
        log.warning("Ағын үзілді — қайта қосылу: %s", self.url)
        try:
            if self._cap:
                self._cap.release()
        except Exception:
            pass
        self._stop.wait(1.0)
        try:
            cap = self._create_capture()
            if cap.isOpened():
                self._cap = cap
                log.info("Ағын қалпына келді.")
            else:
                cap.release()
        except Exception as exc:
            log.warning("Қайта қосылу сәтсіз: %s", exc)

    def read(self) -> Optional[Frame]:
        """Ең соңғы ЖАҢА кадрды қайтарады (ескісін қайталамайды)."""
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self._stop.is_set():
                return None
            with self._lock:
                frame = self._latest
                if frame is not None and frame.index != self._last_served:
                    self._last_served = frame.index
                    return frame
            time.sleep(0.005)
        log.warning("5 секунд бойы жаңа кадр келмеді: %s", self.name)
        return None

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._cap:
            self._cap.release()
            self._cap = None
        log.info("Видео көзі жабылды: %s", self.name)

    @property
    def fps(self) -> float:
        return self._fps
