"""Жолдың ШЕКАРАСЫН анықтау (таңбалау болмаса да).

НЕГЕ БҰЛ КЕРЕК
--------------
`lanes.py` боялған жол сызықтарын іздейді (Hough). Бірақ Шымкенттің көп
көшесінде таңбалау мүлдем жоқ немесе өшіп қалған — ондай жерде ол детектор
ештеңе таппайды.

Ал бізге жолдың ШЕКАРАСЫ керек: асфальт қайда бітеді, шөп/жиек қайдан
басталады. Себебі:
  1. Интерфейсте жолдың шекарасы көрініп тұруы керек (мокаптағыдай)
  2. Ақауды тек ЖОЛ бетінен іздеу керек — шөптегі дақ, тротуардағы
     жарық өтінім болып кетпеуі тиіс

ҚАЛАЙ ЖҰМЫС ІСТЕЙДІ (үйретілген модель қажет емес)
--------------------------------------------------
1. Кадрдың төменгі ортасы — көліктің дәл алдындағы жер. Ол ЕҢ АЛДЫМЕН
   жол болып табылады (басқа не болуы мүмкін?). Осыны «тұқым» (seed)
   ретінде аламыз.
2. Сол тұқымнан бастап түсі мен жарықтығы ұқсас пиксельдерге қарай
   аймақты өсіреміз (flood fill). Асфальт біртекті болғандықтан,
   аймақ жолдың бүкіл бетін қамтиды да, шөпке/жиекке жеткенде тоқтайды.
3. Әр көлденең жолдағы аймақтың сол және оң шетін жинаймыз — бұл
   жолдың шекарасы.
4. Шекараға қисық (2-дәрежелі полином) жақындатамыз: жол бұрылса да
   сызық соны қайталайды.

flood fill таңдалғанының себебі: ол жарықтың біртіндеп өзгеруіне
(көлеңке, күн шағылысуы) төзімді — тұрақты табалдырықтан әлдеқайда сенімді.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)

WORK_WIDTH = 480
SMOOTHING = 0.6          # кадрлар арасындағы тегістеу (діріл болмасын)


@dataclass
class RoadBoundary:
    """Табылған жол беті мен оның шекаралары."""

    left: list[tuple[int, int]] = field(default_factory=list)    # сол шекара нүктелері
    right: list[tuple[int, int]] = field(default_factory=list)   # оң шекара
    mask: Optional[np.ndarray] = None                            # жол бетінің маскасы
    horizon_y: int = 0
    coverage: float = 0.0    # жол бетінің ROI-дағы үлесі (сапа көрсеткіші)

    @property
    def found(self) -> bool:
        return len(self.left) >= 2 and len(self.right) >= 2

    @property
    def polygon(self) -> Optional[np.ndarray]:
        if not self.found:
            return None
        return np.array(self.left + self.right[::-1], dtype=np.int32)


class RoadBoundaryDetector:
    name = "roadedge"

    def __init__(
        self,
        flood_tolerance: int = 14,      # түс айырмасының шегі (flood fill)
        min_coverage: float = 0.10,     # осыдан аз болса — жол табылмады деп есептейміз
        max_coverage: float = 0.92,     # тым көп болса — бүкіл кадр «жол» болып кеткен
        min_texture: float = 1.1,       # мүлдем тегіс бет (капот/әйнек) шегі
    ):
        self.flood_tolerance = flood_tolerance
        self.min_coverage = min_coverage
        self.max_coverage = max_coverage
        self.min_texture = min_texture
        self._left_fit: Optional[np.ndarray] = None
        self._right_fit: Optional[np.ndarray] = None
        self._prev_gray: Optional[np.ndarray] = None
        self._hood_top: Optional[int] = None      # капоттың жоғарғы шекарасы

    # ---------- капотты автоматты табу ----------

    def _update_hood(self, gray: np.ndarray) -> None:
        """Кадрдың төменіндегі ҚОЗҒАЛМАЙТЫН жолақ — көліктің капоты.

        Бұл — капотты жолдан ажыратудың ең сенімді белгісі. Асфальт
        көлік жүргенде үнемі жаңарып отырады, ал капот бір орында
        қатып тұрады: кадрлар айырмасы нөлге жақын.

        Пайдаланушыдан `--crop-bottom` мәнін дұрыс табуды талап
        етпейміз — жүйе өзі анықтайды.
        """
        previous = self._prev_gray
        self._prev_gray = gray

        if previous is None or previous.shape != gray.shape:
            return

        height = gray.shape[0]
        diff = cv2.absdiff(gray, previous)
        row_motion = diff.mean(axis=1)

        # Төменнен жоғары қарай: қозғалыс жоқ жолақ қай жерде бітеді?
        limit = int(height * 0.45)      # капот кадрдың жартысынан аспайды
        hood_top = height
        for y in range(height - 1, limit, -1):
            if row_motion[y] > 1.6:     # қозғалыс басталды — бұл жол
                break
            hood_top = y

        block = height - hood_top
        if block < height * 0.06:
            self._hood_top = None       # капот кадрға түспейді
            return

        # Тегістеу: бір кадрдағы кездейсоқ мәнге сүйенбейміз
        if self._hood_top is None:
            self._hood_top = hood_top
        else:
            self._hood_top = int(0.8 * self._hood_top + 0.2 * hood_top)

    # ---------- жол бетін табу ----------

    def _road_mask(self, small: np.ndarray) -> tuple[Optional[np.ndarray], float]:
        height, width = small.shape[:2]

        # Капот табылған болса, оның үстіндегі бөлікпен ғана жұмыс істейміз
        limit = self._hood_top if self._hood_top else height
        limit = max(int(height * 0.45), min(height, int(limit)))

        # Жарық ауытқуын азайту үшін бұлдырлатып, LAB кеңістігіне ауысамыз:
        # LAB-та жарықтық (L) пен түс (a, b) бөлек, сондықтан көлеңке
        # түстің өзін бұрмаламайды.
        blurred = cv2.GaussianBlur(small, (7, 7), 0)
        lab = cv2.cvtColor(blurred, cv2.COLOR_BGR2LAB)

        # flood fill үшін маска кадрдан 2 пиксельге үлкен болуы керек
        mask = np.zeros((height + 2, width + 2), np.uint8)

        # Тұқым нүктелері — көліктің алдындағы жол.
        #
        # МАҢЫЗДЫ: кадрдың ЕҢ ТӨМЕНГІ жолақтарын алуға болмайды — онда
        # әдетте көліктің КАПОТЫ тұрады. Капот та біртекті әрі күңгірт
        # болғандықтан, flood fill оны «жол» деп қабылдап, шекараны
        # капоттың үстіне салып қояды. Сондықтан сәл жоғарырақ бастаймыз.
        # Кең жолақтан үміткер нүктелер жинаймыз: капот қаншалық биік
        # тұрса да, жоғарырақтағы нүктелер жолға түседі.
        candidates = [
            (int(width * fx), int(limit * fy))
            for fy in (0.99, 0.94, 0.88, 0.82, 0.75, 0.68)
            for fx in (0.38, 0.46, 0.54, 0.62)
        ]

        # АСФАЛЬТ ПА, КАПОТ ПА?
        # Капот пен әйнек ТЕГІС — түйіршігі жоқ. Асфальт әрқашан
        # түйіршікті. Сондықтан тұқым нүктесінің айналасындағы
        # текстураны өлшеп, тегіс жерлерді тұқым ретінде алмаймыз.
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        texture = cv2.blur(np.abs(cv2.Laplacian(gray, cv2.CV_32F)), (25, 25))

        scored = [
            (float(texture[y, x]), (x, y))
            for (x, y) in candidates
            if 0 <= y < height and 0 <= x < width
        ]
        if not scored:
            return None, 0.0

        # Ең түйіршікті нүктелерді таңдаймыз. Бекітілген табалдырықтың
        # орнына САЛЫСТЫРМАЛЫ шек: видеоның сапасы әртүрлі болуы мүмкін,
        # ал капот пен асфальттың АЙЫРМАСЫ әрқашан сақталады.
        scored.sort(reverse=True, key=lambda item: item[0])
        best_texture = scored[0][0]
        if best_texture < self.min_texture:
            log.debug("Жол табылмады: бүкіл аймақ тегіс (капот/әйнек)")
            return None, 0.0

        floor = max(self.min_texture, best_texture * 0.55)
        seeds = [point for score, point in scored if score >= floor][:6]

        tolerance = (self.flood_tolerance,) * 3
        filled_total = 0
        for seed in seeds:
            if mask[seed[1] + 1, seed[0] + 1]:
                continue          # бұл нүкте бұрынғы аймаққа кірген
            try:
                filled, _, _, _ = cv2.floodFill(
                    lab.copy(), mask, seed, 255,
                    tolerance, tolerance,
                    cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8),
                )
                filled_total += filled
            except cv2.error:
                continue

        road = mask[1:-1, 1:-1]
        if limit < height:
            road[limit:, :] = 0        # капот аймағын алып тастаймыз

        # Ұсақ тесіктерді жабамыз (таңбалау сызығы, дақ, көлеңке)
        kernel = np.ones((9, 9), np.uint8)
        road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, kernel, iterations=2)
        road = cv2.morphologyEx(road, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

        # Ең үлкен байланысты аймақты ғана қалдырамыз
        count, labels, stats, _ = cv2.connectedComponentsWithStats(road, connectivity=8)
        if count <= 1:
            return None, 0.0
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        road = np.where(labels == largest, 255, 0).astype(np.uint8)

        coverage = float(cv2.countNonZero(road)) / float(height * width)

        # ПЕРСПЕКТИВА ТЕКСЕРІСІ: нағыз жол алысқа қарай ТАРАЯДЫ.
        # Егер табылған аймақ жоғарыда кеңейіп кетсе, ол жол емес —
        # көбіне капот, көлеңке немесе аспан. Ондайды қабылдамаймыз.
        rows = np.flatnonzero(road.any(axis=1))
        if rows.size >= 10:
            top, bottom = int(rows[0]), int(rows[-1])
            span = max(1, bottom - top)
            upper_band = road[top:top + max(1, span // 4)]
            lower_band = road[bottom - max(1, span // 4):bottom]
            upper_width = float(np.count_nonzero(upper_band)) / max(1, upper_band.shape[0])
            lower_width = float(np.count_nonzero(lower_band)) / max(1, lower_band.shape[0])
            # Нағыз жол перспективамен АЙТАРЛЫҚТАЙ тараяды. Капот пен
            # әйнек тарамайды — солармен ажырататын басты белгі осы.
            if upper_width > lower_width * 1.15:
                log.debug(
                    "Жол емес: аймақ тараймайды (жоғарғы %.0f px, төменгі %.0f px)",
                    upper_width, lower_width,
                )
                return None, coverage

        return road, coverage

    # ---------- шекараны шығару ----------

    @staticmethod
    def _edges_from_mask(road: np.ndarray) -> tuple[list, list, int]:
        """Әр жолдағы аймақтың сол/оң шетін жинау."""
        height, width = road.shape[:2]
        left_points: list[tuple[float, float]] = []
        right_points: list[tuple[float, float]] = []
        # Жиекке тірелген жолдар — қосалқы қор: негізгісі жетпей қалса
        # ғана қолданамыз (мүлдем сызбағаннан гөрі дөрекілеу сызық артық)
        left_edge_fallback: list[tuple[float, float]] = []
        right_edge_fallback: list[tuple[float, float]] = []
        top_y = height

        # Төменнен жоғары қарай жүреміз, әр 4-ші жолды аламыз
        for y in range(height - 2, int(height * 0.35), -4):
            xs = np.flatnonzero(road[y])
            if xs.size < width * 0.06:
                continue                     # бұл жолда жол беті тым тар

            # МАҢЫЗДЫ: аймақ кадрдың жиегіне тірелген болса, ол ЖОЛДЫҢ
            # шекарасы емес — жай ғана кадрдың шеті. Ондай жолды
            # елемейміз, әйтпесе қисық төменде сыртқа қарай иіліп,
            # жол сопақ болып көрінеді.
            if xs[0] > 2:
                left_points.append((float(xs[0]), float(y)))
            else:
                left_edge_fallback.append((float(xs[0]), float(y)))

            if xs[-1] < width - 3:
                right_points.append((float(xs[-1]), float(y)))
            else:
                right_edge_fallback.append((float(xs[-1]), float(y)))

            top_y = min(top_y, y)

        if len(left_points) < 6:
            left_points = (left_points + left_edge_fallback)
        if len(right_points) < 6:
            right_points = (right_points + right_edge_fallback)

        left_points.sort(key=lambda p: -p[1])
        right_points.sort(key=lambda p: -p[1])
        return left_points, right_points, top_y

    @staticmethod
    def _fit_curve(points: list[tuple[float, float]]) -> Optional[np.ndarray]:
        """x = a*y² + b*y + c түріндегі қисық (жол бұрылысын да қайталайды).

        Шеткі мәндерді (шу) алып тастау үшін екі рет жақындатамыз.
        """
        if len(points) < 6:
            return None

        xs = np.array([p[0] for p in points], dtype=np.float64)
        ys = np.array([p[1] for p in points], dtype=np.float64)

        try:
            fit = np.polyfit(ys, xs, 2)
            residuals = np.abs(np.polyval(fit, ys) - xs)
            keep = residuals < max(6.0, float(np.std(residuals)) * 2.0)
            if keep.sum() >= 6:
                fit = np.polyfit(ys[keep], xs[keep], 2)
            return fit
        except (np.linalg.LinAlgError, ValueError, TypeError):
            return None

    def _smooth(self, previous, current):
        if current is None:
            return previous
        if previous is None:
            return current
        return SMOOTHING * previous + (1.0 - SMOOTHING) * current

    # ---------- негізгі ----------

    def detect(self, image: np.ndarray) -> RoadBoundary:
        height, width = image.shape[:2]
        scale = WORK_WIDTH / float(width)
        small = cv2.resize(image, (WORK_WIDTH, int(height * scale)),
                           interpolation=cv2.INTER_AREA)

        # Капотты кадрлар айырмасы бойынша анықтаймыз (қозғалмайтын жолақ)
        self._update_hood(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))

        road, coverage = self._road_mask(small)
        if road is None or not (self.min_coverage <= coverage <= self.max_coverage):
            return RoadBoundary(coverage=coverage)

        left_points, right_points, top_y = self._edges_from_mask(road)

        self._left_fit = self._smooth(self._left_fit, self._fit_curve(left_points))
        self._right_fit = self._smooth(self._right_fit, self._fit_curve(right_points))

        if self._left_fit is None or self._right_fit is None:
            return RoadBoundary(coverage=coverage)

        sh = small.shape[0]
        y_bottom = sh - 2
        y_top = max(top_y, int(sh * 0.38))

        left_curve: list[tuple[int, int]] = []
        right_curve: list[tuple[int, int]] = []

        for i in range(15):
            y = y_bottom + (y_top - y_bottom) * (i / 14.0)
            lx = float(np.polyval(self._left_fit, y))
            rx = float(np.polyval(self._right_fit, y))
            if rx - lx < WORK_WIDTH * 0.04:
                break                        # шекаралар қиылысты — әрі қарай мәнсіз
            left_curve.append((int(lx / scale), int(y / scale)))
            right_curve.append((int(rx / scale), int(y / scale)))

        if len(left_curve) < 3:
            return RoadBoundary(coverage=coverage)

        # Маскаға толық өлшемге келтіреміз — су тасу детекторы қолданады
        full_mask = cv2.resize(road, (width, height), interpolation=cv2.INTER_NEAREST)

        return RoadBoundary(
            left=left_curve,
            right=right_curve,
            mask=full_mask,
            horizon_y=int(y_top / scale),
            coverage=coverage,
        )
