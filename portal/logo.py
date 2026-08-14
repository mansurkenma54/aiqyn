"""AIQYN логотипі — бір жерде анықталып, барлық жерде қолданылады.

Логотиптің мағынасы:
    дөңгелектелген шаршы  — қосымша иконкасы
    көлбеу қара эллипс    — көз (қала «көреді»)
    ішіндегі ақ жұлдыз    — «айқын», анық көрінетін нәрсе
    қызыл нүкте           — REC, жазу жүріп жатыр

Екі пішімде беріледі:
    SVG  — сайт, басып шығару беті, favicon
    PNG  — Word құжаты (docx SVG-ні қабылдамайды)
"""

from __future__ import annotations

import io
import math
from functools import lru_cache

# ============================================================
#  Геометрия
# ============================================================

CENTER = 50.0
ELLIPSE_RX, ELLIPSE_RY = 41.0, 25.5
ELLIPSE_ANGLE = -38.0          # градус
STAR_RADIUS = 21.0
STAR_WAIST = 0.19              # неғұрлым кіші болса, сәулелер соғұрлым өткір
REC_DOT = (68.0, 32.0, 6.8)    # x, y, r


def star_path(cx: float = CENTER, cy: float = CENTER,
              radius: float = STAR_RADIUS, waist: float = STAR_WAIST) -> str:
    """Төрт сәулелі жұлдыздың (sparkle) SVG жолы.

    Әр сәуле — кубтық Безье қисығымен жасалған ойыс (concave) бел.
    Полигонмен салынған жұлдыз «үшкір» емес, өрескел көрінеді, сондықтан
    дәл осылай.
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
             eye: str = "#0b0b0c", star: str = "#ffffff",
             dot: str = "#ff2d20", with_frame: bool = True) -> str:
    """Толық иконка (ақ фонды дөңгелектелген шаршы)."""
    frame = (
        f'<rect x="1" y="1" width="98" height="98" rx="{rounded}" fill="{background}"/>'
        if with_frame else ""
    )
    return (
        f'<svg viewBox="0 0 100 100" width="{size}" height="{size}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="AIQYN">'
        f"{frame}"
        f'<ellipse cx="{CENTER}" cy="{CENTER}" rx="{ELLIPSE_RX}" ry="{ELLIPSE_RY}" '
        f'transform="rotate({ELLIPSE_ANGLE} {CENTER} {CENTER})" fill="{eye}"/>'
        f'<path d="{star_path()}" fill="{star}"/>'
        f'<circle cx="{REC_DOT[0]}" cy="{REC_DOT[1]}" r="{REC_DOT[2]}" fill="{dot}"/>'
        f"</svg>"
    )


def svg_outline(size: int = 100) -> str:
    """Қара фонға арналған контурлы нұсқа (құжат бланкісі, ақ түсті бет)."""
    return (
        f'<svg viewBox="0 0 100 100" width="{size}" height="{size}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="AIQYN">'
        f'<ellipse cx="{CENTER}" cy="{CENTER}" rx="{ELLIPSE_RX}" ry="{ELLIPSE_RY}" '
        f'transform="rotate({ELLIPSE_ANGLE} {CENTER} {CENTER})" '
        f'fill="none" stroke="currentColor" stroke-width="3.2"/>'
        f'<path d="{star_path()}" fill="currentColor"/>'
        f'<circle cx="{REC_DOT[0]}" cy="{REC_DOT[1]}" r="{REC_DOT[2]}" fill="#ff2d20"/>'
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

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 1) Дөңгелектелген шаршы
    if not transparent:
        draw.rounded_rectangle(
            [0, 0, canvas - 1, canvas - 1],
            radius=int(23 * unit), fill=(255, 255, 255, 255),
        )

    # 2) Көлбеу эллипс — бөлек қабатта сызып, бұрып қоямыз
    layer = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        [
            (CENTER - ELLIPSE_RX) * unit, (CENTER - ELLIPSE_RY) * unit,
            (CENTER + ELLIPSE_RX) * unit, (CENTER + ELLIPSE_RY) * unit,
        ],
        fill=(11, 11, 12, 255),
    )
    layer = layer.rotate(-ELLIPSE_ANGLE, resample=Image.BICUBIC, center=(canvas / 2, canvas / 2))
    image = Image.alpha_composite(image, layer)
    draw = ImageDraw.Draw(image)

    # 3) Төрт сәулелі жұлдыз — Безье қисығын нүктелермен жуықтаймыз
    points: list[tuple[float, float]] = []
    radius = STAR_RADIUS * unit
    waist = radius * STAR_WAIST
    cx = cy = CENTER * unit

    def bezier(p0, p1, p2, p3, steps: int = 18):
        for i in range(steps + 1):
            t = i / steps
            u = 1 - t
            x = (u ** 3) * p0[0] + 3 * (u ** 2) * t * p1[0] + 3 * u * (t ** 2) * p2[0] + (t ** 3) * p3[0]
            y = (u ** 3) * p0[1] + 3 * (u ** 2) * t * p1[1] + 3 * u * (t ** 2) * p2[1] + (t ** 3) * p3[1]
            points.append((x, y))

    tips = [(cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy)]
    controls = [
        (cx + waist, cy - waist), (cx + waist, cy + waist),
        (cx - waist, cy + waist), (cx - waist, cy - waist),
    ]
    for i in range(4):
        bezier(tips[i], controls[i], controls[i], tips[(i + 1) % 4])

    draw.polygon(points, fill=(255, 255, 255, 255))

    # 4) Қызыл REC нүктесі
    x, y, r = REC_DOT
    draw.ellipse(
        [(x - r) * unit, (y - r) * unit, (x + r) * unit, (y + r) * unit],
        fill=(255, 45, 32, 255),
    )

    image = image.resize((size, size), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def png_stream(size: int = 256, transparent: bool = True) -> io.BytesIO:
    return io.BytesIO(png_bytes(size, transparent))
