"""Детекторлар жинағы.

DetectorBank — барлық детекторды бір кадрға жүргізіп, нәтижені
біріктіретін қабат. Pipeline тек осымен сөйлеседі, жеке детекторлар
туралы білмейді.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np

from ..config import Config
from .base import Detection, Detector, merge_overlapping
from .confirm import TemporalConfirmer
from .pothole_cv import PotholeCvDetector
from .flood import FloodDetector
from .lanes import LaneDetector, LaneResult
from .roadedge import RoadBoundary, RoadBoundaryDetector
from .streetlight import StreetlightDetector, is_night
from .yolo import RoadDamageDetector

log = logging.getLogger(__name__)

__all__ = [
    "Detection",
    "Detector",
    "DetectorBank",
    "LaneDetector",
    "LaneResult",
    "RoadBoundary",
    "RoadBoundaryDetector",
    "is_night",
]


class DetectorBank:
    """Барлық детекторды басқаратын қабат."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.detectors: list[Detector] = []
        self.lane_detector = LaneDetector() if (cfg.draw_lanes or cfg.enable_flood) else None
        # Жолдың шекарасы — таңбалау жоқ көшелерде де жұмыс істейді.
        # road_only қосулы болса, ол МІНДЕТТІ: ақау тек сол шекараның
        # ішінен ізделеді.
        self.road_detector = (
            RoadBoundaryDetector()
            if (cfg.draw_lanes or cfg.enable_flood or cfg.road_only)
            else None
        )
        self.confirmer = (
            TemporalConfirmer(cfg.confirm_frames, cfg.confirm_window, cfg.track_iou)
            if cfg.confirm_frames > 1
            else None
        )
        self.dropped_off_road = 0

        if cfg.enable_yolo:
            self.detectors.append(RoadDamageDetector(cfg))
        if cfg.enable_pothole_cv:
            self.detectors.append(PotholeCvDetector(cfg))
        if cfg.enable_flood:
            self.detectors.append(FloodDetector(cfg))
        if cfg.enable_streetlight:
            self.detectors.append(StreetlightDetector(cfg))

        log.info(
            "Іске қосылған детекторлар: %s",
            ", ".join(d.name for d in self.detectors) or "жоқ",
        )

        self._night = False
        self._night_checked_at = 0.0
        self._timings: dict[str, float] = {}

    def warmup(self) -> None:
        for detector in self.detectors:
            detector.warmup()

    # ---------- негізгі ----------

    def process(self, image: np.ndarray) -> tuple[list[Detection], Optional[LaneResult], dict]:
        """Бір кадрды толық талдау.

        Қайтарады: (детекциялар, жол жолағы, контекст)
        """
        now = time.time()

        # Түн/күн режимін әр 3 секунд сайын ғана тексереміз — әр кадрда қажет емес
        if now - self._night_checked_at > 3.0:
            self._night = is_night(image, self.cfg.night_brightness)
            self._night_checked_at = now

        # --- 1-САТЫ: ЖОЛДЫҢ ШЕКАРАСЫ ---
        # Ақауды іздемес бұрын жолдың өзін табамыз. Себебі екеу:
        #   а) интерфейсте жолдың шекарасы көрініп тұруы керек
        #   б) ақау тек ЖОЛ бетінен ізделуі тиіс — шөптегі дақ немесе
        #      тротуардағы жарық өтінім болып кетпеуі керек
        road = None
        if self.road_detector is not None:
            started = time.time()
            try:
                road = self.road_detector.detect(image)
            except Exception as exc:
                log.warning("Жол шекарасын анықтау сәтсіз: %s", exc)
            self._timings["roadedge"] = (time.time() - started) * 1000

        # --- 2-САТЫ: ЖОЛ ТАҢБАЛАУЫ (бар болса) ---
        lane: Optional[LaneResult] = None
        if self.lane_detector is not None:
            started = time.time()
            try:
                lane = self.lane_detector.detect(image)
            except Exception as exc:
                log.warning("Жол жолағын анықтау сәтсіз: %s", exc)
            self._timings["lanes"] = (time.time() - started) * 1000

        # Екеуін біріктіреміз: шекара — асфальттан, ортаңғы сызық — таңбалаудан
        if road is not None and road.found:
            if lane is None:
                lane = LaneResult()
            lane.left_curve = road.left
            lane.right_curve = road.right
            lane.road_mask = road.mask
            lane.horizon_y = road.horizon_y or lane.horizon_y
            lane.source = "both" if lane.left or lane.right else "surface"
            if lane.polygon is None and road.polygon is not None:
                lane.polygon = road.polygon

        context = {"is_night": self._night, "lane": lane, "road": road}

        detections: list[Detection] = []
        for detector in self.detectors:
            started = time.time()
            try:
                detections.extend(detector.detect(image, context))
            except Exception as exc:
                log.error("Детектор '%s' қатесі: %s", detector.name, exc)
            self._timings[detector.name] = (time.time() - started) * 1000

        detections = merge_overlapping(detections)

        # --- 3-САТЫ: ЖОЛ БЕТІМЕН ШЕКТЕУ ---
        if self.cfg.road_only:
            before = len(detections)
            detections = self._keep_on_road(detections, road)
            self.dropped_off_road += before - len(detections)

        # --- 4-САТЫ: УАҚЫТ БОЙЫНША РАСТАУ ---
        # Оқиға тек бірнеше кадрда расталғаннан кейін ғана құрылады
        context["confirmed"] = (
            self.confirmer.update(detections) if self.confirmer else detections
        )

        return detections, lane, context

    # ---------- жол бетімен шектеу ----------

    def _keep_on_road(self, detections: list[Detection], road) -> list[Detection]:
        """Тек жол бетіндегі ақауларды қалдыру.

        Детекцияның ТӨМЕНГІ ортасы — оның жермен жанасатын нүктесі.
        Дәл сол нүкте жол маскасының ішінде болуы керек. Шөптегі дақ,
        тротуардағы жарық, ғимарат — бәрі осы жерде сүзіледі.

        Жол шекарасы табылмаса сүзбейміз: әйтпесе шекара табылмаған
        кадрларда жүйе мүлдем үнсіз қалар еді.
        """
        if road is None or not getattr(road, "found", False) or road.mask is None:
            return detections

        mask = road.mask
        height, width = mask.shape[:2]
        margin = max(0, int(self.cfg.road_margin_px))

        kept: list[Detection] = []
        for detection in detections:
            x1, y1, x2, y2 = detection.bbox
            cx = max(0, min(width - 1, (x1 + x2) // 2))
            cy = max(0, min(height - 1, y2 - 2))

            lo_x, hi_x = max(0, cx - margin), min(width, cx + margin + 1)
            lo_y, hi_y = max(0, cy - margin), min(height, cy + margin + 1)

            if mask[lo_y:hi_y, lo_x:hi_x].any():
                kept.append(detection)

        return kept

    @property
    def is_night_now(self) -> bool:
        return self._night

    @property
    def timings_ms(self) -> dict[str, float]:
        return dict(self._timings)
