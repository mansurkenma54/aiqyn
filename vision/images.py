"""Дайын фотосуреттерді талдау (топтама режимі).

Не үшін керек:
    * Телефон қосылмай тұрып жүйені тексеру
    * Бұрын түсірілген жол суреттерінен бірден құжат жасау
    * Жюриге көрсету: «мына суретті беріңіз — жүйе құжат құрастырады»

Координата көзі (кезегімен):
    1. Суреттің өз EXIF деректері (телефон түсірген файлда болады)
    2. --lat / --lon аргументтері
    3. config.json ішіндегі fallback (Шымкент орталығы)

Видеофайлдан дәлел клипін кесу үшін бұл емес, негізгі режим қолданылады:
    python -m vision.main run --source file --video жол.mp4
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .aiverify import AIVerifier
from .config import Config
from .dedup import Deduplicator
from .detectors import DetectorBank
from .document import build_document, new_event_id
from .geocode import Geocoder
from .exif import gps_from_image
from .gps import GpsFix
from .overlay import Hud, HudStats
from .sender import PortalSender

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def read_image(path: str | Path):
    """Windows-та кириллица/Unicode жолын да сенімді ашу.

    Кей OpenCV Windows build-тарында cv2.imread() Unicode файл атауын
    аша алмайды. Python файл API-і байттарды оқиды, ал imdecode сол байттан
    суретті платформадан тәуелсіз декодтайды.
    """
    try:
        payload = np.fromfile(str(path), dtype=np.uint8)
        if payload.size == 0:
            return None
        return cv2.imdecode(payload, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None


def write_image(path: str | Path, image, quality: int = 92) -> bool:
    """Unicode файл атауына OpenCV суретін сенімді жазу."""
    target = Path(path)
    suffix = target.suffix.lower() or ".jpg"
    params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)] if suffix in {".jpg", ".jpeg"} else []
    try:
        ok, encoded = cv2.imencode(suffix, image, params)
        if not ok:
            return False
        target.write_bytes(encoded.tobytes())
        return True
    except (OSError, ValueError, cv2.error):
        return False


def collect_images(target: str | Path) -> list[Path]:
    """Файл немесе қалтадан суреттер тізімін жинау."""
    path = Path(target)
    if path.is_file():
        return [path] if path.suffix.lower() in IMAGE_SUFFIXES else []
    if path.is_dir():
        return sorted(
            p for p in path.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        )
    return []


class ImageBatchProcessor:
    """Суреттерді бірінен соң бірін талдап, толық құжат құрастырады."""

    def __init__(self, cfg: Config, lat: Optional[float] = None, lon: Optional[float] = None):
        self.cfg = cfg
        self.detectors = DetectorBank(cfg)
        self.geocoder = Geocoder(cfg)
        self.dedup = Deduplicator(cfg)
        self.sender = PortalSender(cfg)
        self.verifier = AIVerifier(cfg)
        self.hud = Hud(cfg)
        self.hud.show_debug = False      # суретте статистика панелі артық
        self.hud.show_captions = True    # ресми evidence-де санат/сенімділік көрінсін
        self.manual_lat = lat
        self.manual_lon = lon

        self.evidence_dir = cfg.evidence_path
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

        self.processed = 0
        self.documents = 0

    # ---------- координата ----------

    def _resolve_fix(self, image_path: Path) -> GpsFix:
        if self.manual_lat is not None and self.manual_lon is not None:
            return GpsFix(
                lat=self.manual_lat, lon=self.manual_lon, ts=time.time(),
                source="manual", trusted=True,
            )

        exif = gps_from_image(image_path)
        if exif and "lat" in exif:
            log.info("   EXIF координатасы табылды: %.5f, %.5f", exif["lat"], exif["lon"])
            return GpsFix(
                lat=exif["lat"], lon=exif["lon"],
                ts=exif.get("timestamp", time.time()),
                source="exif", trusted=True,
            )

        log.warning("   Координата табылмады — әдепкі нүкте қолданылады (--lat/--lon беріңіз)")
        return GpsFix(
            lat=self.cfg.fallback_lat, lon=self.cfg.fallback_lon,
            ts=time.time(), source="fallback", trusted=False,
        )

    # ---------- бір сурет ----------

    def process_image(self, image_path: Path, save_preview: bool = True) -> list[dict]:
        image = read_image(image_path)
        if image is None:
            log.error("Сурет оқылмады: %s", image_path)
            return []
        image = self.cfg.crop(image)

        log.info("Талдануда: %s (%dx%d)", image_path.name, image.shape[1], image.shape[0])

        # Қалтадағы фотолар бір видео тізбегі емес. Алдыңғы суреттің EMA
        # lane күйі келесі тәуелсіз суретке ауыспауға тиіс.
        if self.detectors.lane_detector is not None:
            self.detectors.lane_detector.reset()
        detections, lane, _ = self.detectors.process(image)
        if not detections:
            log.info("   Ақау табылмады.")
            return []

        log.info("   %d ақау табылды: %s", len(detections),
                 ", ".join(f"{d.class_key} {d.confidence*100:.0f}%" for d in detections))

        fix = self._resolve_fix(image_path)
        detected_at = fix.ts if fix.source == "exif" else time.time()
        address = self.geocoder.reverse(fix.lat, fix.lon)

        # Белгіленген нұсқасын сақтау (жюриге көрсету үшін ыңғайлы)
        if save_preview:
            annotated = self.hud.render(
                image, detections, lane, fix, HudStats(), captured_at=detected_at
            )
            preview_path = self.evidence_dir / f"{image_path.stem}_annotated.jpg"
            if write_image(preview_path, annotated, quality=92):
                log.info("   Белгіленген сурет: %s", preview_path.name)
            else:
                log.warning("   Белгіленген сурет жазылмады: %s", preview_path)

        documents: list[dict] = []
        for detection in detections:
            # Дедупликацияны тек НАҚТЫ координата болғанда қолданамыз.
            # Әйтпесе бүкіл топтама бірдей әдепкі нүктені пайдаланады да,
            # әртүрлі суреттегі бөлек ақаулар «қайталанған» болып қалады.
            if fix.trusted and not self.dedup.check(detection.class_key, fix.lat, fix.lon):
                continue

            event_id = new_event_id()
            self.dedup.remember(detection.class_key, fix.lat, fix.lon, event_id)

            # Дәлел фотосы: ақауы белгіленген нұсқасы (ресми құжатқа сол түседі),
            # қасында түпнұсқасы да сақталады
            photo_path = self.evidence_dir / f"{event_id}.jpg"
            shutil.copy2(image_path, self.evidence_dir / f"{event_id}_raw.jpg")

            marked = self.hud.render(
                image, [detection], lane, fix, HudStats(), captured_at=detected_at
            )
            if not write_image(photo_path, marked, quality=92):
                log.error("Дәлел фотосы жазылмады: %s", photo_path)
                continue

            # Екінші саты: ИИ көру моделі талдап, қорытынды жазады
            verdict = self.verifier.verify(
                photo_path, detection.class_key, detection.confidence,
                address=address.get("address_text", ""),
                when=time.strftime("%d.%m.%Y %H:%M", time.localtime(detected_at)),
            )
            if verdict is not None and not verdict.is_real and self.cfg.ai_drop_rejected:
                log.info("   ИИ жоққа шығарды — құжат құрылмады (%s)", verdict.reason[:80])
                photo_path.unlink(missing_ok=True)
                continue

            document = build_document(
                cfg=self.cfg,
                event_id=event_id,
                class_key=detection.class_key,
                confidence=detection.confidence,
                area_frac=detection.area_frac(image.shape),
                fix=fix,
                address=address,
                detected_at=detected_at,
                detector_name=detection.detector,
                video_seconds=0.0,
                has_video=False,
                marked_on_road=False,
                extra={
                    "bbox": list(detection.bbox),
                    "source_file": image_path.name,
                    **detection.extra,
                },
                ai=verdict.as_dict() if verdict else None,
            )

            (self.evidence_dir / f"{event_id}.json").write_text(
                json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            log.info(
                "   ҚҰЖАТ: %s | %s | %s",
                event_id, document["defect_type_official"], document["address_text"],
            )

            self.sender.send(document, photo_path, None)
            documents.append(document)
            self.documents += 1

        return documents

    # ---------- топтама ----------

    def run(self, target: str | Path) -> int:
        images = collect_images(target)
        if not images:
            log.error("Сурет табылмады: %s", target)
            return 1

        log.info("=" * 68)
        log.info("Топтама режимі: %d сурет", len(images))
        log.info("=" * 68)

        self.detectors.warmup()
        self.sender.start_retry_loop()

        for image_path in images:
            try:
                self.process_image(image_path)
                self.processed += 1
            except Exception as exc:
                log.exception("Сурет талдау сәтсіз (%s): %s", image_path.name, exc)

        self.sender.flush_outbox()
        self.sender.stop()

        log.info("=" * 68)
        log.info("ҚОРЫТЫНДЫ")
        log.info("  Талданған сурет     : %d", self.processed)
        log.info("  Құрастырылған құжат : %d", self.documents)
        log.info("  Сайтқа жіберілді    : %d", self.sender.sent_count)
        log.info("  Кезекте қалды       : %d", self.sender.pending_count())
        log.info("  Файлдар             : %s", self.evidence_dir)
        log.info("=" * 68)
        return 0
