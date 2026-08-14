"""Көрсетілім интерфейсі (HUD) — презентациядағы мокапқа дәл сәйкес.

Неге бұл маңызды: жюри көретін бірінші нәрсе — экран. Мокапта көрсеткен
көрініс (жоғарғы қара жолақ, көгілдір жол сызықтары, нөмірленген
тіктөртбұрыштар) нақты программада да дәл солай болуы керек.

Техникалық ескерту: OpenCV-дің putText функциясы кириллицаны да,
қазақ әріптерін де (ә, қ, ғ, ң, ө, ұ, ү, і) сала алмайды — сұрақ белгісі
шығады. Сондықтан бүкіл мәтін Pillow арқылы TTF шрифтпен салынады.
Жылдамдық үшін бір кадрдағы барлық жазу жиналып, БІР рет қана
BGR -> PIL -> BGR түрлендіруі жасалады.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import categories
from .config import Config
from .detectors import Detection, LaneResult
from .document import SHYMKENT_TZ
from .geo import format_coord
from .gps import GpsFix

log = logging.getLogger(__name__)

# --- Мокаптағы түстер (BGR) ---
CYAN = (255, 229, 0)          # жол сызықтары  #00E5FF
BAR_BG = (26, 24, 24)         # жоғарғы жолақ  #18181A
REC_RED = (48, 59, 255)       # REC нүктесі    #FF3B30
ALERT_RED = (31, 59, 255)     # #FF3B1F — ескерту баннері (ақаумен бір түс)
WHITE = (255, 255, 255)
GREY = (170, 170, 170)
PANEL_BG = (24, 22, 22)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
]
FONT_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
]


# ============================================================
#  Мәтін қабаты
# ============================================================

@dataclass
class _TextItem:
    xy: tuple[int, int]
    text: str
    size: int
    color: tuple
    bold: bool = False
    anchor: str = "la"          # Pillow anchor: la=солдан-жоғары, mm=орталық


class TextLayer:
    """Кадрдағы барлық жазуды жинап, бір рет салады."""

    _font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def __init__(self):
        self._items: list[_TextItem] = []

    @classmethod
    def _font(cls, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = ("b" if bold else "r", size)
        if key in cls._font_cache:
            return cls._font_cache[key]

        candidates = FONT_BOLD_CANDIDATES if bold else FONT_CANDIDATES
        font = None
        for path in candidates:
            if Path(path).exists():
                try:
                    font = ImageFont.truetype(path, size)
                    break
                except Exception:
                    continue
        if font is None:
            log.warning("TTF шрифт табылмады — қазақ әріптері дұрыс шықпауы мүмкін.")
            font = ImageFont.load_default()

        cls._font_cache[key] = font
        return font

    def add(self, xy, text, size=16, color=WHITE, bold=False, anchor="la") -> None:
        self._items.append(_TextItem(xy, str(text), size, color, bold, anchor))

    def measure(self, text: str, size: int, bold: bool = False) -> tuple[int, int]:
        font = self._font(size, bold)
        box = font.getbbox(str(text))
        return (box[2] - box[0], box[3] - box[1])

    def flush(self, image: np.ndarray) -> np.ndarray:
        if not self._items:
            return image

        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil)
        for item in self._items:
            font = self._font(item.size, item.bold)
            rgb = (item.color[2], item.color[1], item.color[0])   # BGR -> RGB
            try:
                draw.text(item.xy, item.text, font=font, fill=rgb, anchor=item.anchor)
            except Exception:
                draw.text(item.xy, item.text, font=font, fill=rgb)

        self._items.clear()
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


# ============================================================
#  HUD
# ============================================================

def rounded_rect(
    image: np.ndarray,
    pt1: tuple[int, int],
    pt2: tuple[int, int],
    color: tuple,
    radius: int = 8,
    thickness: int = -1,
) -> None:
    """Дөңгелектелген бұрышы бар тіктөртбұрыш (OpenCV-де дайыны жоқ)."""
    x1, y1 = pt1
    x2, y2 = pt2
    radius = max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))

    if radius == 0:
        cv2.rectangle(image, pt1, pt2, color, thickness, cv2.LINE_AA)
        return

    if thickness < 0:
        cv2.rectangle(image, (x1 + radius, y1), (x2 - radius, y2), color, -1, cv2.LINE_AA)
        cv2.rectangle(image, (x1, y1 + radius), (x2, y2 - radius), color, -1, cv2.LINE_AA)
        for cx, cy in ((x1 + radius, y1 + radius), (x2 - radius, y1 + radius),
                       (x1 + radius, y2 - radius), (x2 - radius, y2 - radius)):
            cv2.circle(image, (cx, cy), radius, color, -1, cv2.LINE_AA)
        return

    cv2.line(image, (x1 + radius, y1), (x2 - radius, y1), color, thickness, cv2.LINE_AA)
    cv2.line(image, (x1 + radius, y2), (x2 - radius, y2), color, thickness, cv2.LINE_AA)
    cv2.line(image, (x1, y1 + radius), (x1, y2 - radius), color, thickness, cv2.LINE_AA)
    cv2.line(image, (x2, y1 + radius), (x2, y2 - radius), color, thickness, cv2.LINE_AA)
    for (cx, cy), angle in (
        ((x1 + radius, y1 + radius), 180), ((x2 - radius, y1 + radius), 270),
        ((x2 - radius, y2 - radius), 0), ((x1 + radius, y2 - radius), 90),
    ):
        cv2.ellipse(image, (cx, cy), (radius, radius), angle, 0, 90,
                    color, thickness, cv2.LINE_AA)


def add_glow(image: np.ndarray, mask_layer: np.ndarray, strength: float = 1.0,
             blur: int = 21) -> None:
    """Неон жарқырауы: қара қабатты бұлдырлатып, кадрға ҚОСАМЫЗ.

    addWeighted араластыруы суретті күңгірттендіреді, ал cv2.add тек
    жарықтандырады — мокаптағы неон эффектісі дәл осылай шығады.
    """
    blurred = cv2.GaussianBlur(mask_layer, (0, 0), sigmaX=blur, sigmaY=blur)
    if strength != 1.0:
        blurred = cv2.convertScaleAbs(blurred, alpha=strength, beta=0)
    cv2.add(image, blurred, dst=image)


@dataclass
class HudStats:
    fps: float = 0.0
    detections_total: int = 0
    documents_sent: int = 0
    queued: int = 0
    active_recordings: int = 0
    is_night: bool = False
    timings_ms: dict = field(default_factory=dict)
    portal_online: bool = False
    gps_ok: bool = False


class Hud:
    """Мокап стиліндегі көрсетілім қабаты."""

    # Дизайн мокабы осы өлшемде сызылған — барлық элемент соған
    # пропорционалды масштабталады, сондықтан 720p-де де, 1080p-де де
    # интерфейс бірдей көрінеді.
    REFERENCE_DIAGONAL = 1795.0     # sqrt(1122² + 1402²)

    def __init__(self, cfg: Config):
        self.cfg = cfg
        # Эталон интерфейс таза: қызметтік панель тек оператор d басқанда ашылады.
        self.show_debug = False
        # Live preview-де reference суреттеріндегідей тек тұрақты нөмір қалады.
        # Evidence режимі мұны True етіп, санат пен confidence-ті де көрсетеді.
        self.show_captions = False

    @classmethod
    def _scale(cls, image: np.ndarray) -> float:
        height, width = image.shape[:2]
        diagonal = float(np.hypot(width, height))
        return max(0.55, min(2.2, diagonal / cls.REFERENCE_DIAGONAL))

    def _bar_height(self, image: np.ndarray) -> int:
        return max(26, int(46 * self._scale(image)))

    # ---------- жол сызықтары ----------

    def _draw_lanes(self, image: np.ndarray, lane: Optional[LaneResult]) -> None:
        """Мокаптағыдай неон жол сызықтары: жарқырау + бойындағы түйіндер.

        Тек ЕКІ жақ та табылғанда саламыз (polygon сол кезде ғана құрылады).
        Бір ғана сызық табылса, ол көбіне жалған болады — әйнектегі
        шағылысу, панельдің жиегі, көлеңке. Ондай сызық суретті бүлдіреді.
        """
        if lane is None:
            return

        height, width = image.shape[:2]
        k = self._scale(image)

        # ЖОЛДЫҢ ШЕКАРАСЫ табылса — негізгі сызық сол болады.
        # Ол таңбалауға тәуелді емес, әрі жол бұрылса қисық та бұрылады.
        if lane.has_curves:
            self._draw_road_edges(image, lane, k)
            return

        if lane.polygon is None:
            return

        core_w = max(2, int(5 * k))          # сызықтың өзі
        halo_w = max(6, int(16 * k))         # жарқырау қалыңдығы
        node = max(3, int(7 * k))            # түйін шаршысының жартысы

        # 1) Жарқырау қабаты — қара фонға сызып, бұлдырлатып қосамыз
        glow = np.zeros_like(image)

        def stroke(layer, pts, thickness, color=CYAN):
            cv2.line(layer, pts[0], pts[1], color, thickness, cv2.LINE_AA)

        for side in (lane.left, lane.right):
            if side:
                stroke(glow, side, halo_w)

        center_segments: list[tuple[tuple[int, int], tuple[int, int], int]] = []
        if lane.center:
            (cx1, cy1), (cx2, cy2) = lane.center
            steps = 16
            for i in range(steps):
                if i % 2:
                    continue
                t0, t1 = i / steps, (i + 0.72) / steps
                p0 = (int(cx1 + (cx2 - cx1) * t0), int(cy1 + (cy2 - cy1) * t0))
                p1 = (int(cx1 + (cx2 - cx1) * t1), int(cy1 + (cy2 - cy1) * t1))
                # Перспектива: жақындағы сегмент жуан, алыстағысы жіңішке
                seg_w = max(2, int(core_w * 2.4 * (1.0 - t0 * 0.78)))
                center_segments.append((p0, p1, seg_w))
                cv2.line(glow, p0, p1, CYAN, int(seg_w * 2.6), cv2.LINE_AA)

        add_glow(image, glow, strength=0.95, blur=max(9, int(17 * k)))

        # 2) Сызықтың өзі — анық, жарқыраудың үстінен
        for side in (lane.left, lane.right):
            if side:
                cv2.line(image, side[0], side[1], CYAN, core_w, cv2.LINE_AA)

        for p0, p1, seg_w in center_segments:
            cv2.line(image, p0, p1, CYAN, seg_w, cv2.LINE_AA)
            cv2.line(image, p0, p1, (255, 255, 255), max(1, seg_w // 3), cv2.LINE_AA)

        # 3) Сызық бойындағы шаршы түйіндер (мокаптағы ұсақ шаршылар).
        #    Жақындағысы үлкен, көкжиекке қарай кішірейеді.
        for side in (lane.left, lane.right):
            if not side:
                continue
            (sx, sy), (ex, ey) = side
            for t in (0.16, 0.42, 0.66, 0.84):
                px = int(sx + (ex - sx) * t)
                py = int(sy + (ey - sy) * t)
                if not (0 <= px < width and 0 <= py < height):
                    continue
                size = max(2, int(node * (1.0 - t * 0.72)))
                cv2.rectangle(image, (px - size, py - size), (px + size, py + size),
                              CYAN, -1, cv2.LINE_AA)
                cv2.rectangle(image, (px - size, py - size), (px + size, py + size),
                              (255, 255, 255), max(1, size // 3), cv2.LINE_AA)

    def _draw_road_edges(self, image: np.ndarray, lane: LaneResult, k: float) -> None:
        """Жолдың шекарасын неон қисықпен салу + бетін жеңіл бояу."""
        left = np.array(lane.left_curve, dtype=np.int32)
        right = np.array(lane.right_curve, dtype=np.int32)
        core_w = max(2, int(5 * k))
        halo_w = max(6, int(15 * k))

        # 1) Жол бетіне АЗҒАНТАЙ көгілдір реңк — «жүйе жолды көріп тұр»
        #    деген сезім береді, бірақ асфальттың өз түсін жаппайды.
        #    Мәні әдейі кішкентай: бұрын күштірек еді, жол су басқандай
        #    көрінетін.
        surface = np.zeros_like(image)
        polygon = np.vstack([left, right[::-1]])
        cv2.fillPoly(surface, [polygon], (18, 12, 0))
        cv2.add(image, surface, dst=image)

        # 2) Жарқырау қабаты
        glow = np.zeros_like(image)
        cv2.polylines(glow, [left, right], False, CYAN, halo_w, cv2.LINE_AA)
        add_glow(image, glow, strength=0.9, blur=max(9, int(15 * k)))

        # 3) Шекараның өзі
        cv2.polylines(image, [left, right], False, CYAN, core_w, cv2.LINE_AA)

        # 4) Шекара бойындағы шаршы түйіндер (мокаптағыдай), перспективамен кішірейеді
        node = max(3, int(7 * k))
        count = len(left)
        for index in range(0, count, max(1, count // 5)):
            t = index / max(1, count - 1)
            size = max(2, int(node * (1.0 - t * 0.72)))
            for point in (left[index], right[index]):
                x, y = int(point[0]), int(point[1])
                cv2.rectangle(image, (x - size, y - size), (x + size, y + size),
                              CYAN, -1, cv2.LINE_AA)
                cv2.rectangle(image, (x - size, y - size), (x + size, y + size),
                              (255, 255, 255), max(1, size // 3), cv2.LINE_AA)

        # 5) Таңбалау да табылса — ортаңғы үзік сызықты қосамыз
        if lane.center:
            (cx1, cy1), (cx2, cy2) = lane.center
            for i in range(0, 14, 2):
                t0, t1 = (i / 14.0) ** 0.72, ((i + 0.75) / 14.0) ** 0.72
                p0 = (int(cx1 + (cx2 - cx1) * t0), int(cy1 + (cy2 - cy1) * t0))
                p1 = (int(cx1 + (cx2 - cx1) * t1), int(cy1 + (cy2 - cy1) * t1))
                seg_w = max(2, int(core_w * 2.0 * (1.0 - t0 * 0.75)))
                cv2.line(image, p0, p1, CYAN, seg_w, cv2.LINE_AA)

    # ---------- детекция тіктөртбұрыштары ----------

    def _draw_detections(
        self, image: np.ndarray, detections: list[Detection], text: TextLayer
    ) -> None:
        if not detections:
            return

        height, width = image.shape[:2]
        k = self._scale(image)

        # Мокаптағыдай: ең жақыны (кадрда ең төмені) — 01 нөмірі
        ordered = sorted(detections, key=lambda d: -d.bbox[3])

        # 1) Ішкі бояу — санатқа қарай. Жол ақауы reference-тегідей жеңіл
        # қызғылт қабат алады, ал жанбай тұрған фонарьдың bbox-ы бос қалады.
        for det in ordered:
            x1, y1, x2, y2 = det.bbox
            x1, x2 = max(0, min(width - 1, x1)), max(0, min(width, x2))
            y1, y2 = max(0, min(height - 1, y1)), max(0, min(height, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            if det.class_key == "streetlight_out":
                alpha = 0.0
            elif det.class_key == "obstruction":
                alpha = 0.13
            else:
                alpha = 0.19
            if alpha <= 0:
                continue
            roi = image[y1:y2, x1:x2]
            tint = np.full_like(roi, categories.get(det.class_key).color, dtype=np.uint8)
            cv2.addWeighted(tint, alpha, roi, 1.0 - alpha, 0, dst=roi)

        # 2) Жиектің айналасында жеңіл жарқырау — тіктөртбұрыш кадрдан
        #    «көтеріліп» тұрғандай көрінеді
        glow = np.zeros_like(image)
        for det in ordered:
            x1, y1, x2, y2 = det.bbox
            x1, x2 = max(0, min(width - 1, x1)), max(0, min(width - 1, x2))
            y1, y2 = max(0, min(height - 1, y1)), max(0, min(height - 1, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            cv2.rectangle(glow, (x1, y1), (x2, y2),
                          categories.get(det.class_key).color,
                          max(3, int(7 * k)), cv2.LINE_AA)
        add_glow(image, glow, strength=0.55, blur=max(7, int(13 * k)))

        border = max(2, int(4 * k))
        chip_w, chip_h = int(46 * k), int(30 * k)
        chip_font = max(11, int(17 * k))
        caption_font = max(10, int(14 * k))
        radius = max(2, int(4 * k))

        for index, det in enumerate(ordered, start=1):
            x1, y1, x2, y2 = det.bbox
            x1, x2 = max(0, min(width - 1, x1)), max(0, min(width - 1, x2))
            y1, y2 = max(0, min(height - 1, y1)), max(0, min(height - 1, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            category = categories.get(det.class_key)
            color = category.color

            cv2.rectangle(image, (x1, y1), (x2, y2), color, border, cv2.LINE_AA)

            # Нөмір чипі (мокаптағы «01», «02») — толық боялған
            label = f"{index:02d}"

            # Тіктөртбұрыш кадрдың жоғарғы жиегіне тірелсе, чипті ішіне саламыз
            cy1 = y1 - chip_h if y1 >= chip_h else y1
            cx1 = max(0, min(x1, width - chip_w))

            rounded_rect(image, (cx1, cy1), (cx1 + chip_w, cy1 + chip_h),
                         color, radius=radius, thickness=-1)
            text.add((cx1 + chip_w // 2, cy1 + chip_h // 2), label,
                     size=chip_font, color=WHITE, bold=True, anchor="mm")

            # Санат пен сенімділік. Кадрдың оң жиегінен шығып кетпеуін
            # тексереміз — шықса, жазуды тіктөртбұрыштың оң жағына
            # туралап, солға қарай жазамыз.
            if self.show_captions:
                caption = f"{category.short}  {det.confidence * 100:.0f}%"
                caption_w, _ = text.measure(caption, caption_font, bold=True)
                caption_x = cx1 + chip_w + int(9 * k)

                if caption_x + caption_w > width - int(20 * k):
                    text.add((max(caption_w + 8, min(x2, width - int(8 * k))),
                              cy1 + chip_h // 2),
                             caption, size=caption_font, color=color, bold=True, anchor="rm")
                else:
                    text.add((caption_x, cy1 + chip_h // 2), caption,
                             size=caption_font, color=color, bold=True, anchor="lm")

    # ---------- жоғарғы жолақ ----------

    def _draw_top_bar(
        self,
        image: np.ndarray,
        fix: Optional[GpsFix],
        text: TextLayer,
        stats: HudStats,
        captured_at: Optional[float] = None,
    ) -> None:
        h, w = image.shape[:2]
        k = self._scale(image)
        bar_h = self._bar_height(image)

        cv2.rectangle(image, (0, 0), (w, bar_h), BAR_BG, -1)
        # Жолақтың астындағы жіңішке жиек — кадрдан анық ажыратады
        cv2.line(image, (0, bar_h), (w, bar_h), (48, 46, 46), max(1, int(k)), cv2.LINE_AA)

        cy = bar_h // 2
        font_size = max(11, int(19 * k))
        margin = int(18 * k)

        # --- сол жақ: REC ● AI ROAD SCAN ---
        text.add((margin, cy), "REC", size=font_size, color=WHITE, anchor="lm")
        rec_x = margin + text.measure("REC", font_size)[0] + int(13 * k)
        dot_r = max(3, int(font_size * 0.33))

        rec_glow = np.zeros_like(image)
        cv2.circle(rec_glow, (rec_x, cy), dot_r, REC_RED, -1, cv2.LINE_AA)
        add_glow(image, rec_glow, strength=0.7, blur=max(5, int(9 * k)))
        cv2.circle(image, (rec_x, cy), dot_r, REC_RED, -1, cv2.LINE_AA)

        text.add((rec_x + dot_r + int(12 * k), cy), "AI ROAD SCAN",
                 size=font_size, color=WHITE, anchor="lm")

        # --- орта: координата (локация белгісімен) ---
        if fix is not None:
            coord_text = format_coord(fix.lat, fix.lon)
            colour = WHITE if fix.trusted else (120, 200, 255)
            coord_w = text.measure(coord_text, font_size)[0]

            text.add((w // 2 + int(10 * k), cy), coord_text,
                     size=font_size, color=colour, anchor="mm")

            # Локация тамшысы (pin): дөңгелек + ұшы
            pin_x = w // 2 + int(10 * k) - coord_w // 2 - int(16 * k)
            pin_r = max(3, int(4.5 * k))
            cv2.circle(image, (pin_x, cy - int(3 * k)), pin_r, colour,
                       max(1, int(1.6 * k)), cv2.LINE_AA)
            tip = np.array([
                (pin_x - pin_r + 1, cy - int(1 * k)),
                (pin_x + pin_r - 1, cy - int(1 * k)),
                (pin_x, cy + int(6 * k)),
            ], dtype=np.int32)
            cv2.drawContours(image, [tip], 0, colour, -1, cv2.LINE_AA)
        else:
            text.add((w // 2, cy), "GPS ЖОҚ", size=font_size,
                     color=(80, 140, 255), bold=True, anchor="mm")

        # --- оң жақ: күн мен уақыт ---
        now = (
            datetime.fromtimestamp(captured_at, SHYMKENT_TZ)
            if captured_at is not None
            else datetime.now(SHYMKENT_TZ)
        )
        text.add((w - margin, cy), now.strftime("%d.%m.%Y   %H:%M:%S"),
                 size=font_size, color=WHITE, anchor="rm")

    @staticmethod
    def measure_cache(text: TextLayer, value: str, size: int) -> tuple[int, int]:
        return text.measure(value, size)

    # ---------- ескерту баннері ----------

    def _draw_alert(self, image: np.ndarray, detections: list[Detection], text: TextLayer) -> None:
        """Мокаптағы «НЕ РАБОТАЮТ 2 ФОНАРЯ» баннері."""
        lights_out = [d for d in detections if d.class_key == "streetlight_out"]
        if not lights_out:
            return

        h, w = image.shape[:2]
        k = self._scale(image)

        count = len(lights_out)
        message = f"{count} ФОНАРЬ ЖАНБАЙДЫ" if count > 1 else "ФОНАРЬ ЖАНБАЙДЫ"
        size = max(13, int(21 * k))
        text_w, text_h = text.measure(message, size, bold=True)

        icon_zone = int(38 * k)
        pad_x, pad_y = int(24 * k), int(13 * k)
        box_w = text_w + pad_x * 2 + icon_zone
        box_h = max(int(40 * k), text_h + pad_y * 2)
        x1 = (w - box_w) // 2
        # Landscape кадрда h*0.042 жоғарғы жолақтың ішіне түсетін. Баннер
        # әрқашан top bar-дың астынан басталады.
        y1 = self._bar_height(image) + max(1, int(3 * k))

        # Баннердің айналасындағы жарқырау — көзге бірден түседі
        glow = np.zeros_like(image)
        rounded_rect(glow, (x1, y1), (x1 + box_w, y1 + box_h), ALERT_RED,
                     radius=int(7 * k), thickness=-1)
        blurred = cv2.GaussianBlur(
            glow, (0, 0), sigmaX=max(9, int(15 * k)), sigmaY=max(9, int(15 * k))
        )
        blurred = cv2.convertScaleAbs(blurred, alpha=0.45, beta=0)
        # Glow да top bar ішіне кірмесін: REC/GPS мәтіні әрқашан таза қалады.
        blurred[:self._bar_height(image) + 1] = 0
        cv2.add(image, blurred, dst=image)

        rounded_rect(image, (x1, y1), (x1 + box_w, y1 + box_h), ALERT_RED,
                     radius=int(7 * k), thickness=-1)

        # ⚠ белгісі (леп белгісі бар үшбұрыш)
        icon_cx = x1 + pad_x + int(4 * k)
        icon_cy = y1 + box_h // 2
        tri = int(11 * k)
        triangle = np.array([
            (icon_cx, icon_cy - tri),
            (icon_cx - tri, icon_cy + int(tri * 0.85)),
            (icon_cx + tri, icon_cy + int(tri * 0.85)),
        ], dtype=np.int32)
        cv2.drawContours(image, [triangle], 0, WHITE, max(2, int(2.4 * k)), cv2.LINE_AA)
        text.add((icon_cx, icon_cy + int(2 * k)), "!",
                 size=max(9, int(size * 0.72)), color=WHITE, bold=True, anchor="mm")

        text.add((icon_cx + int(icon_zone * 0.55) + text_w // 2, y1 + box_h // 2),
                 message, size=size, color=WHITE, bold=True, anchor="mm")

    # ---------- төменгі ақпарат панелі ----------

    def _draw_debug_panel(self, image: np.ndarray, stats: HudStats, text: TextLayer) -> None:
        if not self.show_debug:
            return

        h, w = image.shape[:2]
        lines = [
            ("FPS", f"{stats.fps:.1f}"),
            ("Табылған ақау", str(stats.detections_total)),
            ("Жіберілген құжат", str(stats.documents_sent)),
            ("Кезекте", str(stats.queued)),
            ("Жазылуда", str(stats.active_recordings)),
            ("Режим", "ТҮН" if stats.is_night else "КҮНДІЗ"),
            ("GPS", "OK" if stats.gps_ok else "ЖОҚ"),
            ("Portal", "OK" if stats.portal_online else "OFF"),
        ]

        size = 13
        line_h = size + 7
        panel_w = 210
        panel_h = line_h * len(lines) + 16
        x1 = 12
        y1 = h - panel_h - 12

        panel = image[y1:y1 + panel_h, x1:x1 + panel_w]
        if panel.size:
            blended = cv2.addWeighted(
                panel, 0.25, np.full_like(panel, PANEL_BG, dtype=np.uint8), 0.75, 0
            )
            image[y1:y1 + panel_h, x1:x1 + panel_w] = blended

        for i, (key, value) in enumerate(lines):
            y = y1 + 10 + i * line_h
            colour = WHITE
            if key == "GPS" and value == "ЖОҚ":
                colour = (80, 140, 255)
            if key == "Portal" and value == "OFF":
                colour = (80, 180, 255)
            text.add((x1 + 10, y), key, size=size, color=GREY)
            text.add((x1 + panel_w - 10, y), value, size=size, color=colour, bold=True, anchor="ra")

    # ---------- негізгі ----------

    def render(
        self,
        image: np.ndarray,
        detections: list[Detection],
        lane: Optional[LaneResult],
        fix: Optional[GpsFix],
        stats: HudStats,
        captured_at: Optional[float] = None,
    ) -> np.ndarray:
        canvas = image.copy()
        text = TextLayer()

        if self.cfg.draw_lanes:
            self._draw_lanes(canvas, lane)

        self._draw_detections(canvas, detections, text)
        self._draw_top_bar(canvas, fix, text, stats, captured_at=captured_at)
        self._draw_alert(canvas, detections, text)
        self._draw_debug_panel(canvas, stats, text)

        return text.flush(canvas)


def fit_to_width(image: np.ndarray, width: int) -> np.ndarray:
    """Превьюді экранға сыятындай кішірейту."""
    h, w = image.shape[:2]
    if w <= width:
        return image
    scale = width / float(w)
    return cv2.resize(image, (width, int(h * scale)), interpolation=cv2.INTER_AREA)
