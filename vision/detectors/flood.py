"""Су тасу / шалшық — ережеге негізделген (rule-based) детектор.

Неге YOLO емес: RDD2022 датасетінде "су тасу" класы жоқ, ал 5 күнде
жаңа класты жинап үйрету — тәуекел. Ал судың физикалық белгісі
классикалық әдіспен жақсы ажыратылады.

Судың белгілері (асфальтпен салыстырғанда):
  * ТЕГІС — асфальтта майда түйіршік (текстура) көп, суда ол жоқ
  * ШАҒЫЛЫСАДЫ — аспанды шағылыстырып, айналасынан ашық болады
  * ТҮСІ БОЗ — қанықтығы (saturation) төмен

Детектор ӘДЕЙІ қатаң бапталған: жалған детекция (көлеңкені су деп тану)
операторға артық жұмыс береді, сондықтан "аз тапқан" жақсы.
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

from ..config import Config
from .base import Detection, Detector
from .lanes import LaneDetector

log = logging.getLogger(__name__)

WORK_WIDTH = 480


class FloodDetector(Detector):
    name = "flood"

    def __init__(
        self,
        cfg: Config,
        texture_threshold: float = 6.0,     # осыдан төмен = тегіс бет
        min_area_frac: float = 0.020,       # жол аймағының кемінде 2%
        min_confidence: float = 0.55,
        require_lane: bool = True,          # тек жол жолағы табылған кадрда жұмыс істеу
    ):
        self.cfg = cfg
        self.texture_threshold = texture_threshold
        self.min_area_frac = min_area_frac
        self.min_confidence = min_confidence
        self.require_lane = require_lane

    def detect(self, image: np.ndarray, context: Optional[dict] = None) -> list[Detection]:
        context = context or {}

        # Түнде шағылысу мен фаралар тым көп жалған сигнал береді
        if context.get("is_night"):
            return []

        # ҚОРҒАНЫС №1: кадрда шынымен ЖОЛ болуы керек.
        # Жол жолағы табылып, екі сызық перспективамен жиналған болса ғана
        # жалғастырамыз. Бұл шартсыз детектор кез келген тегіс ақшыл бетті
        # (экран суреті, қабырға, аспан) су деп тануы мүмкін.
        lane = context.get("lane")
        road = context.get("road")
        has_road = road is not None and getattr(road, "found", False)
        if self.require_lane and not has_road and (lane is None or lane.polygon is None):
            return []

        h, w = image.shape[:2]
        scale = WORK_WIDTH / float(w)
        small = cv2.resize(image, (WORK_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)

        # LaneDetector нәтижесі толық өлшемдегі image координатасында болады.
        # Бұрын оны тікелей small кадрға бергенде polygon кадрдан тыс қалып,
        # road_area=0 болып, flood детекторы үнсіз өшетін. Масканы алдымен
        # бастапқы өлшемде құрып, содан кейін nearest-neighbour арқылы кішірейтеміз.
        road_full = LaneDetector.road_mask(image.shape, lane)
        road = cv2.resize(
            road_full, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST
        )
        road_area = float(cv2.countNonZero(road))
        if road_area < 500:
            return []

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

        # 1) Текстура картасы: Лаплас операторының жергілікті орташа модулі.
        #    Асфальт -> жоғары мән, су беті -> төмен.
        laplacian = cv2.Laplacian(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_32F)
        texture = cv2.blur(np.abs(laplacian), (15, 15))
        smooth_mask = (texture < self.texture_threshold).astype(np.uint8) * 255

        # ҚОРҒАНЫС: су — асфальттың ТҮЙІРШІКТІ бетіндегі ТЕГІС дақ.
        # Егер бүкіл аймақ тегіс болса (мыс. кадрда жол емес, экран суреті
        # немесе біркелкі бет тұрса), «тегіс = су» деген қорытынды мағынасыз.
        smooth_fraction = cv2.countNonZero(cv2.bitwise_and(smooth_mask, road)) / road_area
        if smooth_fraction > 0.55:
            return []

        # 2) Қанықтығы төмен (сұрғылт/шағылысқан) аймақ
        low_saturation = (hsv[:, :, 1] < 60).astype(np.uint8) * 255

        # 3) Жол бетінің медианасынан ашықтау (аспан шағылысы) немесе әлдеқайда қараңғы (терең су)
        road_pixels = gray[road > 0]
        if road_pixels.size < 100:
            return []
        median = float(np.median(road_pixels))
        brighter = (gray > median + 12).astype(np.uint8) * 255
        darker = (gray < median - 35).astype(np.uint8) * 255
        reflective = cv2.bitwise_or(brighter, darker)

        candidate = cv2.bitwise_and(smooth_mask, low_saturation)
        candidate = cv2.bitwise_and(candidate, reflective)
        candidate = cv2.bitwise_and(candidate, road)

        # Морфология: ұсақ шуды тазалап, тұтас аймақтарды біріктіру
        kernel = np.ones((7, 7), np.uint8)
        candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, kernel, iterations=1)
        candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, kernel, iterations=2)

        contours, _ = cv2.findContours(candidate, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[Detection] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            area_frac = area / road_area
            if area_frac < self.min_area_frac:
                continue

            x, y, bw, bh = cv2.boundingRect(contour)
            if bh == 0:
                continue

            # Шалшық көлденеңінен жайылады — тік, жіңішке дақ су емес
            aspect = bw / float(bh)
            if aspect < 0.9:
                continue

            # Толықтық: контурдың өз тіктөртбұрышын қаншалық толтыратыны
            fill = area / float(max(1, bw * bh))
            if fill < 0.45:
                continue

            confidence = min(0.94, 0.45 + area_frac * 2.2 + fill * 0.25)
            if confidence < self.min_confidence:
                continue

            detections.append(
                Detection(
                    class_key="flood",
                    confidence=float(confidence),
                    bbox=(
                        int(x / scale), int(y / scale),
                        int((x + bw) / scale), int((y + bh) / scale),
                    ),
                    detector="flood",
                    extra={
                        "area_frac_of_road": round(area_frac, 4),
                        "fill": round(fill, 3),
                        "method": "rule-based",
                    },
                )
            )

        return detections
