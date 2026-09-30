"""AIQYN логотипі — бір жерде анықталып, барлық жерде қолданылады.

Логотиптің мағынасы:
    дөңгелектелген ақ шаршы  — қосымша иконкасы
    көлбеу қара эллипс       — КӨЗ: қала жолды көріп отыр
    ішіндегі перспектива жол — ақ жиек сызықтары мен үзік орта сызық
                               (асфальт қара — көздің өз түсі)
    төрт сәулелі жұлдыз      — «айқын»: жолдың соңындағы анық нүкте.
                               Оның төменгі сәулесі жолдың тоғысу
                               нүктесімен ҚОСЫЛАДЫ — біртұтас сәуле.
    қызыл нүкте              — REC, жазу жүріп жатыр

Екі пішімде беріледі:
    SVG  — сайт, басып шығару беті, favicon
    PNG  — Word құжаты (docx SVG-ні қабылдамайды)

Анимация үшін бөліктердің класы бар:
    .lg-badge  ақ таблетка     .lg-eye   эллипс
    .lg-road   жол (тобы)      .lg-edge  жиек сызығы
    .lg-dash   үзік сызық      .lg-star  жұлдыз
    .lg-dot    REC нүктесі
"""

from __future__ import annotations

import io
import math
from functools import lru_cache

# ============================================================
#  Геометрия — бәрі 0..100 торында
# ============================================================

CENTER = 50.0

# Көз: көлбеу эллипс
ELLIPSE_RX, ELLIPSE_RY = 47.0, 26.0
ELLIPSE_ANGLE = -36.0                       # градус, SVG rotate()

# Жұлдыз: жолдың тоғысу нүктесінің дәл үстінде тұрады
STAR_CY = 45.0
STAR_RADIUS = 17.0
STAR_WAIST = 0.16                           # кіші болса — сәулелер өткір

# Жол: жұлдыздың төменгі ұшынан басталып, көзді толтыра кеңейеді.
# ROAD_BOT_* эллипстен әдейі асып тұр — шеті клиппен қиылады да,
# жол көздің жиегіне дейін жайылады.
ROAD_TOP_Y = STAR_CY + STAR_RADIUS          # = 62.0, жұлдызбен қосылады
ROAD_TOP_HALF = 0.6
ROAD_BOT_Y = 100.0
ROAD_BOT_HALF = 58.0
ROAD_EDGE_TOP = 0.4                         # жиек сызығының қалыңдығы
ROAD_EDGE_BOT = 4.6
DASH_K = 0.075                              # орта сызық — жол енінің үлесі
DASH_STEPS = ((0.06, 0.17), (0.26, 0.42), (0.52, 0.72), (0.84, 1.0))

REC_DOT = (78.0, 20.0, 5.2)                 # x, y, r

ASPHALT = "#0b0b0c"
PAINT = "#ffffff"
SIGNAL = "#ff2d20"


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _half(t: float) -> float:
    return _lerp(ROAD_TOP_HALF, ROAD_BOT_HALF, t)


def _y(t: float) -> float:
    return _lerp(ROAD_TOP_Y, ROAD_BOT_Y, t)


def _edge_w(t: float) -> float:
    return _lerp(ROAD_EDGE_TOP, ROAD_EDGE_BOT, t)


# ------------------------------------------------------------
#  Жолдың бөліктері
# ------------------------------------------------------------

def _edge_points(side: int) -> list[tuple[float, float]]:
    """Жолдың бір жиегі — перспективамен қалыңдайтын жолақ."""
    return [
        (CENTER + side * _half(0) - _edge_w(0) / 2, _y(0)),
        (CENTER + side * _half(1) - _edge_w(1) / 2, _y(1)),
        (CENTER + side * _half(1) + _edge_w(1) / 2, _y(1)),
        (CENTER + side * _half(0) + _edge_w(0) / 2, _y(0)),
    ]


def _dash_points(a: float, b: float) -> list[tuple[float, float]]:
    w0, w1 = _half(a) * DASH_K, _half(b) * DASH_K
    return [(CENTER - w0, _y(a)), (CENTER + w0, _y(a)),
            (CENTER + w1, _y(b)), (CENTER - w1, _y(b))]


def _path(points: list[tuple[float, float]]) -> str:
    head, *rest = points
    body = " ".join(f"L {x:.2f} {y:.2f}" for x, y in rest)
    return f"M {head[0]:.2f} {head[1]:.2f} {body} Z"


def road_svg(color: str = PAINT) -> str:
    """Жолдың таңбалары: екі жиек сызығы және орта үзік сызық."""
    parts = [
        f'<path class="lg-edge lg-edge-l" d="{_path(_edge_points(-1))}" fill="{color}"/>',
        f'<path class="lg-edge lg-edge-r" d="{_path(_edge_points(1))}" fill="{color}"/>',
    ]
    parts += [
        f'<path class="lg-dash lg-dash-{i + 1}" d="{_path(_dash_points(a, b))}" fill="{color}"/>'
        for i, (a, b) in enumerate(DASH_STEPS)
    ]
    return "".join(parts)


def star_path(cx: float = CENTER, cy: float = STAR_CY,
              radius: float = STAR_RADIUS, waist: float = STAR_WAIST) -> str:
    """Төрт сәулелі жұлдыздың (sparkle) SVG жолы.

    Әр сәуле — кубтық Безье қисығымен жасалған ойыс (concave) бел.
    Полигонмен салынған жұлдыз үшкір емес, өрескел көрінеді.
    """
    k = radius * waist
    return (
        f"M {cx:.2f} {cy - radius:.2f} "
        f"C {cx + k:.2f} {cy - k:.2f} {cx + k:.2f} {cy - k:.2f} {cx + radius:.2f} {cy:.2f} "
        f"C {cx + k:.2f} {cy + k:.2f} {cx + k:.2f} {cy + k:.2f} {cx:.2f} {cy + radius:.2f} "
        f"C {cx - k:.2f} {cy + k:.2f} {cx - k:.2f} {cy + k:.2f} {cx - radius:.2f} {cy:.2f} "
        f"C {cx - k:.2f} {cy - k:.2f} {cx - k:.2f} {cy - k:.2f} {cx:.2f} {cy - radius:.2f} Z"
    )


# ============================================================
#  SVG
# ============================================================

def svg_icon(size: int = 100, rounded: int = 23, background: str = "#ffffff",
             eye: str = ASPHALT, paint: str = PAINT,
             dot: str = SIGNAL, with_frame: bool = True, uid: str = "aq") -> str:
    """Толық иконка (ақ фонды дөңгелектелген шаршы).

    uid — clipPath идентификаторы. Бір бетте бірнеше логотип тұрса,
    әрқайсысына бөлек берген дұрыс.
    """
    frame = (
        f'<rect class="lg-badge" x="1" y="1" width="98" height="98" '
        f'rx="{rounded}" fill="{background}"/>'
        if with_frame else ""
    )
    ellipse = (
        f'cx="{CENTER}" cy="{CENTER}" rx="{ELLIPSE_RX}" ry="{ELLIPSE_RY}" '
        f'transform="rotate({ELLIPSE_ANGLE} {CENTER} {CENTER})"'
    )
    return (
        f'<svg viewBox="0 0 100 100" width="{size}" height="{size}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="AIQYN ZHOL">'
        f'<defs><clipPath id="lg-eye-{uid}"><ellipse {ellipse}/></clipPath></defs>'
        f"{frame}"
        f'<ellipse class="lg-eye" {ellipse} fill="{eye}"/>'
        f'<g class="lg-road" clip-path="url(#lg-eye-{uid})">{road_svg(paint)}</g>'
        f'<path class="lg-star" d="{star_path()}" fill="{paint}"/>'
        f'<circle class="lg-dot" cx="{REC_DOT[0]}" cy="{REC_DOT[1]}" '
        f'r="{REC_DOT[2]}" fill="{dot}"/>'
        f"</svg>"
    )


def svg_outline(size: int = 100, uid: str = "ol") -> str:
    """Қара фонға арналған контурлы нұсқа (құжат бланкісі, ақ түсті бет)."""
    ellipse = (
        f'cx="{CENTER}" cy="{CENTER}" rx="{ELLIPSE_RX}" ry="{ELLIPSE_RY}" '
        f'transform="rotate({ELLIPSE_ANGLE} {CENTER} {CENTER})"'
    )
    return (
        f'<svg viewBox="0 0 100 100" width="{size}" height="{size}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="AIQYN">'
        f'<defs><clipPath id="lg-eye-{uid}"><ellipse {ellipse}/></clipPath></defs>'
        f'<ellipse {ellipse} fill="none" stroke="currentColor" stroke-width="3.2"/>'
        f'<g clip-path="url(#lg-eye-{uid})">{road_svg("currentColor")}</g>'
        f'<path d="{star_path()}" fill="currentColor"/>'
        f'<circle cx="{REC_DOT[0]}" cy="{REC_DOT[1]}" r="{REC_DOT[2]}" fill="{SIGNAL}"/>'
        f"</svg>"
    )


def favicon_data_uri() -> str:
    """Браузер қойындысындағы белгі (сыртқы файлсыз)."""
    svg = svg_icon(size=64).replace("#", "%23").replace('"', "'").replace("\n", "")
    return f"data:image/svg+xml,{svg}"


# ============================================================
#  PNG (Word құжаты үшін)
# ============================================================

@lru_cache(maxsize=8)
def png_bytes(size: int = 256, transparent: bool = True) -> bytes:
    """Логотипті PNG етіп сызу (Pillow арқылы, сыртқы тәуелділіксіз).

    docx SVG-ні қабылдамайды, сондықтан бланк үшін растр керек.
    Тегіс жиек алу үшін 4 есе үлкен сызып, кейін кішірейтеміз.
    """
    from PIL import Image, ImageDraw

    scale = 4
    canvas = size * scale
    unit = canvas / 100.0

    def px(points):
        return [(x * unit, y * unit) for x, y in points]

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))

    # 1) Дөңгелектелген шаршы
    if not transparent:
        ImageDraw.Draw(image).rounded_rectangle(
            [0, 0, canvas - 1, canvas - 1],
            radius=int(23 * unit), fill=(255, 255, 255, 255),
        )

    # 2) Көз: бөлек қабатта сызылып, бұрылады. Сол қабаттың альфасы
    #    жолдың МАСКАСЫ болады — SVG-дегі clipPath-тың дәл баламасы.
    eye_layer = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(eye_layer).ellipse(
        [(CENTER - ELLIPSE_RX) * unit, (CENTER - ELLIPSE_RY) * unit,
         (CENTER + ELLIPSE_RX) * unit, (CENTER + ELLIPSE_RY) * unit],
        fill=(11, 11, 12, 255),
    )
    eye_layer = eye_layer.rotate(-ELLIPSE_ANGLE, resample=Image.BICUBIC,
                                 center=(canvas / 2, canvas / 2))
    image = Image.alpha_composite(image, eye_layer)

    # 3) Жол — эллипстің ішінде ғана
    road_layer = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    road_draw = ImageDraw.Draw(road_layer)
    for side in (-1, 1):
        road_draw.polygon(px(_edge_points(side)), fill=(255, 255, 255, 255))
    for a, b in DASH_STEPS:
        road_draw.polygon(px(_dash_points(a, b)), fill=(255, 255, 255, 255))
    road_layer.putalpha(Image.composite(
        road_layer.getchannel("A"),
        Image.new("L", (canvas, canvas), 0),
        eye_layer.getchannel("A"),
    ))
    image = Image.alpha_composite(image, road_layer)

    draw = ImageDraw.Draw(image)

    # 4) Жұлдыз — Безье қисығын нүктелермен жуықтаймыз
    points: list[tuple[float, float]] = []
    radius = STAR_RADIUS * unit
    waist = radius * STAR_WAIST
    cx, cy = CENTER * unit, STAR_CY * unit

    def bezier(p0, control, p3, steps: int = 18):
        for i in range(steps + 1):
            t = i / steps
            u = 1 - t
            points.append((
                (u ** 3) * p0[0] + 3 * (u ** 2) * t * control[0]
                + 3 * u * (t ** 2) * control[0] + (t ** 3) * p3[0],
                (u ** 3) * p0[1] + 3 * (u ** 2) * t * control[1]
                + 3 * u * (t ** 2) * control[1] + (t ** 3) * p3[1],
            ))

    tips = [(cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy)]
    controls = [(cx + waist, cy - waist), (cx + waist, cy + waist),
                (cx - waist, cy + waist), (cx - waist, cy - waist)]
    for i in range(4):
        bezier(tips[i], controls[i], tips[(i + 1) % 4])
    draw.polygon(points, fill=(255, 255, 255, 255))

    # 5) Қызыл REC нүктесі
    x, y, r = REC_DOT
    draw.ellipse([(x - r) * unit, (y - r) * unit, (x + r) * unit, (y + r) * unit],
                 fill=(255, 45, 32, 255))

    image = image.resize((size, size), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def png_stream(size: int = 256, transparent: bool = True) -> io.BytesIO:
    return io.BytesIO(png_bytes(size, transparent))
