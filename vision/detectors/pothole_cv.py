"""Шұңқырды физикалық белгісі бойынша табу (YOLO-ға қосымша).

НЕГЕ ЕКІНШІ ДЕТЕКТОР КЕРЕК
--------------------------
YOLO (RDD2022) нақты фотосуреттерде жақсы жұмыс істейді — далалық
бейнеде шұңқырды 52-75% сенімділікпен тапты. Бірақ ол ҮЙРЕТІЛГЕН
деректерге тәуелді: көрмеген түрдегі кадрда (басқа камера, басқа
жарық, симуляция) үнсіз қалады.

Ал шұңқырдың ФИЗИКАСЫ әрқашан бірдей:
    * ол — ойық, сондықтан айналасындағы асфальттан ҚАРАҢҒЫ
    * шеті тегіс емес, кесілген (жарықтың орнына сынық жиек)
    * пішіні дөңгелекке жақын, бірақ дұрыс емес
    * ол ЖОЛ БЕТІНДЕ жатады

Осы белгілер бойынша іздейтін детектор үйретуді қажет етпейді әрі
кез келген камерада бірдей жұмыс істейді. Екеуі қатар жүргенде
жүйе әлдеқайда тұрақты болады.

МАҢЫЗДЫ: детектор ТЕК жол маскасының ішінен іздейді. Шөптегі көлеңке,
тротуардағы дақ — бәрі сыртта қалады.
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

from ..config import Config
from .base import Detection, Detector

log = logging.getLogger(__name__)

WORK_WIDTH = 480


class PotholeCvDetector(Detector):
    name = "pothole_cv"

    def __init__(
        self,
        cfg: Config,
        darkness: float = 20.0,         # жергілікті ортадан осынша күңгірт
        min_area_frac: float = 0.0015,  # жол ауданының үлесі
        max_area_frac: float = 0.14,
        min_solidity: float = 0.70,     # пішіннің тұтастығы (көлеңке тарамдалады)
        min_edge: float = 14.0,         # жиектің өткірлігі (көлеңкеде төмен)
        min_confidence: float = 0.55,
    ):
        self.cfg = cfg
        self.darkness = darkness
        self.min_area_frac = min_area_frac
        self.max_area_frac = max_area_frac
        self.min_solidity = min_solidity
        self.min_edge = min_edge
        self.min_confidence = min_confidence

    def detect(self, image: np.ndarray, context: Optional[dict] = None) -> list[Detection]:
        context = context or {}
        road = context.get("road")

        # Жол шекарасы жоқ болса — іздемейміз. «Қайда іздеу керегін»
        # білмей іздеу жалған детекцияға әкеледі.
        if road is None or not getattr(road, "found", False) or road.mask is None:
            return []

        height, width = image.shape[:2]
        scale = WORK_WIDTH / float(width)
        small = cv2.resize(image, (WORK_WIDTH, int(height * scale)),
                           interpolation=cv2.INTER_AREA)
        mask = cv2.resize(road.mask, (small.shape[1], small.shape[0]),
                          interpolation=cv2.INTER_NEAREST)

        road_area = float(cv2.countNonZero(mask))
        if road_area < 800:
            return []

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 5, 40, 40)   # шуды басып, жиекті сақтау

        # Жергілікті фон: асфальттың сол маңдағы қалыпты жарықтығы.
        # Үлкен ядромен бұлдырату — «айналасымен салыстыру» дегеннің өзі.
        background = cv2.blur(gray, (81, 81))
        difference = background.astype(np.int16) - gray.astype(np.int16)

        dark = ((difference > self.darkness) & (mask > 0)).astype(np.uint8) * 255
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)

        # Жиектің өткірлігі — шұңқырды КӨЛЕҢКЕДЕН ажырататын басты белгі.
        # Шұңқырдың шеті — сынған асфальт, градиенті күрт. Көлеңкенің
        # шеті біртіндеп өтеді, градиенті жайлы.
        sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        gradient = cv2.magnitude(sobel_x, sobel_y)

        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[Detection] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            area_frac = area / road_area
            if not (self.min_area_frac <= area_frac <= self.max_area_frac):
                continue

            x, y, bw, bh = cv2.boundingRect(contour)
            if bw < 6 or bh < 4:
                continue

            # 1) Пішін: шұңқыр дөңгелекке жақын. Ұзын жіңішке дақ —
            #    көбіне жол таңбалауының көлеңкесі немесе жарық.
            aspect = bw / float(bh)
            if not (0.7 <= aspect <= 3.2):
                continue

            # 2) Тұтастық: көлеңке тарамдалып жатады, шұңқыр тұтас
            hull_area = cv2.contourArea(cv2.convexHull(contour))
            solidity = area / hull_area if hull_area > 0 else 0.0
            if solidity < self.min_solidity:
                continue

            # 3) Күңгірттік: неғұрлым қараңғы болса, соғұрлым сенімді
            blob = np.zeros_like(dark)
            cv2.drawContours(blob, [contour], -1, 255, -1)
            contrast = float(np.mean(difference[blob > 0])) if np.any(blob) else 0.0

            # 4) Жолдың бүкіл енін алып жатса — бұл шұңқыр емес,
            #    көлеңке немесе жарық/күңгірт учаске
            row = min(small.shape[0] - 1, y + bh // 2)
            road_row_width = int(np.count_nonzero(mask[row]))
            if road_row_width > 0 and bw > road_row_width * 0.85:
                continue

            # 5) ЖИЕКТІҢ ӨТКІРЛІГІ — көлеңкені осы жерде кесеміз.
            #    Контурдың дәл үстіндегі градиенттің орташа мәнін аламыз.
            ring = cv2.dilate(blob, np.ones((5, 5), np.uint8)) - \
                cv2.erode(blob, np.ones((5, 5), np.uint8))
            edge_strength = float(np.mean(gradient[ring > 0])) if np.any(ring) else 0.0
            if edge_strength < self.min_edge:
                continue

            confidence = min(
                0.95,
                0.30
                + min(0.28, (contrast - self.darkness) / 60.0)
                + min(0.16, area_frac * 8.0)
                + min(0.10, (solidity - self.min_solidity) * 0.5)
                + min(0.16, (edge_strength - self.min_edge) / 60.0),
            )
            if confidence < self.min_confidence:
                continue

            detections.append(
                Detection(
                    class_key="pothole",
                    confidence=float(confidence),
                    bbox=(
                        int(x / scale), int(y / scale),
                        int((x + bw) / scale), int((y + bh) / scale),
                    ),
                    detector="pothole_cv",
                    extra={
                        "contrast": round(contrast, 1),
                        "solidity": round(solidity, 2),
                        "edge_strength": round(edge_strength, 1),
                        "area_frac_of_road": round(area_frac, 4),
                        "method": "физикалық белгі (күңгірт ойық, өткір жиек)",
                    },
                )
            )

        return detections
