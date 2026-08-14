"""Уақыт бойынша растау — жалған детекцияны сүзудің ең күшті тәсілі.

МӘСЕЛЕ
------
YOLO бір кадрда асфальттың дағын, көлеңкені немесе қиыршық тасты
«шұңқыр» деп қате тануы мүмкін. Бірақ ондай қате ТҰРАҚСЫЗ: келесі
кадрда жоғалып кетеді.

Ал нағыз шұңқыр көлік оған жақындаған сайын БІРНЕШЕ кадрда қатарынан
көрінеді, әрі кадрда бірте-бірте ҮЛКЕЮІ керек (жақындап келе жатыр).

ШЕШІМ
-----
Детекцияларды кадрдан кадрға бақылаймыз (bbox қабаттасуы бойынша).
Оқиға тек `confirm_frames` рет расталғаннан кейін ғана нақты деп
саналады. Расталғанша ешқандай құжат құрылмайды.

Бұл — үйретілген модель қоспай-ақ дәлдікті көтерудің ең арзан жолы.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .base import Detection

log = logging.getLogger(__name__)


@dataclass
class Track:
    """Кадрдан кадрға бақыланып жүрген бір нысан."""

    class_key: str
    bbox: tuple[int, int, int, int]
    confidence: float
    hits: int = 1
    last_seen: int = 0            # соңғы көрінген талданған кадр нөмірі
    first_seen: int = 0
    confirmed: bool = False
    areas: list[int] = field(default_factory=list)

    @property
    def growing(self) -> bool:
        """Нысан кадрда ҮЛКЕЮДЕ ме (көлік жақындап келе жатыр)."""
        if len(self.areas) < 2:
            return True
        return self.areas[-1] >= self.areas[0] * 0.85


def _center_gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Екі тіктөртбұрыш ортасының ара қашықтығы, ӨЛШЕМГЕ шағылған.

    Неге IoU жеткіліксіз: көлік шұңқырға жақындағанда ол кадрда жылдам
    жылжиды әрі үлкейеді. Талданатын екі кадрдың арасы 0.2 секунд болса,
    қабаттасу нөлге тең болуы мүмкін — бірақ бұл БІР нысан.

    Сондықтан ортасының жылжуын нысанның өз өлшемімен салыстырамыз:
    өз енінен аспай жылжыса — сол нысан деп санаймыз.
    """
    ax = (a[0] + a[2]) / 2.0
    ay = (a[1] + a[3]) / 2.0
    bx = (b[0] + b[2]) / 2.0
    by = (b[1] + b[3]) / 2.0
    scale = max(12.0, max(a[2] - a[0], b[2] - b[0]))
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 / scale


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / float(union) if union else 0.0


class TemporalConfirmer:
    """Детекцияны бірнеше кадрда растайды."""

    def __init__(self, need_hits: int = 2, window: int = 6,
                 iou_threshold: float = 0.20, max_center_gap: float = 2.2):
        self.need_hits = max(1, need_hits)
        self.window = max(1, window)
        self.iou_threshold = iou_threshold
        # Нысан ортасы өз енінен осынша есе жылжыса да — сол нысан
        self.max_center_gap = max_center_gap
        self._tracks: list[Track] = []
        self._frame = 0

        self.seen = 0
        self.confirmed_count = 0

    def update(self, detections: list[Detection]) -> list[Detection]:
        """Кадрдағы детекцияларды қабылдап, РАСТАЛҒАНДАРЫН қайтарады.

        Бір нысан бір-ақ рет расталады: расталғаннан кейін ол қайта
        қайтарылмайды (дедупликация одан кейінгі қабат).
        """
        self._frame += 1
        self.seen += len(detections)

        matched_tracks: set[int] = set()
        confirmed_now: list[Detection] = []

        for detection in detections:
            # Ең жақсы сәйкестікті ЕКІ өлшеммен іздейміз: қабаттасу (IoU)
            # немесе ортасының жылжуы. Жылдам жақындаған нысанда бірінші
            # өлшем нашар жұмыс істейді, сондықтан екіншісі құтқарады.
            best_index, best_score = None, 0.0
            for index, track in enumerate(self._tracks):
                if index in matched_tracks or track.class_key != detection.class_key:
                    continue

                iou = _iou(track.bbox, detection.bbox)
                gap = _center_gap(track.bbox, detection.bbox)
                score = max(iou, 1.0 - min(1.0, gap / self.max_center_gap))

                if iou >= self.iou_threshold or gap <= self.max_center_gap:
                    if score > best_score:
                        best_index, best_score = index, score

            if best_index is None:
                # Жаңа нысан — бақылауға аламыз
                track = Track(
                    class_key=detection.class_key,
                    bbox=detection.bbox,
                    confidence=detection.confidence,
                    last_seen=self._frame,
                    first_seen=self._frame,
                    areas=[detection.area],
                )
                self._tracks.append(track)
                matched_tracks.add(len(self._tracks) - 1)

                if self.need_hits <= 1:
                    track.confirmed = True
                    self.confirmed_count += 1
                    confirmed_now.append(detection)
                continue

            # Бұрыннан бақылаудағы нысан
            track = self._tracks[best_index]
            matched_tracks.add(best_index)
            track.bbox = detection.bbox
            track.confidence = max(track.confidence, detection.confidence)
            track.hits += 1
            track.last_seen = self._frame
            track.areas.append(detection.area)

            if not track.confirmed and track.hits >= self.need_hits:
                track.confirmed = True
                self.confirmed_count += 1
                # Растау сәтіндегі ең жоғары сенімділікті береміз
                confirmed_now.append(
                    Detection(
                        class_key=detection.class_key,
                        confidence=track.confidence,
                        bbox=detection.bbox,
                        detector=detection.detector,
                        extra={
                            **detection.extra,
                            "confirm_hits": track.hits,
                            "confirm_frames": self._frame - track.first_seen + 1,
                        },
                    )
                )

        # Ескірген бақылауларды тазалаймыз
        self._tracks = [
            t for t in self._tracks if self._frame - t.last_seen <= self.window
        ]

        return confirmed_now

    @property
    def pending(self) -> int:
        return sum(1 for t in self._tracks if not t.confirmed)

    def stats(self) -> dict:
        return {
            "seen": self.seen,
            "confirmed": self.confirmed_count,
            "pending": self.pending,
        }
