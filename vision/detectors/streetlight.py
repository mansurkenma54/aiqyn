"""Жанбай тұрған көше шамын анықтау (түнгі режим).

СТРАТЕГИЯЛЫҚ МАҢЫЗЫ: зерттеу бойынша iKOMEK 109-ға ең көп түсетін
санат — көше жарығы (~6 000 өтініш, оның ~1 500-і әлі қаралуда).
Яғни бұл — ең үлкен санатты жабатын детектор.

Әдісі — геометриялық, үйретілген модель қажет емес:
    1. Түнде жанып тұрған шамдар кадрда анық ашық дақ болып көрінеді.
    2. Бағаналар бірдей қашықтықта тұрады, сондықтан дақтар қатарында
       белгілі бір заңдылық болады: перспективаға байланысты аралық
       алыстаған сайын тұрақты коэффициентпен КЕМИДІ (геометриялық прогрессия).
    3. Егер бір шам жанбаса — қатардағы бір аралық күтілгеннен әлдеқайда
       ҮЛКЕН болады. Дәл сол жер — жанбайтын шам.

Бұл әдіс "бағананы тану" сияқты күрделі есептен әлдеқайда сенімді:
біз бар нәрсені (жанған шам) санаймыз, жоқ нәрсені (қараңғы бағана)
іздемейміз.
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

from ..config import Config
from .base import Detection, Detector

log = logging.getLogger(__name__)

WORK_WIDTH = 640
MIN_LAMPS_PER_SIDE = 4        # заңдылықты анықтау үшін ең аз шам саны
GAP_TOLERANCE = 1.55          # күтілгеннен осынша есе үлкен аралық = жетіспейтін шам
MAX_MISSING_PER_GAP = 3       # бір аралықтағы ең көп жетіспейтін шам


class StreetlightDetector(Detector):
    name = "streetlight"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    # ---------- шам дақтарын табу ----------

    def _find_lamps(self, gray: np.ndarray) -> list[tuple[float, float, float]]:
        """Жанып тұрған шамдардың (x, y, аудан) тізімі."""
        h, w = gray.shape[:2]

        # Табалдырықты кадрдың өзіне бейімдейміз: ең жарық 0.3% пиксель.
        # Тұрақты сан (мыс. 240) әр камерада әртүрлі жұмыс істер еді.
        threshold = max(200.0, float(np.percentile(gray, 99.7)) - 10.0)
        _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

        count, _, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)

        frame_area = float(h * w)
        lamps: list[tuple[float, float, float]] = []

        for i in range(1, count):
            area = float(stats[i, cv2.CC_STAT_AREA])
            bw = float(stats[i, cv2.CC_STAT_WIDTH])
            bh = float(stats[i, cv2.CC_STAT_HEIGHT])
            cx, cy = float(centroids[i][0]), float(centroids[i][1])

            if area < 4 or area / frame_area > 0.010:
                continue                                  # шу немесе тым үлкен жарық көзі
            if cy > h * 0.80:
                continue                                  # жол бетіндегі шағылысу
            if bw > w * 0.10 or bh > h * 0.10:
                continue
            aspect = bw / max(1.0, bh)
            if aspect > 4.0 or aspect < 0.25:
                continue                                  # шам дөңгелекке жақын болады

            lamps.append((cx, cy, area))

        return lamps

    # ---------- қатардағы үзілісті табу ----------

    @staticmethod
    def _find_gaps(lamps: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
        """Бір жақтағы шамдар қатарынан жетіспейтін орындарды табу.

        Қайтарады: (x, y, сенімділік) — жетіспейтін шамның болжамды орны.
        """
        if len(lamps) < MIN_LAMPS_PER_SIDE:
            return []

        # Жақыннан алысқа қарай реттейміз (кадрда: төменнен жоғарыға)
        ordered = sorted(lamps, key=lambda p: -p[1])
        points = np.array([(p[0], p[1]) for p in ordered], dtype=np.float64)

        gaps = np.linalg.norm(np.diff(points, axis=0), axis=1)
        if len(gaps) < 3 or np.any(gaps < 1e-3):
            return []

        # Перспектива: әр келесі аралық алдыңғысынан r есе кіші
        ratios = gaps[1:] / gaps[:-1]
        ratios = ratios[(ratios > 0.15) & (ratios < 1.6)]
        if len(ratios) < 2:
            return []
        r = float(np.median(ratios))
        if not (0.30 <= r <= 1.20):
            return []

        missing: list[tuple[float, float, float]] = []

        for i in range(1, len(gaps)):
            expected = gaps[i - 1] * r
            if expected < 1e-3:
                continue
            factor = gaps[i] / expected
            if factor < GAP_TOLERANCE:
                continue

            # Аралыққа қанша шам сыяды
            count = int(round(factor)) - 1
            count = max(1, min(MAX_MISSING_PER_GAP, count))

            start = points[i]
            end = points[i + 1]
            for k in range(1, count + 1):
                t = k / float(count + 1)
                x = start[0] + (end[0] - start[0]) * t
                y = start[1] + (end[1] - start[1]) * t
                # Заңдылық неғұрлым айқын бұзылса — сенімділік соғұрлым жоғары
                confidence = min(0.90, 0.55 + (factor - GAP_TOLERANCE) * 0.30)
                missing.append((float(x), float(y), float(confidence)))

        return missing

    # ---------- негізгі ----------

    def detect(self, image: np.ndarray, context: Optional[dict] = None) -> list[Detection]:
        context = context or {}
        if not context.get("is_night", False):
            return []                       # бұл детектор тек түнде жұмыс істейді

        h, w = image.shape[:2]
        scale = WORK_WIDTH / float(w)
        small = cv2.resize(image, (WORK_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        sh, sw = gray.shape[:2]

        lamps = self._find_lamps(gray)
        if len(lamps) < MIN_LAMPS_PER_SIDE:
            return []

        # Сол және оң жақ бағаналар қатары бөлек талданады
        left = [p for p in lamps if p[0] < sw * 0.5]
        right = [p for p in lamps if p[0] >= sw * 0.5]

        detections: list[Detection] = []
        for side_name, side_lamps in (("left", left), ("right", right)):
            for x, y, confidence in self._find_gaps(side_lamps):
                # Жетіспейтін шамның айналасына тіктөртбұрыш саламыз.
                # Өлшемі — сол жақтағы шамдардың орташа өлшеміне пропорционал.
                mean_area = float(np.mean([p[2] for p in side_lamps]))
                radius = max(14.0, min(45.0, np.sqrt(mean_area) * 4.0))

                x1 = int((x - radius) / scale)
                y1 = int((y - radius) / scale)
                x2 = int((x + radius) / scale)
                y2 = int((y + radius) / scale)

                detections.append(
                    Detection(
                        class_key="streetlight_out",
                        confidence=float(confidence),
                        bbox=(max(0, x1), max(0, y1), min(w, x2), min(h, y2)),
                        detector="streetlight",
                        extra={
                            "side": side_name,
                            "lamps_detected": len(side_lamps),
                            "method": "geometric-gap",
                        },
                    )
                )

        if detections:
            log.debug("Түнгі режим: %d шам табылды, %d жанбайды",
                      len(lamps), len(detections))

        return detections


def is_night(image: np.ndarray, threshold: float) -> bool:
    """Кадрдың орташа жарықтығы бойынша түн/күн анықтау."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(np.mean(gray)) < threshold
