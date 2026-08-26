"""AIQYN Portal — оператор сайты.

РӨЛІ ҚАТАҢ ШЕКТЕЛГЕН:
    Бұл сайтта AI/ML логикасы ЖОҚ. Ол ешнәрсені классификацияламайды,
    мекенжай анықтамайды, мәтін генерацияламайды. Барлығын AIQYN Vision
    программасы бұрын жасаған. Сайт тек:
        1) дайын құжатты қабылдайды (өзгертпей),
        2) картада көрсетеді,
        3) операторға тексертеді,
        4) расталғанын жауапты органға жібереді,
        5) статусын бақылайды (before/after).

Іске қосу:
    python -m uvicorn portal.app:app --reload --port 8000
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import secrets
import shutil
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse,
)
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import db
from .notify import notifier

# .env файлын жүктеу (кілттер сол жерде)
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from vision.env import load_env
    load_env()
except Exception:
    pass

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-18s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("portal")

ROOT = Path(__file__).resolve().parent
# Медиа қалтасы сырттан берілуі мүмкін (Vercel-де ол демо қалтасы)
MEDIA_DIR = Path(os.getenv("AIQYN_MEDIA_DIR") or (ROOT / "media"))
# Serverless ортада жаңа құжат қабылданбайды: жүктелген файлды
# сақтайтын тұрақты диск жоқ
READ_ONLY = os.getenv("AIQYN_READ_ONLY", "").strip().lower() in ("1", "true", "yes")
STATIC_DIR = ROOT / "static"
TEMPLATES_DIR = ROOT / "templates"
TICKETS_LOG = ROOT / "mock_109_tickets.jsonl"

try:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass          # тек оқу режиміндегі орта (Vercel)
STATIC_DIR.mkdir(exist_ok=True)

ADMIN_USER = os.getenv("AIQYN_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("AIQYN_ADMIN_PASSWORD", "aiqyn2026")

# ЕСКЕРТУ: демо кезінде НАҚТЫ мемлекеттік мекеменің поштасы қойылмауы керек.
# Растаусыз ресми өтінім жіберу жалған шағым болып саналады.
SMTP_HOST = os.getenv("AIQYN_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("AIQYN_SMTP_PORT", "587"))
SMTP_USER = os.getenv("AIQYN_SMTP_USER", "")
SMTP_PASSWORD = os.getenv("AIQYN_SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("AIQYN_SMTP_FROM", "aiqyn@localhost")

app = FastAPI(
    title="AIQYN Portal",
    description="Қала инфрақұрылымы ақауларының ресми құжат айналымы",
    version="1.0.0",
)

security = HTTPBasic()


class ZonePayload(BaseModel):
    """Circle/polygon/polyline үшін бір ортақ, backward-compatible келісім."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    kind: str = "repair"
    shape: str = "circle"
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    radius_m: Optional[float] = Field(default=100, gt=0, le=10000)
    corridor_m: Optional[float] = Field(default=None, gt=0, le=1000)
    polygon: Optional[list[list[float]]] = None
    points: Optional[list[list[float]]] = None
    active: bool = True
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    note: str = Field(default="", max_length=2000)
    source_url: str = Field(default="", max_length=1000)
    external_ref: str = Field(default="", max_length=300)
    responsible_org: str = Field(default="", max_length=500)
    boundary_verified: bool = False

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if value not in db.ZONE_KINDS:
            raise ValueError(f"түрі {db.ZONE_KINDS} қатарында болуы керек")
        return value

    @field_validator("shape")
    @classmethod
    def validate_shape(cls, value: str) -> str:
        if value not in db.ZONE_SHAPES:
            raise ValueError(f"геометрия {db.ZONE_SHAPES} қатарында болуы керек")
        return value

    @field_validator("polygon", "points")
    @classmethod
    def validate_geometry(cls, value: Optional[list[list[float]]]) -> Optional[list[list[float]]]:
        if value is None:
            return value
        normalized: list[list[float]] = []
        for point in value:
            if len(point) != 2:
                raise ValueError("әр нүкте [lat, lon] болуы керек")
            lat, lon = float(point[0]), float(point[1])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("нүкте координатасы жарамсыз")
            normalized.append([lat, lon])
        return normalized

    @model_validator(mode="after")
    def validate_shape_data(self):
        if self.shape == "circle" and (self.lat is None or self.lon is None):
            raise ValueError("circle үшін lat және lon міндетті")
        if self.shape == "polygon" and len(self.polygon or []) < 3:
            raise ValueError("polygon үшін кемінде 3 нүкте міндетті")
        if self.shape == "polyline" and len(self.points or self.polygon or []) < 2:
            raise ValueError("polyline үшін кемінде 2 нүкте міндетті")
        return self


class ZonePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=240)
    kind: Optional[str] = None
    shape: Optional[str] = None
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    radius_m: Optional[float] = Field(default=None, gt=0, le=10000)
    corridor_m: Optional[float] = Field(default=None, gt=0, le=1000)
    polygon: Optional[list[list[float]]] = None
    points: Optional[list[list[float]]] = None
    active: Optional[bool] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    source_url: Optional[str] = Field(default=None, max_length=1000)
    external_ref: Optional[str] = Field(default=None, max_length=300)
    responsible_org: Optional[str] = Field(default=None, max_length=500)
    boundary_verified: Optional[bool] = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in db.ZONE_KINDS:
            raise ValueError(f"түрі {db.ZONE_KINDS} қатарында болуы керек")
        return value

    @field_validator("shape")
    @classmethod
    def validate_shape(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in db.ZONE_SHAPES:
            raise ValueError(f"геометрия {db.ZONE_SHAPES} қатарында болуы керек")
        return value

    @field_validator("polygon", "points")
    @classmethod
    def validate_geometry(cls, value: Optional[list[list[float]]]) -> Optional[list[list[float]]]:
        return ZonePayload.validate_geometry(value)


class LocationCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    address_text: Optional[str] = Field(default=None, max_length=500)
    note: str = Field(min_length=3, max_length=1000)


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    note: str = Field(default="", max_length=1000)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in db.STATUSES:
            raise ValueError(f"статус {db.STATUSES} қатарында болуы керек")
        return value


class RejectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=3, max_length=1000)


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    log.info("=" * 62)
    log.info("  AIQYN Portal іске қосылды")
    log.info("  Карта      : http://localhost:8000/")
    log.info("  API        : http://localhost:8000/docs")
    log.info("  Оператор   : %s (құпия сөз журналға жазылмайды)", ADMIN_USER)
    log.info("=" * 62)


def require_operator(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Оператор әрекеттері үшін қарапайым аутентификация."""
    user_ok = secrets.compare_digest(credentials.username, ADMIN_USER)
    password_ok = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (user_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Кіру деректері дұрыс емес",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ============================================================
#  Қабылдау — Vision программасынан
# ============================================================

@app.post("/api/documents")
async def receive_document(
    request: Request,
    photo: Optional[UploadFile] = File(None),
    video_clip: Optional[UploadFile] = File(None),
):
    """AIQYN Vision жіберген ДАЙЫН құжатты қабылдау.

    Сайт өрістерді ӨЗГЕРТПЕЙДІ — келген күйінде сақтайды.
    """
    form = await request.form()

    payload: dict = {}
    for key, value in form.multi_items():
        if key in ("photo", "video_clip"):
            continue
        if isinstance(value, str):
            # Vision күрделі мәндерді JSON-жол ретінде жібереді
            if value.startswith(("{", "[")):
                try:
                    payload[key] = json.loads(value)
                    continue
                except json.JSONDecodeError:
                    pass
            if value in ("true", "false"):
                payload[key] = value == "true"
                continue
            payload[key] = value

    if READ_ONLY:
        raise HTTPException(
            status_code=503,
            detail="Бұл — көрсетуге арналған айна (тек оқу). Далалық жұмыс "
                   "ноутбуктегі сайтқа жүреді.",
        )

    event_id = payload.get("event_id")
    if not event_id:
        raise HTTPException(status_code=400, detail="event_id көрсетілмеген")

    # Sender желілік жауапты алмай қайта жіберуі мүмкін. Бұрын қабылданған
    # event-тің дәлел файлын және аудит тарихын қайта жазбаймыз.
    existing = db.get_document(event_id)
    if existing is not None:
        return {
            "ok": True,
            "duplicate": True,
            "event_id": event_id,
            "status": existing["status"],
        }

    folder = MEDIA_DIR / event_id
    folder.mkdir(parents=True, exist_ok=True)

    photo_name = video_name = None
    if photo is not None and photo.filename:
        photo_name = "photo.jpg"
        with open(folder / photo_name, "wb") as handle:
            shutil.copyfileobj(photo.file, handle)
    if video_clip is not None and video_clip.filename:
        video_name = "clip.mp4"
        with open(folder / video_name, "wb") as handle:
            shutil.copyfileobj(video_clip.file, handle)

    db.insert_document(payload, photo_name, video_name)

    log.info(
        "ҚҰЖАТ ҚАБЫЛДАНДЫ: %s | %s | %s%s",
        event_id, payload.get("defect_type_official"), payload.get("address_text"),
        "  [ИИ растады]" if payload.get("ai_verified") else "",
    )

    # Операторға Telegram-ға хабар (кілт бапталған болса)
    notifier.notify_new_document(
        payload, folder / photo_name if photo_name else None
    )

    # Автоматты жіберу (әдепкі күйде ӨШІРУЛІ — төмендегі түсініктемені қараңыз)
    final_status = "new"
    if _should_auto_send(payload):
        stored = db.get_document(event_id)
        if stored:
            db.set_status(event_id, "confirmed", "Автоматты растау (ережеге сай)", "auto")
            approved = db.get_document(event_id) or stored
            delivery = _send_to_authority(approved)
            db.record_delivery(event_id, delivery, "auto")
            db.set_status(
                event_id, "sent",
                f"Автоматты жіберілді: ticket={delivery.get('ticket_id')}, "
                f"mock-109={delivery['mock_109']}", "auto",
            )
            final_status = "sent"
            log.info("АВТОМАТТЫ ЖІБЕРІЛДІ: %s", event_id)

    return {"ok": True, "event_id": event_id, "status": final_status}


# ============================================================
#  Оқу
# ============================================================

@app.get("/api/health")
def health():
    return {"ok": True, "service": "AIQYN Portal", "version": app.version}


# ============================================================
#  Жөндеу аймақтары / жабық жолдар
# ============================================================

@app.get("/api/zones")
def get_zones(active_only: bool = True, include_expired: bool = False):
    """Аймақтар тізімі.

    Осы нүктені AIQYN Vision программасы да оқиды: сол аймақтағы
    ақаулар бойынша құжат ҚҰРЫЛМАЙДЫ (жөндеу бұрыннан жүріп жатыр).
    """
    zones = db.list_zones(active_only=active_only, include_expired=include_expired)
    return {"count": len(zones), "zones": zones}


@app.post("/api/zones")
def create_zone(payload: ZonePayload, operator: str = Depends(require_operator)):
    body = payload.model_dump()
    try:
        zone_id = db.insert_zone(body, actor=operator)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    log.info("Аймақ қосылды: #%s «%s» (%s)", zone_id, body.get("name"), body.get("kind"))
    return {"ok": True, "id": zone_id, "zone": db.get_zone(zone_id)}


@app.patch("/api/zones/{zone_id}")
def edit_zone(
    zone_id: int,
    payload: ZonePatch,
    operator: str = Depends(require_operator),
):
    existing = db.get_zone(zone_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Аймақ табылмады")

    changes = payload.model_dump(exclude_unset=True)
    zone_fields = set(ZonePayload.model_fields)
    merged = {key: value for key, value in existing.items() if key in zone_fields}
    merged.update(changes)
    if "points" in changes:
        merged["polygon"] = changes["points"]
    elif existing.get("shape") == "polyline" and existing.get("points"):
        merged["points"] = existing["points"]
    try:
        validated = ZonePayload.model_validate(merged).model_dump()
        # PATCH тек өзгерген өрістерді жазады, бірақ geometry тексерілген түрде қалады.
        if "points" in changes or "polygon" in changes or "shape" in changes:
            changes["points"] = validated.get("points") or validated.get("polygon")
        if not db.update_zone(zone_id, changes, actor=operator):
            raise HTTPException(status_code=404, detail="Аймақ табылмады")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"ok": True, "id": zone_id, "zone": db.get_zone(zone_id)}


@app.delete("/api/zones/{zone_id}")
def remove_zone(zone_id: int, operator: str = Depends(require_operator)):
    if not db.delete_zone(zone_id, actor=operator):
        raise HTTPException(status_code=404, detail="Аймақ табылмады")
    log.info("Аймақ архивке жіберілді: #%s", zone_id)
    return {"ok": True, "id": zone_id, "active": False}


@app.post("/api/zones/{zone_id}/activate")
def activate_zone(zone_id: int, operator: str = Depends(require_operator)):
    if not db.set_zone_active(zone_id, True, actor=operator):
        raise HTTPException(status_code=404, detail="Аймақ табылмады")
    return {"ok": True, "id": zone_id, "zone": db.get_zone(zone_id)}


@app.get("/api/stats")
def get_stats():
    return db.stats()


@app.get("/api/documents")
def get_documents(status: str = "all", class_key: str = "all", limit: int = 500):
    documents = db.list_documents(status=status, class_key=class_key, limit=limit)
    for document in documents:
        document.pop("raw_json", None)
        document["status_label"] = db.STATUS_LABELS.get(document["status"], {}).get(
            "kk", document["status"]
        )
    return {"count": len(documents), "documents": documents}


@app.get("/api/documents/{event_id}")
def get_document(event_id: str):
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")
    document.pop("raw_json", None)
    document["status_label"] = db.STATUS_LABELS.get(document["status"], {}).get(
        "kk", document["status"]
    )
    return document


@app.patch("/api/documents/{event_id}/location")
def correct_document_location(
    event_id: str,
    payload: LocationCorrection,
    operator: str = Depends(require_operator),
):
    try:
        updated = db.correct_document_location(
            event_id,
            payload.lat,
            payload.lon,
            payload.address_text,
            payload.note,
            operator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Құжат табылмады")
    document = db.get_document(event_id)
    if document:
        document.pop("raw_json", None)
        document["status_label"] = db.STATUS_LABELS.get(document["status"], {}).get(
            "kk", document["status"]
        )
    return {"ok": True, "event_id": event_id, "document": document}


# ============================================================
#  Оператор әрекеттері
# ============================================================

def _should_auto_send(payload: dict) -> bool:
    """Құжатты оператордың растауынсыз жіберуге бола ма.

    ӘДЕПКІ КҮЙІ — ӨШІРУЛІ, әрі солай қалғаны дұрыс. Себебі: расталмаған
    автоматты өтінім жалған шағым болып саналуы мүмкін. Толық автоматты
    режим әкімдікпен келісілгеннен кейін ғана қосылады.

    Қосу (.env):
        AIQYN_AUTO_SEND=true
        AIQYN_AUTO_SEND_MIN_CONFIDENCE=0.85
        AIQYN_AUTO_SEND_MIN_SEVERITY=high
        AIQYN_AUTO_SEND_REQUIRE_AI=true
    """
    if os.getenv("AIQYN_AUTO_SEND", "false").strip().lower() not in ("1", "true", "yes"):
        return False

    require_ai = os.getenv("AIQYN_AUTO_SEND_REQUIRE_AI", "true").lower() in ("1", "true", "yes")
    if require_ai and not payload.get("ai_verified"):
        return False

    try:
        min_confidence = float(os.getenv("AIQYN_AUTO_SEND_MIN_CONFIDENCE", "0.85"))
    except ValueError:
        min_confidence = 0.85

    # ИИ расталған болса — соның сенімділігін, әйтпесе YOLO-нікін қараймыз
    confidence = float(payload.get("ai_confidence") or payload.get("confidence") or 0)
    if confidence < min_confidence:
        return False

    order = {"low": 0, "medium": 1, "high": 2}
    min_severity = os.getenv("AIQYN_AUTO_SEND_MIN_SEVERITY", "high").strip().lower()
    if order.get(str(payload.get("severity", "low")).lower(), 0) < order.get(min_severity, 2):
        return False

    return True


def _send_to_authority(document: dict) -> dict:
    """Расталған құжатты жауапты органға жіберу.

    Хакатон режимі: mock-109 тікеті + (бапталса) email.
    """
    result = {
        "ok": False,
        "mock_109": False,
        "email": False,
        "email_error": "",
        "ticket_source": "mock-109",
        "ticket_id": f"MOCK-109-{secrets.token_hex(4).upper()}",
        "status": "accepted",
    }

    ticket = {
        "ticket_source": result["ticket_source"],
        "created_by": "AIQYN",
        "ticket_id": result["ticket_id"],
        "event_id": document["event_id"],
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
        "description": document.get("description_text"),
        "map_link": (
            f"https://www.google.com/maps?q={document.get('lat')},{document.get('lon')}"
            if document.get("lat") is not None and document.get("lon") is not None
            else document.get("map_link")
        ),
        "responsible_org": document.get("responsible_org"),
        "external_ticket_id": result["ticket_id"],
        "created_at": db.now_iso(),
    }
    with open(TICKETS_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(ticket, ensure_ascii=False) + "\n")
    result["mock_109"] = True
    result["ok"] = True
    log.info("mock-109 тікеті жазылды: %s", document["event_id"])

    if SMTP_HOST and SMTP_USER:
        try:
            message = EmailMessage()
            message["Subject"] = f"[AIQYN] {document.get('defect_type_official')} — {document.get('address_text')}"
            message["From"] = SMTP_FROM
            message["To"] = document.get("responsible_email") or SMTP_FROM
            message.set_content(
                f"{document.get('description_text', '')}\n\n"
                f"{'-' * 50}\n\n"
                f"{document.get('description_text_ru', '')}\n\n"
                f"Карта: {document.get('map_link')}\n"
                f"Оқиға нөмірі: {document['event_id']}\n"
            )

            folder = MEDIA_DIR / document["event_id"]
            for filename, mime in (
                (document.get("photo_file"), ("image", "jpeg")),
                (document.get("video_file"), ("video", "mp4")),
            ):
                if not filename:
                    continue
                path = folder / filename
                if path.exists():
                    message.add_attachment(
                        path.read_bytes(), maintype=mime[0], subtype=mime[1], filename=filename
                    )

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
                smtp.starttls()
                smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(message)

            result["email"] = True
            log.info("Email жіберілді: %s", message["To"])
        except Exception as exc:
            result["email_error"] = str(exc)
            log.warning("Email жіберілмеді: %s", exc)
    else:
        log.info("SMTP бапталмаған — email жіберілмеді (тек mock-109).")

    return result


@app.post("/api/documents/{event_id}/confirm")
def confirm_document(event_id: str, operator: str = Depends(require_operator)):
    """Растау -> жауапты органға жіберу."""
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")
    if document["status"] not in ("new", "confirmed"):
        raise HTTPException(status_code=409, detail="Бұл құжатты қазір растауға болмайды")

    try:
        if document["status"] == "new":
            db.set_status(event_id, "confirmed", "Оператор растады", operator)
        approved = db.get_document(event_id) or document
        delivery = _send_to_authority(approved)
        db.record_delivery(event_id, delivery, operator)
        db.set_status(
            event_id, "sent",
            f"Жіберілді: ticket={delivery.get('ticket_id')}, "
            f"mock-109={delivery['mock_109']}, email={delivery['email']}",
            operator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {"ok": True, "event_id": event_id, "status": "sent", "delivery": delivery}


@app.post("/api/documents/{event_id}/reject")
def reject_document(
    event_id: str,
    payload: RejectionPayload,
    operator: str = Depends(require_operator),
):
    """Қабылдамау -> модельді қайта оқыту кезегіне.

    Сайт ЕШНӘРСЕНІ қайта оқытпайды — тек белгілеп қояды. Vision
    программасы бұл жазбаларды кейін өзі сұрап ала алады.
    """
    try:
        if not db.set_status(event_id, "rejected", payload.note, operator):
            raise HTTPException(status_code=404, detail="Құжат табылмады")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "event_id": event_id, "status": "rejected"}


@app.post("/api/documents/{event_id}/status")
def update_status(
    event_id: str,
    payload: StatusUpdate,
    operator: str = Depends(require_operator),
):
    try:
        if not db.set_status(event_id, payload.status, payload.note, operator):
            raise HTTPException(status_code=404, detail="Құжат табылмады")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "event_id": event_id, "status": payload.status}


@app.post("/api/documents/{event_id}/after-photo")
async def upload_after_photo(
    event_id: str,
    after_photo: UploadFile = File(...),
    operator: str = Depends(require_operator),
):
    """Жөндеуден кейінгі фото (before/after тізбегін жабу)."""
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")
    if document["status"] != "in_progress":
        raise HTTPException(
            status_code=409,
            detail="Жөндеуден кейінгі фото тек «Орындалуда» статусында қабылданады",
        )

    folder = MEDIA_DIR / event_id
    folder.mkdir(parents=True, exist_ok=True)
    filename = "after.jpg"
    with open(folder / filename, "wb") as handle:
        shutil.copyfileobj(after_photo.file, handle)

    db.set_after_photo(event_id, filename)
    try:
        db.set_status(event_id, "repaired", "Жөндеу расталды (after-фото)", operator)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "event_id": event_id, "status": "repaired"}


# ============================================================
#  Mock 109 (ЕКЦ эмуляциясы)
# ============================================================

@app.post("/mock/109/tickets")
async def mock_109_tickets(request: Request):
    """iKOMEK 109 API-інің эмуляциясы.

    Нақты интеграция әкімдікпен келісілгеннен кейін осы нүктенің
    орнына нақты API қойылады — қалған код өзгермейді.
    """
    payload = await request.json()
    ticket_id = payload.get("ticket_id") or f"MOCK-109-{secrets.token_hex(4).upper()}"
    payload = {**payload, "ticket_source": "mock-109", "ticket_id": ticket_id}
    with open(TICKETS_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return {"ok": True, "ticket_source": "mock-109", "ticket_id": ticket_id}


@app.get("/api/tickets")
def list_tickets(limit: int = 100):
    """Жіберілген тікеттер журналы (демо үшін)."""
    if not TICKETS_LOG.exists():
        return {"count": 0, "tickets": []}
    lines = TICKETS_LOG.read_text(encoding="utf-8").strip().splitlines()
    tickets = []
    for line in lines[-limit:]:
        try:
            tickets.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"count": len(tickets), "tickets": list(reversed(tickets))}


# ============================================================
#  Беттер мен файлдар
# ============================================================

@app.get("/api/documents/{event_id}/docx")
def download_docx(event_id: str):
    """Ресми құжатты Word (.docx) файлы ретінде жүктеп алу.

    Оператор оны бланкіге қойып, қолын қойып, өз атынан жібере алады.
    """
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")

    from .docgen import build_docx, safe_filename

    try:
        buffer = build_docx(document, MEDIA_DIR)
    except Exception as exc:
        log.exception("Word құжаты жасалмады: %s", exc)
        raise HTTPException(status_code=500, detail=f"Құжат жасалмады: {exc}")

    filename = safe_filename(event_id)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export.csv")
def export_csv(status: str = "all"):
    """Барлық ақауды CSV кестесі етіп жүктеу (есеп беру үшін).

    Excel-де ашылуы үшін UTF-8 BOM қосылады.
    """
    documents = db.list_documents(status=status, limit=10000)

    columns = [
        ("event_id", "Оқиға №"),
        ("status", "Статус"),
        ("defect_type_official", "Ақау түрі"),
        ("address_text", "Мекенжайы"),
        ("lat", "Ені"),
        ("lon", "Бойлығы"),
        ("original_lat", "Бастапқы ені"),
        ("original_lon", "Бастапқы бойлығы"),
        ("location_corrected", "Орны түзетілді"),
        ("location_correction_note", "Орнын түзету себебі"),
        ("severity", "Ауырлығы"),
        ("confidence", "Сенімділік"),
        ("ai_verified", "ИИ растады"),
        ("ai_action", "Ұсынылатын шара"),
        ("ai_urgency_days", "Мерзім (күн)"),
        ("responsible_org", "Жауапты ұйым"),
        ("timestamp_human", "Анықталған уақыты"),
        ("approved_at", "Расталған уақыты"),
        ("approved_by", "Растаған оператор"),
        ("sent_at", "Жіберілген уақыты"),
        ("external_ticket_source", "Сыртқы жүйе"),
        ("external_ticket_id", "Сыртқы өтініш №"),
        ("external_ticket_status", "Сыртқы статус"),
        ("map_link", "Карта"),
    ]

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow([title for _, title in columns])
    for document in documents:
        writer.writerow([document.get(key, "") for key, _ in columns])

    payload = "﻿" + buffer.getvalue()      # BOM — Excel кириллицаны дұрыс оқысын
    stamp = db.now_iso()[:10]

    return StreamingResponse(
        io.BytesIO(payload.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="AIQYN_export_{stamp}.csv"'},
    )


@app.post("/api/documents/{event_id}/send")
def send_again(event_id: str, operator: str = Depends(require_operator)):
    """Расталған құжатты жауапты органға (қайта) жіберу."""
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")
    if document["status"] not in ("sent", "in_progress", "repaired"):
        raise HTTPException(status_code=409, detail="Тек бұрын жіберілген құжат қайта жіберіледі")

    delivery = _send_to_authority(document)
    db.record_delivery(event_id, delivery, operator)
    # Қайта жіберу құжаттың орындалу статусын артқа қайтармайды.
    db.set_status(event_id, document["status"], "Қайта жіберілді", operator)
    return {"ok": True, "event_id": event_id, "delivery": delivery}


@app.get("/logo.svg")
def logo_svg():
    """Логотип — сайт, презентация, кез келген жерде қолдануға."""
    from .logo import svg_icon
    return Response(content=svg_icon(size=512), media_type="image/svg+xml")


@app.get("/logo.png")
def logo_png(size: int = 512, transparent: bool = False):
    from .logo import png_bytes
    size = max(32, min(1024, size))
    return Response(content=png_bytes(size, transparent), media_type="image/png")


ORIGIN = "https://aiqyn-alpha.vercel.app"

ROBOTS_TXT = "\n".join([
    f"# {ORIGIN}",
    "User-agent: *",
    "Allow: /",
    "",
    "# API мен дәлел файлдары индекстелмейді",
    "Disallow: /api/",
    "Disallow: /media/",
    "Disallow: /documents/",
    "",
    f"Sitemap: {ORIGIN}/sitemap.xml",
    "",
])

SITEMAP_PAGES = (("/", "1.0"), ("/portal", "0.8"))


@app.get("/robots.txt")
def robots_txt():
    """Іздеу жүйелеріне арналған нұсқау.

    Vercel конфигурациясы әр сұрауды осы функцияға жібереді, сондықтан
    robots.txt статикалық файл емес, маршрут ретінде беріледі — әйтпесе
    іздеу роботы оның орнына басты беттің HTML-ін алар еді.
    """
    return Response(content=ROBOTS_TXT, media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml")
def sitemap_xml():
    urls = []
    for loc, priority in SITEMAP_PAGES:
        urls.append(
            "  <url>\n"
            f"    <loc>{ORIGIN}{loc}</loc>\n"
            "    <lastmod>2026-08-26</lastmod>\n"
            "    <changefreq>weekly</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            "  </url>\n"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(urls)
        + "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


def _brand_asset(name: str, media_type: str):
    path = STATIC_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Файл табылмады")
    return FileResponse(path, media_type=media_type)


@app.get("/favicon.svg")
def favicon_svg():
    return _brand_asset("favicon.svg", "image/svg+xml")


@app.get("/og-image.png")
def og_image():
    """Сілтеме бөліскенде көрінетін сурет (1200x630)."""
    return _brand_asset("og-image.png", "image/png")


@app.get("/apple-touch-icon.png")
def apple_touch_icon():
    return _brand_asset("apple-touch-icon.png", "image/png")


@app.get("/media/{event_id}/{filename}")
def get_media(event_id: str, filename: str):
    path = (MEDIA_DIR / event_id / filename).resolve()
    # Қалтадан тыс шығуға жол бермеу
    if not str(path).startswith(str(MEDIA_DIR.resolve())) or not path.exists():
        raise HTTPException(status_code=404, detail="Файл табылмады")
    return FileResponse(path)


def _serve_template(name: str) -> HTMLResponse:
    page = TEMPLATES_DIR / name
    if not page.exists():
        return HTMLResponse(f"<h1>{name} табылмады</h1>", status_code=500)
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
def index():
    return _serve_template("index.html")


@app.get("/portal", response_class=HTMLResponse)
def portal():
    return _serve_template("map.html")


@app.get("/documents/{event_id}/print", response_class=HTMLResponse)
def print_document(event_id: str):
    """Ресми құжаттың басып шығаруға дайын нұсқасы (браузерден PDF жасауға болады)."""
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")

    page = TEMPLATES_DIR / "print.html"
    if not page.exists():
        return HTMLResponse("<h1>print.html табылмады</h1>", status_code=500)

    html = page.read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__EVENT_ID__", event_id))


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return JSONResponse(status_code=404, content={"ok": False, "detail": str(exc.detail)})
