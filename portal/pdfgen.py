"""Ресми өтінімді PDF файлы ретінде құрастыру.

Не үшін бөлек PDF: iKomek 109-ға WhatsApp арқылы жүгінгенде хабарламаға
файл ТІРКЕЛМЕЙДІ — wa.me тек мәтін бере алады. Сондықтан хатта құжатқа
СІЛТЕМЕ тұрады, ал сілтеме нақты PDF-ті ашуы керек: .docx файлын
телефоннан ашу қиын, ал HTML бетті «құжат» деп қабылдамайды.

Шрифт: DejaVu Sans — қазақ кириллицасы толық (Ә Ғ Қ Ң Ө Ұ Ү Һ І),
лицензиясы еркін, жобаның өз ішінде жатыр. Жүйелік шрифтке сүйенуге
болмайды: серверде ол жоқ.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

FONT_DIR = Path(__file__).parent / "static" / "vendor" / "fonts"
BLACK = colors.HexColor("#0b0b0c")
GREY = colors.HexColor("#5a5a56")
LINE = colors.HexColor("#deded9")
RED = colors.HexColor("#c2381a")

_registered = False


def _fonts() -> tuple[str, str]:
    """Шрифттерді бір рет тіркеу. Табылмаса — стандарт Helvetica."""
    global _registered
    if _registered:
        return ("AIQYN", "AIQYN-Bold")
    try:
        pdfmetrics.registerFont(TTFont("AIQYN", str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("AIQYN-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("AIQYN", normal="AIQYN", bold="AIQYN-Bold")
        _registered = True
        return ("AIQYN", "AIQYN-Bold")
    except Exception:                              # noqa: BLE001
        return ("Helvetica", "Helvetica-Bold")


def _styles(regular: str, bold: str) -> dict[str, ParagraphStyle]:
    return {
        "brand": ParagraphStyle("brand", fontName=bold, fontSize=15, leading=18,
                                textColor=BLACK, spaceAfter=1),
        "brand_sub": ParagraphStyle("brand_sub", fontName=regular, fontSize=8,
                                    leading=11, textColor=GREY),
        "eyebrow": ParagraphStyle("eyebrow", fontName=bold, fontSize=7.5, leading=11,
                                  textColor=GREY, spaceAfter=3),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=17, leading=21,
                             textColor=BLACK, spaceAfter=4),
        "meta": ParagraphStyle("meta", fontName=regular, fontSize=8.5, leading=12,
                               textColor=GREY),
        "key": ParagraphStyle("key", fontName=regular, fontSize=8.5, leading=12,
                              textColor=GREY),
        "val": ParagraphStyle("val", fontName=regular, fontSize=9.5, leading=13,
                              textColor=BLACK),
        "val_b": ParagraphStyle("val_b", fontName=bold, fontSize=9.5, leading=13,
                                textColor=BLACK),
        "section": ParagraphStyle("section", fontName=bold, fontSize=9, leading=12,
                                  textColor=GREY, spaceBefore=10, spaceAfter=5),
        "body": ParagraphStyle("body", fontName=regular, fontSize=9.5, leading=14,
                               textColor=BLACK, alignment=TA_LEFT),
        "foot": ParagraphStyle("foot", fontName=regular, fontSize=7.5, leading=10.5,
                               textColor=GREY),
        "warn": ParagraphStyle("warn", fontName=bold, fontSize=8.5, leading=12,
                               textColor=RED),
    }


def _escape(value: object) -> str:
    text = str(value or "")
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br/>"))


def build_pdf(document: dict, media_dir: Path, portal_url: str = "") -> io.BytesIO:
    """Құжатты PDF етіп жасап, жадтағы буферді қайтарады."""
    regular, bold = _fonts()
    style = _styles(regular, bold)
    buffer = io.BytesIO()

    event_id = document.get("event_id") or ""
    ticket = document.get("external_ticket_id") or ""
    draft = not ticket

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"AIQYN {event_id}",
        author="AIQYN ZHOL",
        subject=document.get("defect_type_official") or "Жол ақауы туралы өтінім",
    )

    flow: list = []

    # ---- Бланк тақталы ----
    logo_cell = ""
    try:
        from .logo import png_bytes
        logo_cell = Image(io.BytesIO(png_bytes(160, transparent=True)),
                          width=13 * mm, height=13 * mm)
    except Exception:                              # noqa: BLE001
        pass

    head = Table(
        [[logo_cell,
          [Paragraph("AIQYN ZHOL", style["brand"]),
           Paragraph("Шымкент қаласының жол инфрақұрылымын автоматты бақылау жүйесі",
                     style["brand_sub"])],
          Paragraph(f"{'ӨТІНІМ ЖОБАСЫ' if draft else 'ТІРКЕЛГЕН ӨТІНІМ'}<br/>"
                    f"<font size=8>{_escape(ticket or event_id)}</font>", style["meta"])]],
        colWidths=[16 * mm, 105 * mm, 53 * mm],
    )
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, BLACK),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (2, 0), (2, 0), 0),
    ]))
    flow += [head, Spacer(1, 9)]

    # ---- Тақырып ----
    flow += [
        Paragraph("ЖОЛ ИНФРАҚҰРЫЛЫМЫНЫҢ АҚАУЫ", style["eyebrow"]),
        Paragraph(_escape(document.get("defect_type_official") or "Инфрақұрылым ақауы"),
                  style["h1"]),
        Paragraph(_escape(document.get("defect_type_official_ru") or ""), style["meta"]),
        Spacer(1, 8),
    ]

    if draft:
        flow += [Paragraph(
            "ЕСКЕРТУ: бұл — өтінім жобасы. Оператор растағаннан кейін ғана "
            "ресми арнаға жіберіледі.", style["warn"]), Spacer(1, 7)]

    # ---- Негізгі деректер ----
    lat, lon = document.get("lat"), document.get("lon")
    coords = (f"{float(lat):.6f}, {float(lon):.6f}"
              if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else "—")
    binding = document.get("spatial") or {}

    rows = [
        ("Оқиға нөмірі", event_id),
        ("Мекенжай", document.get("display_address_text") or document.get("address_text") or "—"),
        ("Координата", coords),
        ("Қауіптілік деңгейі", document.get("severity_kk") or document.get("severity") or "—"),
        ("Анықталған уақыты", document.get("timestamp_human") or "—"),
        ("Жауапты ұйым", document.get("responsible_org") or "ЕКЦ 109 диспетчерлік кезегі"),
    ]
    if binding.get("bound"):
        rows.append(("Жол сегменті (OSM)", binding.get("spatial_id") or "—"))
    if document.get("ai_note"):
        rows.append(("ЖИ қорытындысы",
                     ("расталды" if document.get("ai_verified") else "расталмады")
                     + (f" · {round(float(document['ai_confidence']) * 100)}%"
                        if document.get("ai_confidence") else "")))

    table = Table(
        [[Paragraph(_escape(key), style["key"]), Paragraph(_escape(value), style["val"])]
         for key, value in rows],
        colWidths=[42 * mm, 132 * mm],
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    flow += [table]

    # ---- Дәлел суреті ----
    photo_name = document.get("photo_file")
    if photo_name:
        path = Path(media_dir) / event_id / photo_name
        if path.exists():
            try:
                width, height = ImageReader(str(path)).getSize()
                box_w = 174 * mm
                box_h = min(95 * mm, box_w * height / max(1, width))
                flow += [
                    Paragraph("ФОТОФИКСАЦИЯ", style["section"]),
                    Image(str(path), width=box_w, height=box_h),
                ]
            except Exception:                      # noqa: BLE001
                pass

    # ---- Сипаттама ----
    description = document.get("description_text") or ""
    if description:
        flow += [
            Paragraph("ӨТІНІМНІҢ МӘТІНІ", style["section"]),
            Paragraph(_escape(description), style["body"]),
        ]

    # ---- Тексеру сілтемелері ----
    links = []
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        links.append(f"Картада: https://www.google.com/maps?q={lat},{lon}")
    if portal_url and event_id:
        links.append(f"Оқиға парағы: {portal_url}/portal?event={event_id}")
    if links:
        flow += [
            Paragraph("ДӘЛЕЛДІ ТЕКСЕРУ", style["section"]),
            Paragraph("<br/>".join(_escape(link) for link in links), style["foot"]),
        ]

    flow += [
        Spacer(1, 10),
        Paragraph(
            "Ақау автоматтандырылған мониторинг жүйесімен табылды, оператор растады. "
            "Фото, координата және уақыт белгісі жүйеде сақтаулы. "
            "Жүйе тұлғаны да, көлік нөмірін де танымайды.",
            style["foot"]),
    ]

    def page_footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(regular, 7)
        canvas.setFillColor(GREY)
        canvas.setStrokeColor(LINE)
        canvas.line(18 * mm, 12 * mm, A4[0] - 18 * mm, 12 * mm)
        canvas.drawString(18 * mm, 8 * mm, f"AIQYN ZHOL · {event_id}")
        canvas.drawRightString(A4[0] - 18 * mm, 8 * mm, f"{canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(flow, onFirstPage=page_footer, onLaterPages=page_footer)
    buffer.seek(0)
    return buffer


def safe_filename(event_id: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(event_id))
    return f"{clean or 'AIQYN'}.pdf"
