"""Расталған өтінімді ЖАУАПТЫ ОРГАНҒА ШЫНЫМЕН жеткізу.

ТЗ талабы: «Интеграция с ЕКЦ 109 для автоматического формирования заявок
на ремонт».

Мәселе: ресми iKOMEK 109-дың ашық API-і жоқ. Сондықтан жүйе бір арнаға
байланып қалмауы керек — өтінім НЕ АРҚЫЛЫ болса да жетуі тиіс. Осында
төрт арна бар, олар бірінен соң бірі емес, ҚАТАР жүреді:

    1. ekc109   — 109 адаптері. AIQYN_109_URL қойылса, нағыз HTTP
                  сұрауы кетеді. Қойылмаса — жергілікті тікет журналы
                  (аудит ізі) жазылады да, «эндпойнт жоқ» деп ашық
                  көрсетіледі. Жалған «жіберілді» ешқашан жазылмайды.
    2. email    — ресми хат: .docx құжаты мен фотосы қоса тіркеледі.
                  Екі көлік: SMTP (Gmail) және HTTP API (Resend/Brevo).
                  HTTP нұсқасы Vercel-де де жұмыс істейді — serverless
                  ортада 587-порт жиі жабық.
    3. whatsapp — wa.me сілтемесі әрқашан жасалады (оператор басып
                  жібереді — хат шынымен жетеді). WhatsApp Cloud API
                  бапталса, автоматты да кетеді.
    4. telegram — жедел хабарлама (portal/notify.py).

ЕРЕЖЕ: әр арна өз квитанциясын қайтарады. Арна бапталмаса — «ok: false,
configured: false». Есепте де, интерфейсте де солай көрінеді.

Баптау — .env.example ішінде қадаммен жазылған.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import smtplib
import ssl
import urllib.parse
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

log = logging.getLogger("portal.delivery")

TIMEOUT = float(os.getenv("AIQYN_DELIVERY_TIMEOUT", "25"))


def _env(name: str, default: str = "") -> str:
    # Кілттер ӘР ЖОЛЫ оқылады: .env бұл модульден кейін жүктелуі мүмкін
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: str = "false") -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "on")


def _portal_url() -> str:
    """Хаттағы сілтемелердің түбірі.

    localhost болмауы керек — хатты АЛУШЫ оны аша алмайды. Сондықтан
    кезек: .env-тегі мекенжай → Vercel берген домен → жергілікті.
    """
    explicit = _env("AIQYN_PORTAL_PUBLIC_URL")
    if explicit:
        return explicit.rstrip("/")
    vercel = _env("VERCEL_PROJECT_PRODUCTION_URL") or _env("VERCEL_URL")
    if vercel:
        return f"https://{vercel}".rstrip("/")
    return "http://localhost:8000"


# ============================================================
#  Кімге барады
# ============================================================

# Демо дерекқорда аймақтарға шартты мекенжайлар қойылған
# (demo+roads@aiqyn.local). Олар нақты пошта емес — хат сол жерге кетсе,
# «жіберілді» деп жазылады да, ешкім алмайды. Сондықтан мұндай домендер
# мекенжай ЖОҚ деп саналады.
_FAKE_DOMAINS = (".local", ".invalid", ".test", ".example",
                 "example.com", "example.org", "example.kz", "localhost")


def _usable_email(value: object) -> str:
    """Хат шынымен жете алатын мекенжай ғана қайтарылады."""
    text = str(value or "").strip()
    if "@" not in text or " " in text:
        return ""
    domain = text.rsplit("@", 1)[1].lower()
    if any(domain == bad or domain.endswith(bad) for bad in _FAKE_DOMAINS):
        return ""
    return text


def resolve_recipients(document: dict) -> dict:
    """Өтінім кімге кететінін анықтау.

    Кезек:
      1. AIQYN_DELIVERY_FORCE_TO — бәрін басып озады (демо кезінде қауіпсіз:
         барлық өтінім бір поштаға түседі, нақты мекемеге кетпейді);
      2. құжаттың өз жауапты ұйымының поштасы (аймақ бойынша);
      3. AIQYN_DELIVERY_TO — әдепкі мекенжай;
      4. жіберушінің өзі.
    Ойдан мекенжай ешқашан құрылмайды.
    """
    forced = _usable_email(_env("AIQYN_DELIVERY_FORCE_TO"))
    from_zone = _usable_email(document.get("responsible_email"))
    fallback = _usable_email(_env("AIQYN_DELIVERY_TO"))
    sender = _usable_email(_env("AIQYN_SMTP_FROM")) or _usable_email(_env("AIQYN_SMTP_USER"))

    email, source = "", "жоқ"
    for candidate, label in ((forced, "AIQYN_DELIVERY_FORCE_TO"),
                             (from_zone, "аймақтың жауапты ұйымы"),
                             (fallback, "AIQYN_DELIVERY_TO"),
                             (sender, "жіберушінің өзі")):
        if candidate:
            email, source = candidate, label
            break

    return {
        "org": document.get("responsible_org") or "ЕКЦ 109 диспетчерлік кезегі",
        "org_ru": document.get("responsible_org_ru") or "ЕКЦ 109",
        "email": email,
        "email_source": source,
        "email_cc": [x.strip() for x in _env("AIQYN_DELIVERY_CC").split(",")
                     if _usable_email(x)],
        "whatsapp": _env("AIQYN_DELIVERY_WHATSAPP"),
    }


# ============================================================
#  Хаттың мәтіні — үш арнаға да ортақ
# ============================================================

def subject_of(document: dict) -> str:
    return (
        f"[AIQYN] Жол ақауы: {document.get('defect_type_official') or 'ақау'} — "
        f"{document.get('address_text') or 'мекенжай көрсетілмеген'}"
    )


def body_of(document: dict, *, plain: bool = True) -> str:
    """Ресми хаттың мәтіні: қазақша + орысша + дәлел сілтемелері."""
    portal = _portal_url().rstrip("/")
    event_id = document.get("event_id", "")

    lat, lon = document.get("lat"), document.get("lon")
    coords = f"{lat:.6f}, {lon:.6f}" if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else "—"
    map_link = document.get("map_link") or (
        f"https://www.google.com/maps?q={lat},{lon}" if lat is not None and lon is not None else ""
    )

    rows = [
        ("Оқиға нөмірі", event_id),
        ("Ақау түрі", document.get("defect_type_official") or "—"),
        ("Мекенжай", document.get("address_text") or "—"),
        ("Координата", coords),
        ("Қауіптілігі", document.get("severity") or "—"),
        ("Тіркелген уақыты", document.get("timestamp_human") or "—"),
        ("Жауапты ұйым", document.get("responsible_org") or "—"),
    ]

    binding = document.get("spatial") or {}
    if binding.get("bound"):
        rows.append(("Жол сегменті", binding.get("spatial_id") or "—"))
        if binding.get("road_name"):
            rows.append(("Көше (OSM)", binding["road_name"]))

    lines = [
        "AIQYN ZHOL — автоматтандырылған жол мониторингі",
        "Шымкент қаласының жол инфрақұрылымын бақылау жүйесі",
        "",
        "ӨТІНІМ / ЗАЯВКА",
        "",
    ]
    lines += [f"{key}: {value}" for key, value in rows]
    lines += [
        "",
        "--- Сипаттама ---",
        document.get("description_text") or "",
        "",
        "--- Описание (RU) ---",
        document.get("description_text_ru") or "",
        "",
        "--- Дәлел / Доказательства ---",
    ]
    if map_link:
        lines.append(f"Картада: {map_link}")
    if event_id:
        lines.append(f"Оқиға парағы: {portal}/portal?event={event_id}")
        lines.append(f"Ресми құжат (.docx): {portal}/api/documents/{event_id}/docx")
    lines += [
        "",
        "Ақау автоматты түрде табылды, оператор растады. Фото, видео және "
        "уақыт белгісі жүйеде сақтаулы.",
        "Жүйе тұлғаны да, көлік нөмірін де танымайды.",
    ]
    text = "\n".join(lines)
    return text if plain else text.replace("\n", "<br>")


# ============================================================
#  1. ЕКЦ 109 адаптері
# ============================================================

def ticket_payload(document: dict, ticket_id: str) -> dict:
    """109 форматындағы өтінім. Ресми эндпойнт ашылғанда өзгермейді."""
    binding = document.get("spatial") or {}
    return {
        "ticket_source": "AIQYN",
        "ticket_id": ticket_id,
        "event_id": document.get("event_id"),
        "category": document.get("defect_type_official"),
        "category_ru": document.get("defect_type_official_ru"),
        "address": document.get("address_text"),
        "lat": document.get("lat"),
        "lon": document.get("lon"),
        "original_lat": document.get("original_lat"),
        "original_lon": document.get("original_lon"),
        "original_address": document.get("original_address_text"),
        "location_corrected": bool(document.get("location_corrected")),
        "location_correction_note": document.get("location_correction_note"),
        "severity": document.get("severity"),
        "confidence": document.get("confidence"),
        "ai_verified": bool(document.get("ai_verified")),
        "description": document.get("description_text"),
        "description_ru": document.get("description_text_ru"),
        "responsible_org": document.get("responsible_org"),
        # Кеңістіктік байланыс — SDR / ЦС ГГ НИПД өрістері
        "spatial_id": binding.get("spatial_id"),
        "osm_way_id": binding.get("osm_way_id"),
        "road_name": binding.get("road_name"),
        "road_class": binding.get("road_class"),
        "created_at": document.get("created_at"),
    }


def send_ekc109(document: dict, ticket_id: str) -> dict:
    """109-ға жіберу. URL қойылмаса — жалған «жіберілді» жазылмайды."""
    url = _env("AIQYN_109_URL")
    if not url:
        return {
            "ok": False, "configured": False,
            "detail": "Ресми 109 эндпойнті берілмеген — өтінім жергілікті "
                      "тікет журналына жазылды (аудит ізі сақталды)",
        }

    import requests

    headers = {"Content-Type": "application/json"}
    token = _env("AIQYN_109_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.post(
            url, json=ticket_payload(document, ticket_id),
            headers=headers, timeout=TIMEOUT,
        )
        if response.status_code >= 400:
            return {"ok": False, "configured": True,
                    "detail": f"109 қатесі {response.status_code}: {response.text[:200]}"}
        try:
            data = response.json()
        except ValueError:
            data = {}
        external = data.get("ticket_id") or data.get("id") or ticket_id
        return {"ok": True, "configured": True,
                "external_ticket_id": str(external),
                "detail": f"109 қабылдады: {external}"}
    except Exception as exc:                      # noqa: BLE001
        return {"ok": False, "configured": True, "detail": f"109-ға жетпеді: {exc}"}


# ============================================================
#  2. Email — екі көлік
# ============================================================

def mail_provider() -> str:
    """Қай көлік қолданылады: smtp | resend | brevo | none."""
    explicit = _env("AIQYN_MAIL_PROVIDER").lower()
    if explicit in ("smtp", "resend", "brevo", "none"):
        return explicit
    if _env("AIQYN_RESEND_KEY"):
        return "resend"
    if _env("AIQYN_BREVO_KEY"):
        return "brevo"
    if _env("AIQYN_SMTP_HOST") and _env("AIQYN_SMTP_USER"):
        return "smtp"
    return "none"


# Хаттың жалпы шегі. Gmail 25 МБ, Brevo API ~10 МБ көтереді, ал base64
# көлемді үштен бірге өсіреді. Сондықтан 7 МБ — қауіпсіз шек. Шектен
# асқан файл тіркелмейді, оның орнына хатта сілтемесі қалады: өтінім
# «тым үлкен» деген себеппен жоғалып кетпеуі керек.
MAX_TOTAL_BYTES = 7 * 1024 * 1024


def _attachments(document: dict, media_dir: Path) -> tuple[list[tuple[str, str, bytes]], list[str]]:
    """Ресми .docx + фото + (сыйса) видео.

    Қайтарады: тіркемелер тізімі және сыймай қалғандардың аттары.
    """
    out: list[tuple[str, str, bytes]] = []
    skipped: list[str] = []
    event_id = document.get("event_id") or "event"
    # docgen қалтамен Path ретінде жұмыс істейді. Мұнда жол жол (str)
    # болып келсе, ресми құжат үнсіз тіркелмей қалатын — ең маңызды
    # тіркеме сол еді.
    media_dir = Path(media_dir)

    try:
        from .docgen import build_docx, safe_filename
        buffer = build_docx(document, media_dir)
        out.append((
            safe_filename(event_id),          # .docx кеңейтімі осында тұр
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            buffer.getvalue(),
        ))
    except Exception as exc:                      # noqa: BLE001
        log.warning("Құжат тіркелмеді: %s", exc)

    used = sum(len(data) for _, _, data in out)
    folder = Path(media_dir) / event_id
    # Фото бірінші: ол дәлел ретінде видеодан маңыздырақ әрі жеңіл
    for key, mime in (("photo_file", "image/jpeg"), ("video_file", "video/mp4")):
        name = document.get(key)
        if not name:
            continue
        path = folder / name
        try:
            if not path.exists():
                continue
            size = path.stat().st_size
            if used + size > MAX_TOTAL_BYTES:
                skipped.append(name)
                continue
            out.append((name, mime, path.read_bytes()))
            used += size
        except OSError as exc:
            log.warning("Тіркеме оқылмады (%s): %s", path, exc)
    return out, skipped


def _send_smtp(to: str, cc: list[str], subject: str, body: str,
               files: list[tuple[str, str, bytes]]) -> dict:
    host = _env("AIQYN_SMTP_HOST")
    port = int(_env("AIQYN_SMTP_PORT", "587") or 587)
    user = _env("AIQYN_SMTP_USER")
    password = _env("AIQYN_SMTP_PASSWORD")
    sender = _env("AIQYN_SMTP_FROM") or user

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"AIQYN ZHOL <{sender}>"
    message["To"] = to
    if cc:
        message["Cc"] = ", ".join(cc)
    message.set_content(body)
    for name, mime, data in files:
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=TIMEOUT) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(message)
    return {"ok": True, "configured": True, "transport": "smtp",
            "detail": f"SMTP арқылы жіберілді: {to}"}


def _send_http_mail(provider: str, to: str, cc: list[str], subject: str,
                    body: str, files: list[tuple[str, str, bytes]]) -> dict:
    """Resend / Brevo — Vercel сияқты serverless ортада SMTP жабық болғанда."""
    import requests

    sender = _env("AIQYN_MAIL_FROM") or _env("AIQYN_SMTP_FROM") or _env("AIQYN_SMTP_USER")
    attachments = [{"name": n, "b64": base64.b64encode(d).decode()} for n, _, d in files]

    if provider == "resend":
        payload = {
            "from": f"AIQYN ZHOL <{sender}>",
            "to": [to], "subject": subject, "text": body,
            "attachments": [{"filename": a["name"], "content": a["b64"]} for a in attachments],
        }
        if cc:
            payload["cc"] = cc
        response = requests.post(
            _env("AIQYN_RESEND_URL", "https://api.resend.com/emails"), json=payload,
            headers={"Authorization": f"Bearer {_env('AIQYN_RESEND_KEY')}"},
            timeout=TIMEOUT,
        )
    else:  # brevo
        payload = {
            "sender": {"name": "AIQYN ZHOL", "email": sender},
            "to": [{"email": to}],
            "subject": subject, "textContent": body,
            "attachment": [{"name": a["name"], "content": a["b64"]} for a in attachments],
        }
        if cc:
            payload["cc"] = [{"email": x.strip()} for x in cc]
        response = requests.post(
            _env("AIQYN_BREVO_URL", "https://api.brevo.com/v3/smtp/email"), json=payload,
            headers={"api-key": _env("AIQYN_BREVO_KEY"), "accept": "application/json"},
            timeout=TIMEOUT,
        )

    if response.status_code >= 400:
        return {"ok": False, "configured": True, "transport": provider,
                "detail": f"{provider} қатесі {response.status_code}: {response.text[:200]}"}
    return {"ok": True, "configured": True, "transport": provider,
            "detail": f"{provider} арқылы жіберілді: {to}"}


def send_email(document: dict, media_dir: Path, recipients: dict) -> dict:
    provider = mail_provider()
    if provider == "none":
        return {"ok": False, "configured": False, "transport": "none",
                "detail": "Пошта бапталмаған (.env: AIQYN_SMTP_* немесе AIQYN_RESEND_KEY)"}

    to = recipients.get("email")
    if not to:
        return {"ok": False, "configured": True, "transport": provider,
                "detail": "Жарамды email мекенжайы жоқ — .env ішіне "
                          "AIQYN_DELIVERY_TO қойыңыз"}

    subject = subject_of(document)
    files, skipped = _attachments(document, media_dir)
    body = body_of(document)
    if skipped:
        # Сыймаған дәлел жоғалмауы керек — хатта оның сілтемесі қалады
        portal = _portal_url().rstrip("/")
        body += (
            f"\n\nЕСКЕРТПЕ: {', '.join(skipped)} — көлемі үлкен болғандықтан "
            f"хатқа тіркелмеді.\nЖүктеу: {portal}/portal?event={document.get('event_id')}"
        )

    try:
        if provider == "smtp":
            sent = _send_smtp(to, recipients.get("email_cc") or [], subject, body, files)
        else:
            sent = _send_http_mail(provider, to, recipients.get("email_cc") or [],
                                   subject, body, files)
        # Мекенжай қайдан алынғаны квитанцияда тұрсын: оператор хаттың
        # нақты мекемеге ме, әлде демо поштаға ма кеткенін көруі керек
        sent["to"] = to
        sent["to_source"] = recipients.get("email_source")
        sent["attachments"] = [name for name, _, _ in files]
        return sent
    except Exception as exc:                      # noqa: BLE001
        return {"ok": False, "configured": True, "transport": provider,
                "detail": f"Жіберілмеді: {exc}"}


# ============================================================
#  iKomek 109 — Шымкенттің ресми байланыс арналары
#  ------------------------------------------------------------
#  Дереккөз: Шымкент әкімдігінің «i-Shymkent» ситуациялық орталығы.
#  Ашық API жоқ, бірақ орталық ЖАРИЯ арналар арқылы өтініш қабылдайды.
#  Жол ақауы олардың «Қалалық жол инфрақұрылымдары» санатына жатады.
#
#  ЕСКЕРТУ: wa.me хабарламаға ФАЙЛ ТІРКЕЙ АЛМАЙДЫ — тек мәтін.
#  Сондықтан хатта ресми PDF-ке сілтеме тұрады.
# ============================================================

IKOMEK = {
    "name": "iKomek 109 · Шымкент",
    "phone": "109",
    "whatsapp": "77777109109",              # +7 7777 109 109
    "telegram_bot": "shymkent_109_bot",
    "instagram": "ishymkent109",
    "category": "Қалалық жол инфрақұрылымдары",
}


def ikomek_text(document: dict) -> str:
    """109 диспетчері оқитын өтініш мәтіні.

    Пішімі олардың сұрайтын ретімен: санат → мәселе → мекенжай →
    дәлел. Соңында жүйенің өзі ашық танылады: бұл автоматты
    мониторинг, оператор растаған.
    """
    portal = _portal_url().rstrip("/")
    event_id = document.get("event_id") or ""
    lat, lon = document.get("lat"), document.get("lon")

    lines = [
        "Өтініш · iKomek 109",
        "",
        f"Санат: {IKOMEK['category']}",
        f"Мәселе: {document.get('defect_type_official') or 'Жол жабынының ақауы'}",
        f"Мекенжай: {document.get('display_address_text') or document.get('address_text') or '—'}",
    ]
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        lines.append(f"Координата: {lat:.6f}, {lon:.6f}")
        lines.append(f"Картада: https://www.google.com/maps?q={lat},{lon}")
    lines += [
        f"Қауіптілігі: {document.get('severity_kk') or document.get('severity') or '—'}",
        f"Анықталған уақыты: {document.get('timestamp_human') or '—'}",
    ]

    note = (document.get("description_text") or "").strip()
    if note:
        first = note.split("\n\n")[0].strip()
        if first:
            lines += ["", first[:400]]

    if event_id:
        lines += [
            "",
            f"Ресми өтінім (PDF): {portal}/api/documents/{event_id}/pdf",
            f"Фото және дәлел: {portal}/portal?event={event_id}",
            f"Оқиға №: {event_id}",
        ]

    lines += [
        "",
        "Хабарлаған: AIQYN ZHOL — Шымкент жол инфрақұрылымын автоматты "
        "бақылау жүйесі. Ақауды жүйе тапты, кезекші оператор растады. "
        "Фото, координата және уақыт белгісі сақтаулы.",
    ]
    return "\n".join(lines)


def ikomek_links(document: dict) -> dict:
    """109-ға жүгінудің дайын сілтемелері."""
    text = ikomek_text(document)
    quoted = urllib.parse.quote(text)
    return {
        "whatsapp": f"https://wa.me/{IKOMEK['whatsapp']}?text={quoted}",
        "telegram": f"https://t.me/{IKOMEK['telegram_bot']}",
        "instagram": f"https://instagram.com/{IKOMEK['instagram']}",
        "phone": f"tel:{IKOMEK['phone']}",
        "text": text,
        "target": IKOMEK["name"],
        "whatsapp_number": "+7 7777 109 109",
    }


# ============================================================
#  3. WhatsApp
# ============================================================

def whatsapp_text(document: dict) -> str:
    """WhatsApp-қа арналған қысқа нұсқа — хат емес, хабарлама."""
    portal = _portal_url().rstrip("/")
    lat, lon = document.get("lat"), document.get("lon")
    lines = [
        "AIQYN ZHOL — жол ақауы туралы өтінім",
        "",
        f"Ақау: {document.get('defect_type_official') or '—'}",
        f"Мекенжай: {document.get('address_text') or '—'}",
        f"Қауіптілігі: {document.get('severity') or '—'}",
        f"Уақыты: {document.get('timestamp_human') or '—'}",
        f"Оқиға №: {document.get('event_id') or '—'}",
    ]
    if lat is not None and lon is not None:
        lines.append(f"Картада: https://www.google.com/maps?q={lat},{lon}")
    if document.get("event_id"):
        lines.append(f"Құжат: {portal}/api/documents/{document['event_id']}/docx")
    return "\n".join(lines)


def whatsapp_link(document: dict, phone: str = "") -> str:
    """wa.me сілтемесі: оператор басады — хабарлама шынымен кетеді."""
    phone = "".join(ch for ch in (phone or _env("AIQYN_DELIVERY_WHATSAPP")) if ch.isdigit())
    text = urllib.parse.quote(whatsapp_text(document))
    return f"https://wa.me/{phone}?text={text}" if phone else f"https://wa.me/?text={text}"


def send_whatsapp(document: dict, recipients: dict) -> dict:
    """Cloud API бапталса — автоматты. Болмаса — сілтеме дайын тұрады."""
    link = whatsapp_link(document, recipients.get("whatsapp", ""))
    token, phone_id = _env("AIQYN_WA_TOKEN"), _env("AIQYN_WA_PHONE_ID")
    to = "".join(ch for ch in (recipients.get("whatsapp") or "") if ch.isdigit())

    if not (token and phone_id and to):
        return {"ok": False, "configured": False, "link": link,
                "detail": "Cloud API бапталмаған — оператор сілтеме арқылы жібереді"}

    import requests
    try:
        response = requests.post(
            f"https://graph.facebook.com/v21.0/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"messaging_product": "whatsapp", "to": to,
                  "type": "text", "text": {"body": whatsapp_text(document)}},
            timeout=TIMEOUT,
        )
        if response.status_code >= 400:
            return {"ok": False, "configured": True, "link": link,
                    "detail": f"WhatsApp қатесі {response.status_code}: {response.text[:200]}"}
        return {"ok": True, "configured": True, "link": link,
                "detail": f"WhatsApp-қа жіберілді: +{to}"}
    except Exception as exc:                      # noqa: BLE001
        return {"ok": False, "configured": True, "link": link,
                "detail": f"WhatsApp-қа жетпеді: {exc}"}


# ============================================================
#  4. Telegram
# ============================================================

def send_telegram(document: dict, media_dir: Path) -> dict:
    from .notify import notifier
    if not notifier.enabled:
        return {"ok": False, "configured": False, "detail": "Telegram бапталмаған"}
    folder = Path(media_dir) / (document.get("event_id") or "")
    photo = folder / document["photo_file"] if document.get("photo_file") else None
    notifier.notify_new_document(document, photo)
    return {"ok": True, "configured": True, "detail": "Telegram-ға жіберілді"}


# ============================================================
#  Барлық арна — бір шақыруда
# ============================================================

def send(document: dict, media_dir: Path, ticket_id: str) -> dict:
    """Өтінімді барлық бапталған арнамен қатар жіберу.

    Қайтарады: әр арнаның квитанциясы + жалпы қорытынды. Ешбір арна
    құламайды — қате болса, ол сол арнаның `detail` өрісінде қалады.
    """
    recipients = resolve_recipients(document)
    channels = {
        "ekc109": send_ekc109(document, ticket_id),
        "email": send_email(document, media_dir, recipients),
        "whatsapp": send_whatsapp(document, recipients),
        "telegram": send_telegram(document, media_dir),
    }
    delivered = [name for name, r in channels.items() if r.get("ok")]
    return {
        "ok": bool(delivered),
        "delivered": delivered,
        "recipients": recipients,
        "channels": channels,
        "whatsapp_link": channels["whatsapp"].get("link", ""),
        # 109-ға жүгіну — кілтсіз жұмыс істейтін НАҚТЫ арна
        "ikomek": ikomek_links(document),
    }


def health() -> dict:
    """Қай арна дайын — баптауды тексеру (жіберусіз)."""
    provider = mail_provider()
    return {
        "ekc109": {
            "configured": bool(_env("AIQYN_109_URL")),
            "target": _env("AIQYN_109_URL") or "жергілікті тікет журналы (аудит)",
        },
        "email": {
            "configured": provider != "none",
            "transport": provider,
            "from": _env("AIQYN_MAIL_FROM") or _env("AIQYN_SMTP_FROM") or _env("AIQYN_SMTP_USER"),
            "to": _env("AIQYN_DELIVERY_TO") or "(құжаттың жауапты ұйымы)",
        },
        "whatsapp": {
            "configured": bool(_env("AIQYN_WA_TOKEN") and _env("AIQYN_WA_PHONE_ID")),
            "to": _env("AIQYN_DELIVERY_WHATSAPP") or "(сілтеме арқылы)",
        },
        "telegram": {"configured": bool(_env("AIQYN_TELEGRAM_TOKEN")
                                        and _env("AIQYN_TELEGRAM_CHAT_ID"))},
    }
