"""Ресми құжатты Word (.docx) файлы ретінде құрастыру.

Неге Word: Қазақстанда мемлекеттік органдармен ресми хат алмасу негізінен
.docx форматында жүреді. Оператор құжатты жүктеп алып, қолы қойылған
бланкіге қоя алады немесе тікелей жібере алады.

Мазмұны:
    шапка → кімге → ақау туралы кесте → ресми мәтін (қаз) → орысша нұсқа
    → ЖИ талдауының қорытындысы → дәлел фотосы → қолтаңба орны
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

log = logging.getLogger("portal.docgen")

SHYMKENT_TZ = timezone(timedelta(hours=5))

ACCENT = RGBColor(0xC0, 0x2B, 0x14)
GREY = RGBColor(0x66, 0x66, 0x66)
DARK = RGBColor(0x11, 0x11, 0x11)


def _set_cell_background(cell, hex_color: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shading)


def _bottom_border(paragraph, size: int = 12, color: str = "111111") -> None:
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def _add_run(paragraph, text: str, size: int = 10, bold: bool = False,
             color: RGBColor = DARK, italic: bool = False):
    run = paragraph.add_run(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = "Segoe UI"
    return run


SYSTEM_ACTORS = {"", "system", "auto", "aiqyn", "aiqyn vision"}


def _display_time(value: object) -> str:
    """ISO уақытын құжатта ықшам көрсету; белгісіз форматты өзгертпеу."""
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("T", " ").replace("+05:00", "")[:19]


def _operator_review(document: dict) -> Optional[dict]:
    """Оператор тексеруін тек нақты actor/time немесе тарих растағанда қайтару."""
    actor = (
        document.get("reviewer_name")
        or document.get("reviewer_id")
        or document.get("reviewed_by")
    )
    reviewed_at = document.get("reviewed_at") or document.get("verified_at")
    if actor and str(actor).strip().lower() not in SYSTEM_ACTORS:
        return {
            "actor": str(actor),
            "at": _display_time(reviewed_at),
            "decision": str(document.get("review_status") or "verified"),
        }

    for item in reversed(document.get("history") or []):
        status = str(item.get("status") or "").lower()
        item_actor = str(item.get("actor") or "").strip()
        if status not in {"confirmed", "verified", "rejected"}:
            continue
        if item_actor.lower() in SYSTEM_ACTORS:
            continue
        return {
            "actor": item_actor,
            "at": _display_time(item.get("created_at")),
            "decision": status,
        }
    return None


def _external_ticket_id(document: dict) -> str:
    return str(
        document.get("external_ticket_id")
        or document.get("service_request_id")
        or document.get("ticket_id")
        or document.get("registration_number")
        or ""
    ).strip()


def _first_value(document: dict, *keys: str):
    for key in keys:
        value = document.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: object) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _coords(lat: object, lon: object) -> str:
    lat_num, lon_num = _number(lat), _number(lon)
    if lat_num is None or lon_num is None:
        return "—"
    return f"{lat_num:.6f}, {lon_num:.6f}"


def _state(document: dict, review: Optional[dict], ticket_id: str) -> dict:
    status = str(document.get("status") or "new").lower()
    sent = bool(document.get("sent_at")) or status in {
        "sent", "submitted", "registered", "assigned", "in_progress",
        "resolved", "repaired", "verification_pending", "closed", "reopened",
    }

    if ticket_id:
        return {
            "label": f"ЕКЦ 109-ДА ТІРКЕЛГЕН  ·  № {ticket_id}",
            "fill": "E8F3EA",
            "title": "ЖОЛ ИНФРАҚҰРЫЛЫМЫНДАҒЫ АҚАУДЫ ТЕКСЕРУ ЖӘНЕ ЖОЮ ЖӨНІНДЕГІ ӨТІНІМ",
            "draft": False,
        }
    if status == "rejected":
        return {
            "label": "ОПЕРАТОР РАСТАМАДЫ — РЕСМИ АРНАҒА ЖІБЕРІЛМЕЙДІ",
            "fill": "F2F2F3",
            "title": "АҚАУ БЕЛГІСІ ТУРАЛЫ ОҚИҒА КАРТОЧКАСЫ",
            "draft": True,
        }
    if sent and not review:
        return {
            "label": "ЖІБЕРІЛГЕН — ОПЕРАТОР ТЕКСЕРУІ РАСТАЛМАҒАН",
            "fill": "FCE8E6",
            "title": "ЖОЛ ИНФРАҚҰРЫЛЫМЫНДАҒЫ АҚАУ ТУРАЛЫ ХАБАРЛАМА",
            "draft": False,
        }
    if sent:
        return {
            "label": "РЕСМИ АРНАҒА ЖІБЕРІЛДІ — ТІРКЕУ НӨМІРІ КҮТІЛУДЕ",
            "fill": "FFF4CE",
            "title": "ЖОЛ ИНФРАҚҰРЫЛЫМЫНДАҒЫ АҚАУДЫ ТЕКСЕРУ ЖӘНЕ ЖОЮ ЖӨНІНДЕГІ ӨТІНІМ",
            "draft": False,
        }
    if review and review.get("decision") != "rejected":
        return {
            "label": "ОПЕРАТОР ТЕКСЕРГЕН ӨТІНІМ ЖОБАСЫ — РЕСМИ АРНАҒА ЖІБЕРІЛМЕГЕН",
            "fill": "FFF4CE",
            "title": "ЖОЛ ИНФРАҚҰРЫЛЫМЫНДАҒЫ АҚАУДЫ ТЕКСЕРУ ЖӘНЕ ЖОЮ ЖӨНІНДЕГІ ӨТІНІМ ЖОБАСЫ",
            "draft": True,
        }
    return {
        "label": "ӨТІНІМ ЖОБАСЫ — РЕСМИ АРНАҒА ЖІБЕРІЛМЕГЕН",
        "fill": "FCE8E6",
        "title": "ЖОЛ ИНФРАҚҰРЫЛЫМЫНДАҒЫ АҚАУДЫ ТЕКСЕРУ ЖӘНЕ ЖОЮ ЖӨНІНДЕГІ ӨТІНІМ ЖОБАСЫ",
        "draft": True,
    }


def build_docx(document: dict, media_dir: Path) -> io.BytesIO:
    """Құжатты .docx етіп жасап, жадтағы буферді қайтарады."""

    review = _operator_review(document)
    ticket_id = _external_ticket_id(document)
    state = _state(document, review, ticket_id)

    doc = Document()

    section = doc.sections[0]
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(1.6)

    style = doc.styles["Normal"]
    style.font.name = "Segoe UI"
    style.font.size = Pt(10)

    # ---------- Шапка ----------
    header = doc.add_paragraph()
    try:
        from .logo import png_stream
        header.add_run().add_picture(png_stream(220, transparent=True), width=Cm(1.15))
        header.add_run("  ")
    except Exception as exc:      # логотип салынбаса да құжат құрылуы керек
        log.debug("Логотип қосылмады: %s", exc)
    _add_run(header, "AIQYN", size=20, bold=True)
    header.paragraph_format.space_after = Pt(0)

    subtitle = doc.add_paragraph()
    _add_run(
        subtitle,
        "Қала инфрақұрылымын автоматты бақылау жүйесі  ·  Шымкент қаласы",
        size=8.5, color=GREY,
    )
    _bottom_border(subtitle)
    subtitle.paragraph_format.space_after = Pt(12)

    meta_line = doc.add_paragraph()
    meta_line.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _add_run(
        meta_line,
        f"AIQYN оқиға ID: {document.get('event_id', '')}\n",
        size=8.5, color=GREY,
    )
    _add_run(
        meta_line,
        f"Қалыптастырылды: {datetime.now(SHYMKENT_TZ).strftime('%d.%m.%Y %H:%M')}",
        size=8.5, color=GREY,
    )

    state_table = doc.add_table(rows=1, cols=1)
    state_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    state_cell = state_table.cell(0, 0)
    _set_cell_background(state_cell, state["fill"])
    state_paragraph = state_cell.paragraphs[0]
    state_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_run(state_paragraph, state["label"], size=9, bold=True)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)

    # ---------- Кімге ----------
    recipient = doc.add_paragraph()
    _add_run(recipient, "Кімге: ", size=10, bold=True)
    _add_run(recipient, document.get("responsible_org") or "—", size=10)
    if document.get("responsible_org_ru"):
        _add_run(recipient, f"\nКому: {document['responsible_org_ru']}", size=9, color=GREY)
    recipient.paragraph_format.space_after = Pt(10)

    # ---------- Тақырып ----------
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_run(title, state["title"], size=12.5, bold=True)
    title.paragraph_format.space_after = Pt(10)

    # ---------- Негізгі кесте ----------
    display_lat = _first_value(document, "display_lat", "snapped_lat", "lat")
    display_lon = _first_value(document, "display_lon", "snapped_lon", "lon")
    raw_lat = _first_value(document, "original_lat", "raw_lat", "lat")
    raw_lon = _first_value(document, "original_lon", "raw_lon", "lon")
    accuracy = _number(_first_value(
        document, "display_accuracy_m", "location_accuracy_m", "gps_accuracy_m"
    ))

    rows = [
        ("AIQYN оқиға ID", document.get("event_id", "")),
        ("Ақау белгісі", document.get("defect_type_official", "")),
        ("Тип дефекта", document.get("defect_type_official_ru", "")),
        ("Оқиға орны", document.get("address_text", "")),
        ("Картадағы координаттар", _coords(display_lat, display_lon)),
        ("Картадағы орны", document.get("map_link", "")),
        ("Анықталған уақыты", document.get("timestamp_human", "")),
        ("Алдын ала қауіп деңгейі",
         f"{document.get('severity_kk') or document.get('severity', '')} "
         "(автоматты бағалау; маман тексеруін талап етеді)"),
        ("Модель сенімділігі",
         f"{float(document.get('confidence') or 0) * 100:.0f}%  "
         f"({document.get('detector', '')}; қауіп деңгейі емес)"),
        ("Жол жұмыстары тізілімімен салыстыру",
         "Белсенді учаскемен автоматты сәйкестік бар"
         if document.get("marked_on_road")
         else "Автоматты сәйкестік табылмады; операторлық нақтылау қажет"),
    ]

    if ticket_id:
        rows.insert(1, ("ЕКЦ 109 тіркеу нөмірі", ticket_id))
    if document.get("service_code"):
        rows.insert(3, ("Қызмет коды", document["service_code"]))
    if accuracy and accuracy > 0:
        rows.insert(6, ("Геопозиция дәлдігі", f"±{accuracy:.0f} м (болжамды)"))
    elif not document.get("gps_trusted", True):
        rows.insert(6, ("Геопозиция дәлдігі", "Дереккөз көрсетпеген; нақтылау қажет"))

    raw_coords = _coords(raw_lat, raw_lon)
    display_coords = _coords(display_lat, display_lon)
    if raw_coords != "—" and raw_coords != display_coords:
        rows.insert(6, ("Бастапқы GPS координаттары", raw_coords))

    if review:
        decision = (
            "Оқиғаны растады" if review.get("decision") != "rejected"
            else "Оқиғаны растамады"
        )
        review_value = f"{review['actor']} · {decision}"
        if review.get("at"):
            review_value += f" · {review['at']}"
        rows.append(("Операторлық тексеру", review_value))

    assigned_org = _first_value(document, "assigned_org", "executor", "responsible_service")
    if assigned_org:
        rows.append(("Тағайындалған орындаушы", assigned_org))
    due_at = _first_value(document, "due_at", "sla_due_at", "planned_due_at")
    if due_at:
        rows.append(("Жоспарлы орындалу мерзімі", _display_time(due_at)))

    if document.get("ai_verified"):
        rows.append((
            "Қосымша ЖИ бағасы",
            f"Алдын ала талдау бар "
            f"({float(document.get('ai_confidence') or 0) * 100:.0f}%); "
            "инженерлік тексеруді алмастырмайды",
        ))
    if document.get("ai_size"):
        rows.append(("Шамамен өлшемі", document["ai_size"]))
    if document.get("ai_location"):
        rows.append(("Жолдағы орны", document["ai_location"]))
    if document.get("ai_danger"):
        rows.append(("Ықтимал қозғалыс қаупі", document["ai_danger"]))
    if document.get("ai_action"):
        urgency = document.get("ai_urgency_days") or 0
        suffix = (
            f"  (ЖИ бағдарлаған мерзім: {urgency} күн; нормативтік SLA емес)"
            if urgency else ""
        )
        rows.append(("ЖИ ұсынған ықтимал шара", f"{document['ai_action']}{suffix}"))

    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    for key, value in rows:
        cells = table.add_row().cells
        cells[0].width = Cm(4.8)
        cells[1].width = Cm(11.4)

        key_paragraph = cells[0].paragraphs[0]
        _add_run(key_paragraph, key, size=9, bold=True)
        _set_cell_background(cells[0], "F2F2F3")

        value_paragraph = cells[1].paragraphs[0]
        _add_run(value_paragraph, str(value), size=9.5)

    doc.add_paragraph().paragraph_format.space_after = Pt(6)

    # ---------- Ресми мәтін ----------
    heading = doc.add_paragraph()
    _add_run(
        heading,
        "Өтінім жобасының мәтіні" if state["draft"] else "Өтінім мәтіні",
        size=10.5, bold=True,
    )
    heading.paragraph_format.space_after = Pt(4)

    for line in (document.get("description_text") or "").split("\n"):
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(2)
        is_heading = line.strip().endswith(":") and line.strip().isupper()
        _add_run(paragraph, line, size=10, bold=is_heading)

    # ---------- Орысша нұсқа ----------
    if document.get("description_text_ru"):
        divider = doc.add_paragraph()
        _bottom_border(divider, size=6, color="CCCCCC")
        divider.paragraph_format.space_before = Pt(8)
        divider.paragraph_format.space_after = Pt(8)

        heading_ru = doc.add_paragraph()
        _add_run(
            heading_ru,
            "Текст проекта заявки (рус.)" if state["draft"] else "Текст заявки (рус.)",
            size=10.5, bold=True,
        )
        heading_ru.paragraph_format.space_after = Pt(4)

        for line in document["description_text_ru"].split("\n"):
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(2)
            _add_run(paragraph, line, size=9.5, color=RGBColor(0x33, 0x33, 0x33))

    disclaimer = doc.add_paragraph()
    disclaimer.paragraph_format.space_before = Pt(8)
    disclaimer.paragraph_format.space_after = Pt(8)
    _add_run(
        disclaimer,
        "Маңызды: ЖИ-модельдің бағасы алдын ала сипатта болады және инженерлік "
        "тексеруді, нормативтік қорытындыны немесе жауапты органның шешімін "
        "алмастырмайды.",
        size=8.5, color=GREY, italic=True,
    )

    # ---------- Қосымшалар тізімі ----------
    attachments: list[str] = []
    if document.get("photo_file"):
        photo_hash = _first_value(document, "photo_sha256", "image_sha256", "evidence_sha256")
        item = f"Фотофиксация: {document['photo_file']}"
        if photo_hash:
            item += f" · SHA-256: {photo_hash}"
        attachments.append(item)
    if document.get("video_file"):
        video_hash = _first_value(document, "video_sha256", "clip_sha256")
        item = (
            f"Видеожазба: {document['video_file']} · "
            f"ұзақтығы {document.get('video_seconds', 0)} секунд"
        )
        if video_hash:
            item += f" · SHA-256: {video_hash}"
        attachments.append(item)
    if document.get("after_photo_file"):
        attachments.append(f"Орындалғаннан кейінгі фото: {document['after_photo_file']}")

    if attachments:
        attachments_heading = doc.add_paragraph()
        _add_run(attachments_heading, "Қосымшалар тізімі", size=10.5, bold=True)
        attachments_heading.paragraph_format.space_after = Pt(3)
        for item in attachments:
            paragraph = doc.add_paragraph(style="List Number")
            paragraph.paragraph_format.space_after = Pt(2)
            _add_run(paragraph, item, size=9)

    # ---------- Дәлел ----------
    photo_name = document.get("photo_file")
    if photo_name:
        photo_path = media_dir / document["event_id"] / photo_name
        if photo_path.exists():
            doc.add_page_break()

            evidence_heading = doc.add_paragraph()
            _add_run(evidence_heading, "Қоса берілген дәлел", size=11, bold=True)
            evidence_heading.paragraph_format.space_after = Pt(6)

            try:
                doc.add_picture(str(photo_path), width=Cm(16.0))
                caption = doc.add_paragraph()
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _add_run(
                    caption,
                    f"Ақау сәтіндегі фотофиксация · {document.get('timestamp_human', '')} · "
                    f"{document.get('address_text', '')}",
                    size=8, color=GREY, italic=True,
                )
            except Exception as exc:
                log.warning("Фотосурет қосылмады (%s): %s", photo_path, exc)

            if document.get("video_file"):
                video_note = doc.add_paragraph()
                _add_run(
                    video_note,
                    f"Видео дәлел: {document.get('video_seconds', 0)} секунд "
                    f"(электрондық нұсқада қоса беріледі, файл: {document['video_file']}).",
                    size=9, color=GREY,
                )

            after_name = document.get("after_photo_file")
            if after_name:
                after_path = media_dir / document["event_id"] / after_name
                if after_path.exists():
                    after_heading = doc.add_paragraph()
                    _add_run(after_heading, "Жөндеуден кейін", size=11, bold=True)
                    try:
                        doc.add_picture(str(after_path), width=Cm(16.0))
                    except Exception:
                        pass

    # ---------- Жолданым берушінің деректері (ӘРПК 63-бап) ----------
    # ҚР Әкімшілік рәсімдік-процестік кодексінің 63-бабының 2-тармағы
    # жазбаша жолданымда МІНДЕТТІ реквизиттерді талап етеді. Автоматты
    # жүйе оларды өзі толтыра алмайды — оны жіберуші адам толтырады.
    # Сондықтан құжатта дайын орын қалдырамыз.
    doc.add_paragraph().paragraph_format.space_after = Pt(10)

    requisites_head = doc.add_paragraph()
    _add_run(requisites_head, "Жолданым берушінің деректері", size=10.5, bold=True)
    _add_run(
        requisites_head,
        "   (ӘРПК 63-бабының 2-тармағы бойынша міндетті)",
        size=8, color=GREY,
    )
    requisites_head.paragraph_format.space_after = Pt(4)

    requisites = doc.add_table(rows=0, cols=2)
    requisites.style = "Table Grid"
    requisites.autofit = False
    for label in (
        "Тегі, аты, әкесінің аты",
        "ЖСН (болған жағдайда)",
        "Пошталық мекенжайы",
        "Байланыс телефоны",
        "Қолы / ЭЦҚ",
        "Күні",
    ):
        cells = requisites.add_row().cells
        cells[0].width = Cm(5.4)
        cells[1].width = Cm(10.8)
        _add_run(cells[0].paragraphs[0], label, size=9, bold=True)
        _set_cell_background(cells[0], "F2F2F3")
        _add_run(cells[1].paragraphs[0], " ", size=9)

    legal = doc.add_paragraph()
    legal.paragraph_format.space_before = Pt(8)
    _add_run(
        legal,
        "Құқықтық негіз: ҚР Әкімшілік рәсімдік-процестік кодексі "
        "(29.06.2020 № 350-VI). Хабарлама 4-баптың 37) тармақшасына сәйкес "
        "берілген және 87-89-баптар бойынша оңайлатылған әкімшілік рәсімде "
        "қаралады. Жолданым бойынша әкімшілік рәсімнің мерзімі — ол "
        "ТІРКЕЛГЕН күннен бастап 15 жұмыс күні (76-бап 1-тармағы); мерзім "
        "әкімшілік орган басшысының уәжді шешімімен екі айдан аспайтын "
        "мерзімге ұзартылуы мүмкін (76-бап 3-тармағы). Құзыретке жатпаса, "
        "жолданым үш жұмыс күні ішінде уәкілетті органға қайта жолданады "
        "(65-бап).",
        size=8, color=GREY,
    )

    # ---------- Қолтаңба ----------
    doc.add_paragraph().paragraph_format.space_after = Pt(14)

    signature = doc.add_paragraph()
    _add_run(signature, "Оқиға карточкасын қалыптастырған: ", size=9.5)
    _add_run(signature, "AIQYN Vision (автоматты мониторинг)", size=9.5, bold=True)
    if review:
        _add_run(signature, f"\nОператорлық тексеру: {review['actor']}", size=9.5)
        if review.get("at"):
            _add_run(signature, f" · {review['at']}", size=9.5)
        if review.get("decision") == "rejected":
            _add_run(signature, " · оқиға расталмады", size=9.5, bold=True)
    else:
        _add_run(
            signature,
            "\nОператорлық тексеру: жүргізілмеген немесе жүйе тарихымен расталмаған",
            size=9.5, bold=True, color=ACCENT,
        )
    if ticket_id:
        _add_run(signature, f"\nЕКЦ 109 тіркеу нөмірі: {ticket_id}", size=9.5, bold=True)
    elif document.get("sent_at"):
        _add_run(
            signature,
            "\nЖіберілген уақыты: " + _display_time(document.get("sent_at"))
            + " · тіркеу нөмірі күтілуде",
            size=9.5,
        )

    footer = doc.add_paragraph()
    _bottom_border(footer, size=6, color="CCCCCC")
    footer.paragraph_format.space_before = Pt(14)

    note = doc.add_paragraph()
    if ticket_id:
        state_note = f"ЕКЦ 109 жүйесіндегі тіркеу нөмірі: {ticket_id}."
    elif state["draft"]:
        state_note = "ӨТІНІМ ЖОБАСЫ — ресми арнаға жіберілмеген."
    else:
        state_note = "Ресми арнаға жіберілген; сыртқы тіркеу нөмірі көрсетілмеген."

    review_note = (
        f"Операторлық тексеру: {review['actor']}"
        + (f", {review['at']}" if review and review.get("at") else "")
        + "."
        if review
        else "Оператор тексеруі құжат деректерімен расталмаған."
    )
    _add_run(
        note,
        f"{state_note} {review_note} "
        "ЖИ бағасы инженерлік тексеруді немесе жауапты органның шешімін "
        "алмастырмайды. Жүйе тұлғаны немесе көлік нөмірін танымайды; тек "
        "инфрақұрылым ақауының белгілерін тіркейді.\n"
        f"AIQYN оқиға ID: {document.get('event_id', '')}",
        size=7.5, color=GREY,
    )

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def safe_filename(event_id: str) -> str:
    return f"AIQYN_{event_id.replace('AIQYN-', '')}.docx"
