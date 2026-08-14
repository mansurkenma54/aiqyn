"""Толыққанды ресми ҚҰЖАТ құрастыру.

МАҢЫЗДЫ АРХИТЕКТУРАЛЫҚ ЕРЕЖЕ:
    Құжат ТОЛЫҚ осы программада дайындалады. Сайт (AIQYN Portal) оны
    өзгертпейді, толықтырмайды, ешнәрсе есептемейді — тек қабылдайды,
    картаға шығарады, операторға көрсетеді және растаудан кейін жібереді.

Сондықтан мұнда бәрі болуы керек: ресми санат атауы, толық мекенжай,
карта сілтемесі, байланысты мәтін (қазақша + орысша), ауырлық деңгейі,
дәлел файлдары.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from . import categories
from .config import Config
from .geo import format_coord, google_maps_link, osm_link, two_gis_link
from .gps import GpsFix

APP_VERSION = "AIQYN Vision 1.0"
SHYMKENT_TZ = timezone(timedelta(hours=5))   # UTC+5


def new_event_id() -> str:
    """Оқиға идентификаторы: AIQYN-20260813-184216-a1b2c3"""
    stamp = datetime.now(SHYMKENT_TZ).strftime("%Y%m%d-%H%M%S")
    return f"AIQYN-{stamp}-{uuid.uuid4().hex[:6]}"


def _format_datetime(ts: float) -> tuple[str, str]:
    """(ISO 8601, адамға оқуға ыңғайлы) форматтары."""
    moment = datetime.fromtimestamp(ts, SHYMKENT_TZ)
    return moment.isoformat(), moment.strftime("%d.%m.%Y %H:%M:%S")


def _description_kk(
    category: categories.Category,
    address: str,
    coords: str,
    human_time: str,
    severity: str,
    confidence: float,
    marked_on_road: bool,
    has_video: bool,
    video_sec: float,
    gps_trusted: bool,
    gps_accuracy_m: float = 0.0,
) -> str:
    lines = [
        f"AIQYN автоматтандырылған мониторинг жүйесі {human_time} кезінде "
        f"көрсетілген учаскеде «{category.kk}» белгілерін тіркеді.",
        "",
        f"Оқиға орны: {address}",
        f"Картадағы координаттар: {coords}",
    ]

    if gps_accuracy_m and gps_accuracy_m > 0:
        lines.append(f"Геопозицияның болжамды дәлдігі: ±{gps_accuracy_m:.0f} м")

    lines.extend([
        f"Алдын ала қауіп деңгейі: {categories.SEVERITY_KK[severity]} "
        "(автоматты бағалау, маманның тексеруін талап етеді)",
        f"AI-модельдің анықтау сенімділігі: {confidence * 100:.0f}% "
        "(бұл көрсеткіш ақаудың қауіп деңгейін білдірмейді)",
    ])

    if not marked_on_road:
        lines.append(
            "Ескерту: қолжетімді тізілім бойынша оқиға белсенді жол жұмыстары "
            "учаскесімен автоматты түрде сәйкестендірілмеді. Бұл белгі "
            "операторлық нақтылауды қажет етеді."
        )

    if not gps_trusted:
        lines.append(
            "Ескерту: GPS дерегі тұрақсыз. Координаттар шамамен көрсетілді, "
            "картадағы орнын оператор нақтылауы тиіс."
        )

    lines.append("")
    if has_video:
        lines.append(
            f"Дәлел ретінде ақау сәтіндегі фотосурет және {video_sec:.0f} секундтық "
            f"видеожазба қоса беріледі."
        )
    else:
        lines.append("Дәлел ретінде ақау сәтіндегі фотосурет қоса беріледі.")

    lines.append("")
    lines.append(
        "Көрсетілген учаскені жергілікті жерде тексеріп, ақау расталған жағдайда "
        "оны жою жөніндегі шараларды қабылдауды, өтінімнің тіркеу нөмірін, "
        "жауапты орындаушыны, жоспарлы орындалу мерзімін және орындалу "
        "нәтижесін хабарлауды сұраймыз."
    )
    return "\n".join(lines)


def _description_ru(
    category: categories.Category,
    address: str,
    coords: str,
    human_time: str,
    severity: str,
    confidence: float,
    marked_on_road: bool,
    has_video: bool,
    video_sec: float,
    gps_trusted: bool,
    gps_accuracy_m: float = 0.0,
) -> str:
    lines = [
        f"Автоматизированная система мониторинга AIQYN {human_time} "
        f"зафиксировала на указанном участке признаки «{category.ru}».",
        "",
        f"Место события: {address}",
        f"Координаты на карте: {coords}",
    ]

    if gps_accuracy_m and gps_accuracy_m > 0:
        lines.append(f"Ориентировочная точность геопозиции: ±{gps_accuracy_m:.0f} м")

    lines.extend([
        f"Предварительный уровень риска: {categories.SEVERITY_RU[severity]} "
        "(автоматическая оценка, требуется проверка специалистом)",
        f"Уверенность AI-модели: {confidence * 100:.0f}% "
        "(этот показатель не является оценкой опасности дефекта)",
    ])

    if not marked_on_road:
        lines.append(
            "Примечание: по доступному реестру событие автоматически не "
            "сопоставлено с действующим участком дорожных работ. Признак "
            "требует уточнения оператором."
        )

    if not gps_trusted:
        lines.append(
            "Примечание: данные GPS нестабильны. Координаты указаны "
            "приблизительно; положение на карте должен уточнить оператор."
        )

    lines.append("")
    if has_video:
        lines.append(
            f"В качестве доказательства прилагаются фотоснимок и видеозапись "
            f"продолжительностью {video_sec:.0f} секунд."
        )
    else:
        lines.append("В качестве доказательства прилагается фотоснимок.")

    lines.append("")
    lines.append(
        "Просим обследовать указанный участок и, при подтверждении дефекта, "
        "принять меры по его устранению, а также сообщить регистрационный "
        "номер заявки, ответственного исполнителя, плановый срок и результат "
        "исполнения."
    )
    return "\n".join(lines)


def build_document(
    cfg: Config,
    event_id: str,
    class_key: str,
    confidence: float,
    area_frac: float,
    fix: GpsFix,
    address: dict,
    detected_at: float,
    detector_name: str,
    video_seconds: float = 0.0,
    has_video: bool = True,
    marked_on_road: bool = False,
    extra: Optional[dict] = None,
    ai: Optional[dict] = None,
) -> dict:
    """Сайтқа жіберілетін ТОЛЫҚ ДАЙЫН құжат."""

    category = categories.get(class_key)
    org = categories.org_for(class_key)
    severity = categories.severity_for(class_key, confidence, area_frac)

    iso_time, human_time = _format_datetime(detected_at)
    coords = format_coord(fix.lat, fix.lon)
    address_text = address.get("address_text", "")

    common = dict(
        category=category,
        address=address_text,
        coords=coords,
        human_time=human_time,
        severity=severity,
        confidence=confidence,
        marked_on_road=marked_on_road,
        has_video=has_video,
        video_sec=video_seconds,
        gps_trusted=fix.trusted,
        gps_accuracy_m=fix.accuracy_m,
    )

    description_kk = _description_kk(**common)
    description_ru = _description_ru(**common)

    # Екінші саты (ИИ көру моделі) — инженерлік талдау бөлімі.
    # Бұл ресми хатқа нақтылық қосады: не көрініп тұр, өлшемі қандай,
    # қандай қауіп бар, қандай шара керек.
    if ai and ai.get("is_real"):
        ai_conf = float(ai.get("confidence") or 0.0)

        analysis_kk = ["", "ҚОСЫМША AI-МОДЕЛЬ БАҒАСЫ (алдын ала):"]
        if ai.get("description_kk"):
            analysis_kk.append(ai["description_kk"])
        if ai.get("size_estimate"):
            analysis_kk.append(f"Шамамен өлшемі: {ai['size_estimate']}")
        if ai.get("location_detail"):
            analysis_kk.append(f"Жолдағы орны: {ai['location_detail']}")
        if ai.get("danger"):
            analysis_kk.append(f"Ықтимал қозғалыс қаупі: {ai['danger']}")
        if ai.get("recommended_action"):
            urgency = ai.get("urgency_days") or 0
            deadline = (
                f" (бағдарлық мерзім: {urgency} күн; нормативтік SLA емес)"
                if urgency else ""
            )
            analysis_kk.append(
                f"AI ұсынған ықтимал шара: {ai['recommended_action']}{deadline}"
            )
        analysis_kk.append(
            f"AI бағасының сенімділігі: {ai_conf * 100:.0f}% "
            f"(модель: {ai.get('model', '—')})"
        )
        analysis_kk.append(
            "Ескерту: AI бағасы инженерлік тексеруді, нормативтік қорытындыны "
            "немесе жауапты органның шешімін алмастырмайды."
        )
        description_kk += "\n" + "\n".join(analysis_kk)

        # Орысша бөлімге тек орысша мәтінді қосамыз: recommended_action
        # қазақша келеді, сондықтан оны бұл жерге қоспаймыз —
        # description_ru ішінде ұсыныс әлдеқашан айтылған.
        analysis_ru = ["", "ДОПОЛНИТЕЛЬНАЯ ОЦЕНКА AI-МОДЕЛИ (предварительно):"]
        if ai.get("description_ru"):
            analysis_ru.append(ai["description_ru"])
        if ai.get("urgency_days"):
            analysis_ru.append(
                f"Ориентировочный срок по оценке AI: {ai['urgency_days']} дн. "
                "(не является нормативным SLA)"
            )
        analysis_ru.append(f"Уверенность AI-оценки: {ai_conf * 100:.0f}%")
        analysis_ru.append(
            "Примечание: AI-оценка не заменяет инженерное обследование, "
            "нормативное заключение или решение ответственного органа."
        )
        description_ru += "\n" + "\n".join(analysis_ru)

    return {
        # --- негізгі (промпт №1-де көрсетілген өрістер) ---
        "event_id": event_id,
        "defect_type_official": category.kk,
        "defect_type_official_ru": category.ru,
        "description_text": description_kk,
        "description_text_ru": description_ru,
        "address_text": address_text,
        "map_link": google_maps_link(fix.lat, fix.lon),
        "lat": round(fix.lat, 6),
        "lon": round(fix.lon, 6),
        "marked_on_road": marked_on_road,
        "severity": severity,
        "confidence": round(confidence, 3),
        "timestamp": iso_time,

        # --- қосымша (сайт көрсетуі үшін пайдалы) ---
        "class_key": class_key,
        "severity_kk": categories.SEVERITY_KK[severity],
        "severity_ru": categories.SEVERITY_RU[severity],
        "timestamp_human": human_time,
        "coords_text": coords,
        "map_link_osm": osm_link(fix.lat, fix.lon),
        "map_link_2gis": two_gis_link(fix.lat, fix.lon),
        "responsible_org": org["kk"],
        "responsible_org_ru": org["ru"],
        "responsible_email": org["email"],

        # --- техникалық (аудит үшін) ---
        "detector": detector_name,
        "gps_source": fix.source,
        "gps_trusted": fix.trusted,
        "gps_accuracy_m": round(fix.accuracy_m, 1),
        "speed_mps": round(fix.speed_mps, 2),
        "video_seconds": round(video_seconds, 1),
        "source_type": cfg.source,
        "app_version": APP_VERSION,

        # --- екінші саты (ИИ көру моделі) ---
        "ai_verified": bool(ai.get("is_real")) if ai else False,
        "ai_confidence": round(float(ai.get("confidence") or 0.0), 3) if ai else 0.0,
        "ai_note": (ai.get("reason") or "")[:300] if ai else "",
        "ai_model": ai.get("model", "") if ai else "",
        "ai_size": ai.get("size_estimate", "") if ai else "",
        "ai_location": ai.get("location_detail", "") if ai else "",
        "ai_danger": ai.get("danger", "") if ai else "",
        "ai_action": ai.get("recommended_action", "") if ai else "",
        "ai_urgency_days": int(ai.get("urgency_days") or 0) if ai else 0,

        "extra": extra or {},
    }
