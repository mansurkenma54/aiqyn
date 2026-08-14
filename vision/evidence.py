"""Дәлел (evidence) жинау — 5-10 секундтық видео клип + ең анық фото.

Логикасы:
    * Соңғы N секундтық кадрлар үнемі сақина буферде (ring buffer) тұрады.
    * Ақау табылған сәтте буфердегі кадрлар "оқиғаға дейінгі" бөлік
      ретінде көшіріледі (pre-roll ~3 сек).
    * Одан кейінгі кадрлар жиналуын жалғастырады (post-roll ~5 сек).
    * Барлығы жиналғанда — файлға жазу БӨЛЕК АҒЫНДА жүреді, негізгі
      цикл тоқтап қалмайды.

Нәтижесі: /evidence/{event_id}.mp4  және  /evidence/{event_id}.jpg
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Recording:
    event_id: str
    frames: list[np.ndarray]
    started_at: float
    ends_at: float
    target_frames: int
    best_image: Optional[np.ndarray] = None
    best_confidence: float = 0.0
    meta: dict = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        # Негізгі өлшем — КАДР САНЫ, уақыт емес. Себебі видеофайлды тез
        # өңдегенде (--fast) кадрлар нақты уақыттан әлдеқайда жылдам келеді
        # де, уақытпен өлшесек клип бірнеше есе ұзын болып кетеді.
        if len(self.frames) >= self.target_frames:
            return True
        # Сақтандыру: тірі ағын үзіліп қалса, шексіз күтіп қалмау үшін
        return time.time() >= self.ends_at


@dataclass
class EvidenceFiles:
    event_id: str
    photo: Path
    video: Optional[Path]
    frame_count: int
    duration_sec: float


class EvidenceRecorder:
    """Сақина буфер + клип жазу."""

    def __init__(
        self,
        cfg: Config,
        on_ready: Optional[Callable[[EvidenceFiles, dict], None]] = None,
        annotator: Optional[Callable[[np.ndarray, dict], np.ndarray]] = None,
    ):
        self.cfg = cfg
        self.on_ready = on_ready
        # Дәлел фотосына ақауды белгілейтін функция. Ресми құжатқа
        # белгіленбеген фото түссе, оны қарап отырған адам ақаудың дәл
        # қай жерде екенін таба алмайды.
        self.annotator = annotator
        self.output_dir = cfg.evidence_path
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.fps = float(cfg.clip_fps)
        self._buffer: deque[np.ndarray] = deque(maxlen=self._buffer_size())
        self._active: dict[str, Recording] = {}
        self._lock = threading.Lock()
        self._writer_threads: list[threading.Thread] = []

        log.info(
            "Дәлел қалтасы: %s (pre-roll %.1fс, post-roll %.1fс)",
            self.output_dir, cfg.pre_roll_sec, cfg.post_roll_sec,
        )

    def _buffer_size(self) -> int:
        return max(15, int(self.cfg.pre_roll_sec * self.fps) + 5)

    def set_fps(self, fps: float) -> None:
        """Видео көзінің нақты FPS-ін орнату.

        Дәлел клипінің ұзақтығы мен ойнату жылдамдығы осыған байланысты:
        30 FPS видеодан 8 секундтық клип = 240 кадр.
        """
        if not (1.0 < fps < 121.0):
            return
        self.fps = float(fps)
        with self._lock:
            self._buffer = deque(self._buffer, maxlen=self._buffer_size())
        log.info("Дәлел жазғышының FPS-і: %.1f", self.fps)

    # ---------- кадр ағыны ----------

    def feed(self, image: np.ndarray) -> None:
        """Әр кадрды осында береміз."""
        with self._lock:
            self._buffer.append(image)
            for recording in self._active.values():
                recording.frames.append(image)

            finished = [rec for rec in self._active.values() if rec.is_complete]
            for recording in finished:
                self._active.pop(recording.event_id, None)

        for recording in finished:
            self._finalize_async(recording)

    # ---------- оқиға ----------

    def start_event(
        self,
        event_id: str,
        best_image: Optional[np.ndarray] = None,
        confidence: float = 0.0,
        meta: Optional[dict] = None,
    ) -> None:
        """Жаңа оқиға бойынша дәлел жинауды бастау."""
        with self._lock:
            if event_id in self._active:
                return
            pre_roll = list(self._buffer)
            now = time.time()
            target = len(pre_roll) + int(self.cfg.post_roll_sec * self.fps)
            self._active[event_id] = Recording(
                event_id=event_id,
                frames=pre_roll,
                started_at=now,
                ends_at=now + self.cfg.post_roll_sec * 3.0,   # тек сақтандыру шегі
                target_frames=target,
                best_image=best_image if best_image is not None else (pre_roll[-1] if pre_roll else None),
                best_confidence=confidence,
                meta=meta or {},
            )
        log.info("Дәлел жиналуда: %s (pre-roll %d кадр)", event_id, len(pre_roll))

    def update_best(self, event_id: str, image: np.ndarray, confidence: float) -> None:
        """Анығырақ кадр табылса — дәлел фотосын жаңарту."""
        with self._lock:
            recording = self._active.get(event_id)
            if recording and confidence > recording.best_confidence:
                recording.best_image = image
                recording.best_confidence = confidence

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    # ---------- файлға жазу ----------

    def _finalize_async(self, recording: Recording) -> None:
        thread = threading.Thread(
            target=self._finalize,
            args=(recording,),
            name=f"evidence-{recording.event_id[:8]}",
            daemon=True,
        )
        thread.start()
        self._writer_threads = [t for t in self._writer_threads if t.is_alive()]
        self._writer_threads.append(thread)

    def _finalize(self, recording: Recording) -> None:
        try:
            photo_path = self.output_dir / f"{recording.event_id}.jpg"
            video_path = self.output_dir / f"{recording.event_id}.mp4"

            # --- фото ---
            image = recording.best_image
            if image is None and recording.frames:
                image = recording.frames[len(recording.frames) // 2]
            if image is None:
                log.error("Дәлел фотосы жоқ: %s", recording.event_id)
                return

            # Түпнұсқа (белгіленбеген) нұсқасын да сақтаймыз — дау туса,
            # өңделмеген кадр дәлел бола алады
            raw_path = self.output_dir / f"{recording.event_id}_raw.jpg"
            cv2.imwrite(str(raw_path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

            if self.annotator is not None:
                try:
                    annotated = self.annotator(image, recording.meta)
                    if annotated is not None:
                        image = annotated
                except Exception as exc:
                    log.warning("Дәлел фотосы белгіленбеді: %s", exc)

            cv2.imwrite(str(photo_path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])

            # --- видео ---
            written = self._write_clip(video_path, recording.frames)
            duration = len(recording.frames) / max(1.0, self.fps)

            files = EvidenceFiles(
                event_id=recording.event_id,
                photo=photo_path,
                video=video_path if written else None,
                frame_count=len(recording.frames),
                duration_sec=round(duration, 1),
            )

            log.info(
                "Дәлел дайын: %s (%d кадр, ~%.1f сек)%s",
                recording.event_id, files.frame_count, files.duration_sec,
                "" if written else "  [видео жазылмады]",
            )

            if self.on_ready:
                self.on_ready(files, recording.meta)

        except Exception as exc:
            log.exception("Дәлелді сақтау сәтсіз (%s): %s", recording.event_id, exc)

    def _write_clip(self, path: Path, frames: list[np.ndarray]) -> bool:
        if len(frames) < 3:
            log.warning("Клип үшін кадр тым аз (%d) — тек фото сақталады.", len(frames))
            return False

        height, width = frames[0].shape[:2]
        # mp4v — браузерде де, VLC-де де ашылады, қосымша кодек орнатудың қажеті жоқ
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(path), fourcc, self.fps, (width, height))

        if not writer.isOpened():
            log.error("Видео жазғыш ашылмады: %s", path)
            return False

        try:
            for frame in frames:
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                writer.write(frame)
        finally:
            writer.release()

        return path.exists() and path.stat().st_size > 1024

    def wait_all(self, timeout: float = 20.0) -> None:
        """Бағдарламаны жабар алдында аяқталмаған жазуларды күту."""
        with self._lock:
            pending = list(self._active.values())
            self._active.clear()

        for recording in pending:
            self._finalize(recording)

        deadline = time.time() + timeout
        for thread in self._writer_threads:
            remaining = deadline - time.time()
            if remaining > 0:
                thread.join(timeout=remaining)
