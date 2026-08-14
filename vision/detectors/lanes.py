"""Жол жолағын анықтау.

Екі мақсаты бар:
  1) Мокаптағы көгілдір сызықтарды салу — көрсетілім кезінде интерфейс
     дәл презентациядағыдай көрінуі керек.
  2) ЖОЛ АЙМАҒЫН шектеу — су тасу детекторы бүкіл кадрды емес, тек
     жол бетін қарайды. Бұл жалған детекцияны айтарлықтай азайтады.

Әдісі: Canny + HoughLinesP (классикалық, жеңіл, CPU-да лезде жұмыс істейді).
Кадрлар арасында коэффициенттер тегістеледі (EMA) — сызық дірілдемейді.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

WORK_WIDTH = 640          # есептеу осы енде жүреді, нәтиже кері масштабталады
SMOOTHING = 0.65          # EMA: 0 = тегістеу жоқ, 1 = мүлдем жаңармайды
MAX_MISSED_FRAMES = 2     # сызық жоғалса, ескі геометрия ең көбі осынша кадр сақталады


@dataclass
class LaneResult:
    left: Optional[tuple[tuple[int, int], tuple[int, int]]] = None
    right: Optional[tuple[tuple[int, int], tuple[int, int]]] = None
    center: Optional[tuple[tuple[int, int], tuple[int, int]]] = None
    polygon: Optional[np.ndarray] = None      # жол бетінің көпбұрышы
    horizon_y: int = 0
    confidence: float = 0.0

    # Жолдың НАҚТЫ шекарасы (roadedge.py тапқан қисықтар).
    # Таңбалау жоқ көшелерде интерфейстегі негізгі сызық осы болады.
    left_curve: list = field(default_factory=list)
    right_curve: list = field(default_factory=list)
    road_mask: Optional[np.ndarray] = None
    source: str = "markings"                  # markings | surface | both

    @property
    def found(self) -> bool:
        return (
            self.left is not None
            or self.right is not None
            or len(self.left_curve) >= 2
        )

    @property
    def has_curves(self) -> bool:
        return len(self.left_curve) >= 2 and len(self.right_curve) >= 2


class LaneDetector:
    """Жол жолағын табу және жол аймағын шектеу."""

    name = "lanes"

    def __init__(
        self,
        roi_top: float = 0.55,
        min_slope: float = 0.35,
        max_missed_frames: int = MAX_MISSED_FRAMES,
    ):
        self.roi_top = roi_top          # ROI жоғарғы шегі (кадр биіктігінің үлесі)
        self.min_slope = min_slope      # көлбеуі кемдерін (көлденең сызық) елемеу
        self.max_missed_frames = max(0, int(max_missed_frames))
        self._left_fit: Optional[np.ndarray] = None
        self._right_fit: Optional[np.ndarray] = None
        self._center_fit: Optional[np.ndarray] = None
        self._left_missed = 0
        self._right_missed = 0
        self._center_missed = 0
        self._work_shape: Optional[tuple[int, int]] = None

    def reset(self) -> None:
        """Кадр тізбегі/камера ауысқанда уақытша геометрияны тазарту."""
        self._left_fit = None
        self._right_fit = None
        self._center_fit = None
        self._left_missed = 0
        self._right_missed = 0
        self._center_missed = 0
        self._work_shape = None

    # ---------- көмекші ----------

    @staticmethod
    def _roi_mask(shape, top_frac: float) -> np.ndarray:
        h, w = shape[:2]
        top = int(h * top_frac)
        polygon = np.array(
            [[
                (int(w * 0.02), h),
                (int(w * 0.42), top),
                (int(w * 0.58), top),
                (int(w * 0.98), h),
            ]],
            dtype=np.int32,
        )
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(mask, polygon, 255)
        return mask

    @staticmethod
    def _fit_side(segments: list[tuple[float, float, float, float]]) -> Optional[np.ndarray]:
        """x = a*y + b түрінде сызық жақындату.

        y бойынша жақындатамыз (x емес), себебі жол сызықтары тікке жақын —
        x = f(y) түрінде сандық тұрақтылық әлдеқайда жақсы.
        """
        if len(segments) < 2:
            return None
        xs: list[float] = []
        ys: list[float] = []
        weights: list[float] = []
        for x1, y1, x2, y2 in segments:
            length = float(np.hypot(x2 - x1, y2 - y1))
            xs.extend([x1, x2])
            ys.extend([y1, y2])
            weights.extend([length, length])  # ұзын сегменттің салмағы көбірек
        try:
            return np.polyfit(ys, xs, 1, w=weights)
        except (np.linalg.LinAlgError, ValueError, TypeError):
            return None

    @staticmethod
    def _select_outer_segments(
        segments: list[tuple[float, float, float, float]],
        side: str,
        y_bottom: float,
        frame_width: int,
    ) -> list[tuple[float, float, float, float]]:
        """Ортаңғы таңбаны жол жиегімен араластырмай, сыртқы кластерді таңдау.

        Reference кадрда сол жақ shoulder мен ортаңғы үзік сызықтың екеуі де
        теріс slope береді. Барлығын бір polyfit-ке салсақ, guide ортаңғы
        сызыққа көшеді. Сегменттерді төменгі жиекке экстраполяциялап, айқын
        үлкен бос аралық болса сыртқы кластерді ғана қалдырамыз.
        """
        if len(segments) < 4:
            return segments

        ranked: list[tuple[float, tuple[float, float, float, float]]] = []
        for segment in segments:
            x1, y1, x2, y2 = segment
            if abs(y2 - y1) < 1e-6:
                continue
            a = (x2 - x1) / (y2 - y1)
            bottom_x = a * y_bottom + (x1 - a * y1)
            ranked.append((float(bottom_x), segment))

        if len(ranked) < 4:
            return segments
        ranked.sort(key=lambda item: item[0])
        gaps = [ranked[i + 1][0] - ranked[i][0] for i in range(len(ranked) - 1)]
        split = int(np.argmax(gaps))
        if gaps[split] < frame_width * 0.12:
            return segments

        low = [item[1] for item in ranked[:split + 1]]
        high = [item[1] for item in ranked[split + 1:]]
        selected = low if side == "left" else high
        return selected if len(selected) >= 2 else segments

    def _smooth(
        self,
        previous: Optional[np.ndarray],
        current: Optional[np.ndarray],
        missed: int,
    ) -> tuple[Optional[np.ndarray], int]:
        """EMA және жоғалған сызыққа қысқа TTL.

        Бұрын current=None кезінде previous шексіз сақталатын. Камера бұрылғанда
        немесе жаңа көрініске өткенде ескі guide жолдан тыс қалып қоятын еді.
        """
        if current is None:
            missed += 1
            if missed > self.max_missed_frames:
                return None, missed
            return previous, missed
        if previous is None:
            return current, 0
        return SMOOTHING * previous + (1.0 - SMOOTHING) * current, 0

    # ---------- негізгі ----------

    def detect(self, image: np.ndarray) -> LaneResult:
        h, w = image.shape[:2]
        scale = WORK_WIDTH / float(w)
        small = cv2.resize(image, (WORK_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]

        # Aspect ratio/source өзгерсе, бұрынғы fit жаңа координата жүйесіне жарамайды.
        if self._work_shape is not None and self._work_shape != (sh, sw):
            self.reset()
        self._work_shape = (sh, sw)

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(gray, 60, 160)
        edges = cv2.bitwise_and(edges, self._roi_mask(small.shape, self.roi_top))

        lines = cv2.HoughLinesP(
            edges, rho=2, theta=np.pi / 180, threshold=45,
            minLineLength=int(sh * 0.08), maxLineGap=int(sh * 0.12),
        )

        left_segments: list[tuple[float, float, float, float]] = []
        right_segments: list[tuple[float, float, float, float]] = []
        center_segments: list[tuple[float, float, float, float]] = []

        if lines is not None:
            # OpenCV 4 (N,1,4) және OpenCV 5 (N,4) пішімдерінің екеуін де қолдаймыз
            for line in np.asarray(lines).reshape(-1, 4):
                x1, y1, x2, y2 = (float(v) for v in line)
                if abs(y2 - y1) < 1e-3:
                    continue
                slope = (y2 - y1) / (x2 - x1) if abs(x2 - x1) > 1e-3 else 999.0
                if not (self.min_slope <= abs(slope) <= 12.0):
                    continue          # көлденеңге не тікке тым жақын — жол сызығы емес

                # Ортаңғы жолақты ЕКІ жиынға да қоспаймыз: жолдың ортаңғы
                # үзік сызығы екі жақты да бұрмалап жіберер еді.
                mid_x = (x1 + x2) / 2.0
                fit_a = (x2 - x1) / (y2 - y1)
                bottom_x = fit_a * (sh - 1) + (x1 - fit_a * y1)
                if sw * 0.25 <= bottom_x <= sw * 0.75 and abs(slope) >= 1.20:
                    center_segments.append((x1, y1, x2, y2))
                elif slope < 0 and mid_x < sw * 0.48:
                    left_segments.append((x1, y1, x2, y2))
                elif slope > 0 and mid_x > sw * 0.52:
                    right_segments.append((x1, y1, x2, y2))

        left_segments = self._select_outer_segments(
            left_segments, "left", sh - 1, sw
        )
        right_segments = self._select_outer_segments(
            right_segments, "right", sh - 1, sw
        )

        self._left_fit, self._left_missed = self._smooth(
            self._left_fit, self._fit_side(left_segments), self._left_missed
        )
        self._right_fit, self._right_missed = self._smooth(
            self._right_fit, self._fit_side(right_segments), self._right_missed
        )
        self._center_fit, self._center_missed = self._smooth(
            self._center_fit, self._fit_side(center_segments), self._center_missed
        )

        return self._build_result(
            self._left_fit, self._right_fit, self._center_fit,
            sh, sw, scale, (h, w),
        )

    def _build_result(
        self, left_fit, right_fit, center_fit, sh, sw, scale, full_shape
    ) -> LaneResult:
        h, w = full_shape
        y_bottom = max(0, sh - 1)
        y_top = max(0, min(sh - 1, int(sh * self.roi_top)))

        # Екі сызық қиылысатын нүкте (шексіздік нүктесі) — көкжиекті нақтылайды
        if left_fit is not None and right_fit is not None:
            denominator = left_fit[0] - right_fit[0]
            if abs(denominator) > 1e-6:
                y_cross = (right_fit[1] - left_fit[1]) / denominator
                if sh * 0.30 < y_cross < sh * 0.95:
                    y_top = int(y_cross) + 4

        def point_at(fit, y: float) -> tuple[int, int]:
            x = fit[0] * y + fit[1]
            # Hough сызығы кадрдан тыс қиылысуы мүмкін. OpenCV оны үнсіз
            # clip етеді, бірақ polygon пен маска қате болып қалады — сондықтан
            # координатаны осы жерде нақты шектейміз.
            px = int(round(x / scale))
            py = int(round(y / scale))
            return (
                max(0, min(w - 1, px)),
                max(0, min(h - 1, py)),
            )

        left = right = center = None
        polygon = None

        if left_fit is not None:
            left = (point_at(left_fit, y_bottom), point_at(left_fit, y_top))
        if right_fit is not None:
            right = (point_at(right_fit, y_bottom), point_at(right_fit, y_top))

        # Геометриялық тексеру: жол жолақтары ЖОҒАРЫ қарай ЖИНАЛУЫ керек
        # (перспектива). Бұл шартты бұзған нәтиже — қате жақындату,
        # оны көрсетуден гөрі мүлдем көрсетпеген дұрыс.
        if left and right:
            bottom_gap = right[0][0] - left[0][0]
            top_gap = right[1][0] - left[1][0]
            top_mid = (left[1][0] + right[1][0]) / 2.0
            geometry_ok = (
                bottom_gap >= w * 0.12
                and 0 <= top_gap < bottom_gap * 0.60
                and w * 0.12 <= top_mid <= w * 0.88
                and left[0][0] < w * 0.78
                and right[0][0] > w * 0.22
            )
            if not geometry_ok:
                self._left_fit = self._right_fit = self._center_fit = None
                self._left_missed = self._right_missed = 0
                self._center_missed = 0
                left = right = None

        if left and right:
            if center_fit is not None:
                detected_center = (
                    point_at(center_fit, y_bottom),
                    point_at(center_fit, y_top),
                )
                center_inside = (
                    left[0][0] <= detected_center[0][0] <= right[0][0]
                    and left[1][0] <= detected_center[1][0] <= right[1][0]
                )
                center = detected_center if center_inside else None
            if center is None:
                center = (
                    ((left[0][0] + right[0][0]) // 2, (left[0][1] + right[0][1]) // 2),
                    ((left[1][0] + right[1][0]) // 2, (left[1][1] + right[1][1]) // 2),
                )
            polygon = np.array(
                [left[0], left[1], right[1], right[0]], dtype=np.int32
            )

            # Геометрия мен табылған сегменттердің жаңалығына негізделген
            # қарапайым confidence. HUD кейін төмен сенімді guide-ты бәсеңдете алады.
            convergence = 1.0 - (top_gap / float(max(1, bottom_gap)))
            freshness = 1.0 - 0.20 * max(self._left_missed, self._right_missed)
            confidence = max(0.0, min(1.0, convergence * freshness))
        else:
            confidence = 0.0

        return LaneResult(
            left=left, right=right, center=center,
            polygon=polygon,
            horizon_y=max(0, min(h - 1, int(y_top / scale))),
            confidence=confidence,
        )

    # ---------- жол аймағы ----------

    @staticmethod
    def road_mask(shape, lane: Optional[LaneResult]) -> np.ndarray:
        """Су тасу детекторы үшін жол бетінің маскасы.

        Жолақ табылса — соның көпбұрышы; табылмаса — кадрдың төменгі
        бөлігіндегі стандартты трапеция.
        """
        h, w = shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)

        if lane is not None and lane.polygon is not None:
            cv2.fillPoly(mask, [lane.polygon], 255)
            # Жолақтың сыртындағы жиекті де қосу үшін сәл кеңейтеміз
            mask = cv2.dilate(mask, np.ones((31, 31), np.uint8), iterations=1)
            return mask

        fallback = np.array(
            [[
                (int(w * 0.05), h),
                (int(w * 0.38), int(h * 0.58)),
                (int(w * 0.62), int(h * 0.58)),
                (int(w * 0.95), h),
            ]],
            dtype=np.int32,
        )
        cv2.fillPoly(mask, fallback, 255)
        return mask
