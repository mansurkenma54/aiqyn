"""Детекторлардың ортақ интерфейсі мен нәтиже құрылымы."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Detection:
    """Бір анықталған ақау."""

    class_key: str                       # categories.py ішіндегі кілт
    confidence: float
    bbox: tuple[int, int, int, int]      # x1, y1, x2, y2 (кадр пикселінде)
    detector: str = "yolo"               # yolo | flood | streetlight
    extra: dict = field(default_factory=dict)

    @property
    def width(self) -> int:
        return max(0, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> int:
        return max(0, self.bbox[3] - self.bbox[1])

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    def area_frac(self, frame_shape) -> float:
        h, w = frame_shape[:2]
        return self.area / float(max(1, h * w))

    def iou(self, other: "Detection") -> float:
        ax1, ay1, ax2, ay2 = self.bbox
        bx1, by1, bx2, by2 = other.bbox
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter == 0:
            return 0.0
        union = self.area + other.area - inter
        return inter / float(union) if union else 0.0


class Detector(ABC):
    """Барлық детекторға ортақ базалық класс."""

    name: str = "detector"
    enabled: bool = True

    @abstractmethod
    def detect(self, image: np.ndarray, context: Optional[dict] = None) -> list[Detection]:
        """Кадрдан ақауларды табу."""

    def warmup(self) -> None:
        """Алғашқы баяу шақыруды алдын ала жасап қою (демо кезінде кідіріс болмасын)."""
        return None


def merge_overlapping(detections: list[Detection], iou_threshold: float = 0.55) -> list[Detection]:
    """Бір ақауды бірнеше детектор тапса — біреуін қалдыру.

    Сенімділігі жоғарысы жеңеді. Әртүрлі санаттағы, бірақ бір жерде
    тұрған нысандар да біріктіріледі (мыс. шұңқыр мен торлы жарық
    бір-бірін жиі жабады).
    """
    ordered = sorted(detections, key=lambda d: d.confidence, reverse=True)
    kept: list[Detection] = []
    for candidate in ordered:
        if any(candidate.iou(k) >= iou_threshold for k in kept):
            continue
        kept.append(candidate)
    return kept
