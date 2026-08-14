"""YOLOv8 — жол жабынының ақауын анықтау (RDD2022).

Салмақтар: github.com/oracl4/RoadDamageDetection — RDD2022 датасетінде
(47 420 сурет, 55 000+ белгіленген ақау) үйретілген.

Салмақты жүктеу:
    python scripts/fetch_weights.py

Егер RDD салмағы табылмаса, программа тоқтап қалмайды: жалпы COCO
моделіне ауысып, тек "жол бөгеті" (obstruction) режимінде жұмыс істейді
және журналға анық ескерту жазады.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import Config
from .base import Detection, Detector

log = logging.getLogger(__name__)


# Модель класының атауы -> AIQYN ішкі кілті.
# Әртүрлі репозиторийде атау әрқалай жазылған, сондықтан кілт сөз бойынша іздейміз.
NAME_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("pothole", "potholes", "d40", "yama"), "pothole"),
    (("alligator", "aligator", "d20", "block crack", "fatigue"), "crack_alligator"),
    (("longitudinal", "d00", "wheel mark"), "crack_longitudinal"),
    (("transverse", "d10", "lateral"), "crack_transverse"),
    (("manhole", "utility hole", "d43", "cover"), "manhole_open"),
    (("flood", "water", "puddle"), "flood"),
    (("debris", "obstruction", "obstacle"), "obstruction"),
]

# COCO-моделіне түсіп қалған жағдайда: жолдағы бөгет болуы мүмкін нысандар
COCO_OBSTRUCTION = {
    "car", "truck", "bus", "motorcycle", "bicycle",
    "bench", "chair", "suitcase", "backpack", "potted plant",
    "cow", "horse", "sheep", "dog", "elephant",
}


def map_class_name(raw: str) -> Optional[str]:
    """Модель класының атауын AIQYN санатына айналдыру."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    for patterns, key in NAME_PATTERNS:
        for pattern in patterns:
            if pattern in text:
                return key
    return None


class RoadDamageDetector(Detector):
    name = "yolo"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.model = None
        self.class_map: dict[int, str] = {}
        self.is_coco_fallback = False
        self._load()

    # ---------- жүктеу ----------

    def _resolve_weights(self) -> tuple[str, bool]:
        """Салмақ файлын табу. (жол, coco_fallback_па) қайтарады."""
        path: Path = self.cfg.weights_path
        if path.exists():
            return str(path), False

        log.warning("=" * 68)
        log.warning("RDD салмағы табылмады: %s", path)
        log.warning("Жол ақауын анықтау ӨШІРУЛІ болады — тек бөгет (obstruction).")
        log.warning("Түзету үшін:  python scripts/fetch_weights.py")
        log.warning("=" * 68)
        return "yolov8n.pt", True

    def _load(self) -> None:
        from ultralytics import YOLO  # ауыр импорт — тек керек кезде

        weights, is_fallback = self._resolve_weights()
        self.is_coco_fallback = is_fallback

        log.info("YOLO жүктелуде: %s (device=%s)", weights, self.cfg.device)
        self.model = YOLO(weights)

        names = self.model.names or {}
        if isinstance(names, (list, tuple)):
            names = dict(enumerate(names))

        unmapped: list[str] = []
        for idx, raw in names.items():
            if is_fallback:
                if str(raw).lower() in COCO_OBSTRUCTION:
                    self.class_map[int(idx)] = "obstruction"
                continue
            key = map_class_name(str(raw))
            if key:
                self.class_map[int(idx)] = key
            else:
                unmapped.append(str(raw))

        log.info(
            "Модель кластары: %s",
            ", ".join(f"{names[i]} -> {k}" for i, k in sorted(self.class_map.items())) or "жоқ",
        )
        if unmapped:
            log.warning("Сәйкестендірілмеген кластар (еленбейді): %s", ", ".join(unmapped))

    def warmup(self) -> None:
        if self.model is None:
            return
        blank = np.zeros((self.cfg.imgsz, self.cfg.imgsz, 3), dtype=np.uint8)
        try:
            self.model.predict(
                blank, imgsz=self.cfg.imgsz, device=self.cfg.device, verbose=False
            )
            log.info("YOLO дайын (warmup орындалды).")
        except Exception as exc:
            log.warning("Warmup сәтсіз: %s", exc)

    # ---------- детекция ----------

    def detect(self, image: np.ndarray, context: Optional[dict] = None) -> list[Detection]:
        if self.model is None:
            return []

        try:
            results = self.model.predict(
                image,
                imgsz=self.cfg.imgsz,
                conf=max(0.15, self.cfg.conf_threshold - 0.15),  # сүзгіні кейін өзіміз саламыз
                device=self.cfg.device,
                verbose=False,
            )
        except Exception as exc:
            log.error("YOLO детекциясы сәтсіз: %s", exc)
            return []

        frame_area = float(image.shape[0] * image.shape[1])
        detections: list[Detection] = []

        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                cls_id = int(box.cls[0])
                key = self.class_map.get(cls_id)
                if key is None:
                    continue

                confidence = float(box.conf[0])
                if confidence < self.cfg.conf_threshold:
                    continue

                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                if (x2 - x1) * (y2 - y1) / frame_area < self.cfg.min_box_area_frac:
                    continue

                detections.append(
                    Detection(
                        class_key=key,
                        confidence=confidence,
                        bbox=(x1, y1, x2, y2),
                        detector="yolo",
                        extra={"model_class": cls_id},
                    )
                )

        return detections
