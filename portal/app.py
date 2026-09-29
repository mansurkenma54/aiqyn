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
import re
import secrets
import shutil
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
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
def _tickets_log_path() -> Path:
    """109 тікеттерінің журналы қай жерде жатады.

    Serverless ортада (Vercel) жоба қалтасы «тек оқу» режимінде — оған
    жазуға тырысу 500 қатесін берді де, оператордың «Тексеру және ЕКЦ
    109-ға жіберу» батырмасы тірі сайтта істемей тұрды. Сондықтан
    жазылатын жерді дерекқормен бір қалтадан аламыз.
    """
    explicit = os.getenv("AIQYN_TICKETS_LOG")
    if explicit:
        return Path(explicit)
    db_path = os.getenv("AIQYN_DB_PATH")
    if db_path:
        return Path(db_path).parent / "mock_109_tickets.jsonl"
    return ROOT / "mock_109_tickets.jsonl"


TICKETS_LOG = _tickets_log_path()

try:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass          # тек оқу режиміндегі орта (Vercel)
STATIC_DIR.mkdir(exist_ok=True)

ADMIN_USER = os.getenv("AIQYN_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("AIQYN_ADMIN_PASSWORD", "aiqyn2026")
# Vision → сайт арнасының кілті. Қойылса, құжатты тек осы кілтті білетін
# камера программасы жібере алады — желідегі кез келген адам емес.
INGEST_TOKEN = os.getenv("AIQYN_INGEST_TOKEN", "").strip()

# Жеткізу арналары portal/delivery.py ішінде. Кілттер сол жерде, әр
# жіберу сайын os.environ-нан оқылады.
#
# ЕСКЕРТУ: демо кезінде AIQYN_DELIVERY_TO — өз поштаңыз болғаны дұрыс.
# Нақты мемлекеттік мекеменің поштасын келісімсіз қоюға болмайды:
# растаусыз ресми өтінім жалған шағым болып саналады.

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
    if INGEST_TOKEN and not secrets.compare_digest(
        request.headers.get("X-AIQYN-Token", ""), INGEST_TOKEN
    ):
        raise HTTPException(status_code=401, detail="Камера кілті қате немесе жоқ")

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

def auto_send_policy() -> dict:
    """Автоматты өтінім қалыптастыру ережелері — оқуға арналған көрініс.

    ТЗ талабы: «Интеграция с ЕКЦ 109 для автоматического формирования
    заявок на ремонт». Ереже жасырын болмауы керек: оператор да, әкімдік
    те қандай шартта өтінім адамсыз кететінін көріп тұруы тиіс.
    """
    enabled = os.getenv("AIQYN_AUTO_SEND", "false").strip().lower() in ("1", "true", "yes")
    try:
        min_confidence = float(os.getenv("AIQYN_AUTO_SEND_MIN_CONFIDENCE", "0.85"))
    except ValueError:
        min_confidence = 0.85
    return {
        "enabled": enabled,
        "require_ai": os.getenv("AIQYN_AUTO_SEND_REQUIRE_AI", "true").lower()
                      in ("1", "true", "yes"),
        "min_confidence": min_confidence,
        "min_severity": os.getenv("AIQYN_AUTO_SEND_MIN_SEVERITY", "high").strip().lower(),
        "require_gps": os.getenv("AIQYN_AUTO_SEND_REQUIRE_GPS", "true").lower()
                       in ("1", "true", "yes"),
        "target": os.getenv("AIQYN_109_URL") or "109 адаптері + email/WhatsApp",
        "channels": [name for name, state in _delivery_health().items()
                     if state.get("configured")],
        "note": "Шарттардың бәрі орындалғанда ғана өтінім операторсыз "
                "қалыптасады. Бір шарт орындалмаса — кезекке түседі.",
    }


def _delivery_health() -> dict:
    from . import delivery
    return delivery.health()


@app.get("/api/auto-send")
def auto_send_status():
    """Автоматты режимнің ағымдағы ережелері."""
    return auto_send_policy()


@app.get("/api/delivery/health")
def delivery_health():
    """Қай жеткізу арнасы дайын — жібермей тұрып тексеру.

    Демонстрацияға дейін ашып көру керек: «email: configured=true» болса
    ғана өтінім шынымен кетеді.
    """
    from . import delivery
    return delivery.health()


@app.post("/api/delivery/test")
def delivery_test(operator: str = Depends(require_operator)):
    """Сынақ өтінімін жіберу — арналардың шынымен жұмыс істейтінін тексеру.

    Нақты ақау жасамай-ақ, бапталған барлық арнаға сынақ хабары кетеді.
    Хат тақырыбында «СЫНАҚ» деп тұрады: оны нақты өтінім деп қабылдап
    қалмауы үшін.
    """
    from . import delivery

    sample = {
        "event_id": f"AIQYN-TEST-{secrets.token_hex(3).upper()}",
        "defect_type_official": "СЫНАҚ — жүйенің байланысын тексеру",
        "defect_type_official_ru": "ТЕСТ — проверка канала связи",
        "address_text": "Шымкент қаласы (сынақ жазбасы)",
        "severity": "low",
        "confidence": 1.0,
        "timestamp_human": db.now_iso().replace("T", " ")[:19],
        "description_text": "Бұл — AIQYN ZHOL жүйесінің байланыс арнасын "
                            "тексеруге арналған СЫНАҚ хабарламасы. Нақты жол "
                            "ақауы туралы өтінім емес, әрекет қажет етпейді.",
        "description_text_ru": "Это ТЕСТОВОЕ сообщение для проверки канала "
                               "связи системы AIQYN ZHOL. Не является заявкой "
                               "о реальном дефекте, действий не требует.",
        "responsible_org": "Сынақ",
        "created_at": db.now_iso(),
    }
    receipt = delivery.send(sample, MEDIA_DIR, sample["event_id"])
    log.info("Сынақ жіберілді (%s): %s", operator, receipt["delivered"] or "жоқ арна")
    return receipt


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

    # Координата сенімсіз болса — автоматты жібермейміз. Жөндеу бригадасы
    # қате мекенжайға баруы адам растағаннан гөрі қымбатқа түседі.
    require_gps = os.getenv("AIQYN_AUTO_SEND_REQUIRE_GPS", "true").lower() in ("1", "true", "yes")
    if require_gps and not payload.get("gps_trusted"):
        return False

    return True


def _send_to_authority(document: dict) -> dict:
    """Расталған құжатты жауапты органға ШЫНЫМЕН жіберу.

    Арналар қатар жүреді (portal/delivery.py): 109 адаптері, ресми email
    (.docx құжаты мен фотосы тіркеледі), WhatsApp, Telegram. Қай арна
    жеткені де, жетпегені де квитанцияда ашық жазылады — «жіберілді» деп
    жалған белгі қойылмайды.
    """
    from . import delivery, spatial

    ticket_id = f"AIQYN-109-{secrets.token_hex(4).upper()}"

    # Кеңістіктік байланыс өтінімнің ІШІНДЕ кетуі керек (ТЗ: SDR / ЦС ГГ
    # НИПД — ақаудың жол сегментіне байлануы). Байлау сәтсіз болса, өріс
    # бос қалады — жалған байланыс жіберілмейді.
    lat = document.get("display_lat") or document.get("lat")
    lon = document.get("display_lon") or document.get("lon")
    document = {**document, "spatial": spatial.bind(lat, lon, allow_network=not READ_ONLY)}

    # Аудит ізі: өтінім қандай күйде кеткені әрқашан журналға жазылады.
    # Ресми эндпойнт ашылған-ашылмағанына қарамастан, дәлел қалады.
    ticket = delivery.ticket_payload(document, ticket_id)
    ticket["created_by"] = "AIQYN"
    ticket["created_at"] = db.now_iso()
    journal = False
    try:
        TICKETS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TICKETS_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(ticket, ensure_ascii=False) + "\n")
        journal = True
    except OSError as exc:
        log.warning("Тікет журналы жазылмады: %s", exc)

    receipt = delivery.send(document, MEDIA_DIR, ticket_id)
    channels = receipt["channels"]

    # 109 өз нөмірін берсе — сол нөмір негізгі болады
    external = channels["ekc109"].get("external_ticket_id")
    if external:
        ticket_id = str(external)

    email = channels["email"]
    result = {
        "ok": bool(receipt["delivered"] or journal),
        "ticket_id": ticket_id,
        "ticket_source": "ЕКЦ 109" if channels["ekc109"].get("ok") else "AIQYN журналы",
        "status": "accepted" if receipt["delivered"] else ("queued" if journal else "failed"),
        "delivered": receipt["delivered"],
        "channels": channels,
        "recipients": receipt["recipients"],
        "whatsapp_link": receipt["whatsapp_link"],
        # Ескі өрістер — дерекқор мен интерфейс солармен жұмыс істейді
        "mock_109": journal,
        "email": bool(email.get("ok")),
        "email_error": "" if email.get("ok") else (email.get("detail") or ""),
    }
    log.info("Өтінім жіберілді (%s): арналар=%s, тікет=%s",
             document.get("event_id"), receipt["delivered"] or "жоқ", ticket_id)
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
    TICKETS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TICKETS_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return {"ok": True, "ticket_source": "mock-109", "ticket_id": ticket_id}


# ============================================================
#  Кеңістіктік байланыс · аналитика · экспорт
#  (ТЗ: SDR / ЦС ГГ НИПД байланысы және есептілік)
# ============================================================

@app.get("/api/spatial/{event_id}")
def spatial_binding(event_id: str, refresh: bool = False):
    """Оқиғаны нақты жол сегментіне байлау.

    Геометрия OpenStreetMap-тен алынады, нәтиже дискіде кэштеледі.
    Жол табылмаса — жалған байланыс қайтарылмайды.
    """
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Оқиға табылмады")

    from . import spatial

    lat = document.get("display_lat") or document.get("lat")
    lon = document.get("display_lon") or document.get("lon")
    binding = spatial.bind(lat, lon, allow_network=not READ_ONLY or refresh)
    return {
        "event_id": event_id,
        "binding": binding,
        "summary_kk": spatial.summary_kk(binding),
    }


def _analytics(documents: list[dict]) -> dict:
    """Жол инфрақұрылымын басқаруға арналған жиынтық көрсеткіштер."""
    from collections import Counter, defaultdict

    by_type: Counter = Counter()
    by_status: Counter = Counter()
    by_severity: Counter = Counter()
    by_street: defaultdict = defaultdict(lambda: {"count": 0, "high": 0, "types": Counter()})
    by_day: Counter = Counter()

    ai_checked = ai_confirmed = 0
    corrected = 0
    confidences: list[float] = []

    for doc in documents:
        class_key = doc.get("class_key") or "белгісіз"
        by_type[class_key] += 1
        by_status[doc.get("status") or "new"] += 1
        severity = doc.get("severity") or "low"
        by_severity[severity] += 1

        address = (doc.get("display_address_text") or doc.get("address_text") or "").strip()
        street = address.split(",")[1].strip() if address.count(",") >= 1 else (address or "белгісіз")
        entry = by_street[street]
        entry["count"] += 1
        entry["types"][class_key] += 1
        if severity == "high":
            entry["high"] += 1

        timestamp = doc.get("timestamp") or ""
        if len(timestamp) >= 10:
            by_day[timestamp[:10]] += 1

        if doc.get("ai_note"):
            ai_checked += 1
            if doc.get("ai_verified"):
                ai_confirmed += 1
        if doc.get("location_corrected"):
            corrected += 1
        if doc.get("confidence"):
            confidences.append(float(doc["confidence"]))

    streets = sorted(
        ({"street": name, "count": v["count"], "high": v["high"],
          "top_type": (v["types"].most_common(1)[0][0] if v["types"] else None)}
         for name, v in by_street.items()),
        key=lambda x: (-x["count"], x["street"]),
    )

    total = len(documents)
    return {
        "total": total,
        "by_type": [{"key": k, "count": c} for k, c in by_type.most_common()],
        "by_status": [{"key": k, "count": c} for k, c in by_status.most_common()],
        "by_severity": [{"key": k, "count": c} for k, c in by_severity.most_common()],
        "by_street": streets[:12],
        "by_day": [{"date": d, "count": c} for d, c in sorted(by_day.items())],
        "quality": {
            "ai_checked": ai_checked,
            "ai_confirmed": ai_confirmed,
            "ai_confirm_rate": round(ai_confirmed / ai_checked, 3) if ai_checked else None,
            "location_corrected": corrected,
            "avg_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        },
        "operations": _operations(documents),
        "generated_at": db.now_iso(),
    }


# Жұмыс ағынының кезеңдері: оқиға осы тізбекпен жүреді
_PIPELINE = ("new", "confirmed", "sent", "in_progress", "repaired", "closed")
_OPEN_STATES = ("new", "confirmed", "sent", "submitted", "registered",
                "assigned", "in_progress", "repaired", "reopened")


def _hours_between(start: object, end: object) -> Optional[float]:
    """Екі уақыт белгісінің арасы (сағатпен). Дұрыс болмаса — None."""
    def parse(value: object) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        # Дерекқорда екі пішім де кездеседі: белдеуі барлары да,
        # жоқтары да. Екеуін бір-бірінен алу мүмкін емес, сондықтан
        # белдеуі жоғына жергілікті белдеу қосылады.
        if stamp.tzinfo is None:
            stamp = stamp.astimezone()
        return stamp

    a, b = parse(start), parse(end)
    if a is None or b is None:
        return None
    delta = (b - a).total_seconds() / 3600
    return round(delta, 2) if delta >= 0 else None


def _median(values: list[float]) -> Optional[float]:
    """Медиана — орташадан адал: бір ұзап кеткен оқиға бүкіл суретті бұрмаламайды."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 1)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 1)


def _operations(documents: list[dict]) -> dict:
    """Басқару тиімділігінің көрсеткіштері.

    ТЗ талабы: «Формирование аналитики и отчётности для повышения
    эффективности управления дорожной инфраструктурой». «Неше ақау бар»
    деген сан оған жетпейді — басшыға ЖҮЙЕ ҚАЛАЙ ЖҰМЫС ІСТЕП ЖАТЫРЫ
    керек: қанша уақытта жауап берілді, қаншасы жабылды, қаншасы
    жөндеуден кейін қайта ашылды.

    Кезең уақыттары documents кестесінде жоқ — олар status_history
    ішінде, сондықтан осында біріктіріледі.
    """
    from . import spatial

    stamps = db.status_timestamps()

    to_review: list[float] = []      # табылғаннан оператор растағанға дейін
    to_repair: list[float] = []      # жіберілгеннен жөнделгенге дейін
    full_cycle: list[float] = []     # табылғаннан жабылғанға дейін

    delivered = delivery_failed = 0
    bound = with_coords = 0
    reopened = closed = open_now = overdue = 0
    oldest_open: Optional[dict] = None
    now = db.now_iso()

    for doc in documents:
        status = doc.get("status") or "new"
        event_id = doc.get("event_id") or ""
        history = stamps.get(event_id, {})
        found = doc.get("timestamp") or doc.get("created_at")

        review = _hours_between(found, doc.get("approved_at") or history.get("confirmed"))
        if review is not None:
            to_review.append(review)

        repair = _hours_between(
            doc.get("sent_at") or history.get("sent") or doc.get("external_ticket_created_at"),
            history.get("repaired"),
        )
        if repair is not None:
            to_repair.append(repair)

        cycle = _hours_between(found, history.get("closed"))
        if cycle is not None:
            full_cycle.append(cycle)

        if doc.get("last_delivery_ok"):
            delivered += 1
        elif doc.get("last_delivery_at"):
            delivery_failed += 1

        # Кеңістіктік байланыс (SDR / ЦС ГГ НИПД). Желіге шықпаймыз —
        # кэштегісін ғана санаймыз, әйтпесе бет ашылуы ондаған секундқа
        # созылып кетер еді.
        lat = doc.get("display_lat") or doc.get("lat")
        lon = doc.get("display_lon") or doc.get("lon")
        if lat is not None and lon is not None:
            with_coords += 1
            try:
                if spatial.bind(lat, lon, allow_network=False).get("bound"):
                    bound += 1
            except Exception:              # noqa: BLE001
                pass

        if status == "closed":
            closed += 1
        if status == "reopened":
            reopened += 1
        if status in _OPEN_STATES:
            open_now += 1
            age = _hours_between(found, now)
            # 72 сағат — пилот кезеңіндегі шартты SLA
            if age is not None and age > 72:
                overdue += 1
            if age is not None and (oldest_open is None or age > oldest_open["hours"]):
                oldest_open = {
                    "event_id": event_id,
                    "hours": round(age, 1),
                    "address": doc.get("display_address_text") or doc.get("address_text") or "",
                    "severity": doc.get("severity") or "low",
                }

    total = len(documents)
    return {
        "open": open_now,
        "closed": closed,
        "reopened": reopened,
        "overdue": overdue,
        "sla_hours": 72,
        "closure_rate": round(closed / total, 3) if total else None,
        "median_to_review_h": _median(to_review),
        "median_to_repair_h": _median(to_repair),
        "median_full_cycle_h": _median(full_cycle),
        "reviewed_count": len(to_review),
        "repaired_count": len(to_repair),
        "delivered": delivered,
        "delivery_failed": delivery_failed,
        "delivery_rate": round(delivered / total, 3) if total else None,
        "spatially_bound": bound,
        "spatial_rate": round(bound / with_coords, 3) if with_coords else None,
        "oldest_open": oldest_open,
        "pipeline": [
            {"key": key, "count": sum(1 for d in documents if (d.get("status") or "new") == key)}
            for key in _PIPELINE
        ],
    }


@app.get("/api/analytics")
def analytics():
    """Аналитика: қай көшеде қандай ақау жиналған, сапа көрсеткіштері."""
    payload = _analytics(db.list_documents(status="all", limit=5000))
    # Картадағы белсенді учаскелер — бұрын оператор хидерінде тұрған сан.
    # Оператордың экраны кезекке арналған, ал сан есепке тиесілі.
    try:
        payload["operations"]["active_zones"] = len(db.list_zones(active_only=True))
    except Exception:                              # noqa: BLE001
        payload["operations"]["active_zones"] = None
    return payload


@app.get("/api/export/gis.geojson")
def export_geojson():
    """ЦС ГГ НИПД / ГАЖ үшін: оқиғалар GeoJSON форматында.

    Әр нысанда координатадан бөлек жол сегментінің байланысы да болады —
    кеңістіктік тізілім дәл соны күтеді.
    """
    from . import spatial

    features = []
    for doc in db.list_documents(status="all", limit=5000):
        lat = doc.get("display_lat") or doc.get("lat")
        lon = doc.get("display_lon") or doc.get("lon")
        if lat is None or lon is None:
            continue
        binding = spatial.bind(lat, lon, allow_network=False)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "event_id": doc.get("event_id"),
                "defect_type": doc.get("class_key"),
                "defect_type_official": doc.get("defect_type_official"),
                "severity": doc.get("severity"),
                "status": doc.get("status"),
                "confidence": doc.get("confidence"),
                "ai_verified": bool(doc.get("ai_verified")),
                "detected_at": doc.get("timestamp"),
                "address": doc.get("display_address_text") or doc.get("address_text"),
                "location_corrected": bool(doc.get("location_corrected")),
                "spatial_id": binding.get("spatial_id"),
                "osm_way_id": binding.get("osm_way_id"),
                "road_name": binding.get("road_name"),
                "road_class": binding.get("road_class"),
                "offset_from_axis_m": binding.get("distance_m"),
                "external_ticket_id": doc.get("external_ticket_id"),
            },
        })
    return {
        "type": "FeatureCollection",
        "name": "AIQYN ZHOL — жол ақаулары",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "generated_at": db.now_iso(),
        "features": features,
    }


@app.get("/api/export/sdr.json")
def export_sdr():
    """SDR (деректерді ұзақ сақтау жүйесі) үшін оқиға журналы.

    Әр жазба — өзгермейтін факт: не, қашан, қайда табылды, кім растады,
    қандай тікет жасалды. Тізілім осындай құрылымды күтеді.
    """
    from . import spatial

    records = []
    for doc in db.list_documents(status="all", limit=5000):
        lat = doc.get("display_lat") or doc.get("lat")
        lon = doc.get("display_lon") or doc.get("lon")
        binding = spatial.bind(lat, lon, allow_network=False) if lat and lon else {}
        records.append({
            "record_id": doc.get("event_id"),
            "record_type": "road_defect",
            "source_system": "AIQYN ZHOL",
            "detected_at": doc.get("timestamp"),
            "registered_at": doc.get("created_at"),
            "geometry": {"lat": lat, "lon": lon,
                         "accuracy_m": doc.get("gps_accuracy_m")},
            "spatial_binding": {
                "spatial_id": binding.get("spatial_id"),
                "road_name": binding.get("road_name"),
                "road_class": binding.get("road_class"),
                "offset_m": binding.get("distance_m"),
            },
            "classification": {
                "type": doc.get("class_key"),
                "official": doc.get("defect_type_official"),
                "severity": doc.get("severity"),
                "model_confidence": doc.get("confidence"),
            },
            "verification": {
                "ai_checked": bool(doc.get("ai_note")),
                "ai_verified": bool(doc.get("ai_verified")),
                "ai_confidence": doc.get("ai_confidence"),
                "operator": doc.get("confirmed_by"),
                "operator_at": doc.get("confirmed_at"),
            },
            "workflow": {
                "status": doc.get("status"),
                "external_ticket_id": doc.get("external_ticket_id"),
                "responsible_org": doc.get("responsible_org"),
            },
            "evidence": {
                "photo": bool(doc.get("photo_file")),
                "video": bool(doc.get("video_file")),
            },
        })
    return {"system": "SDR-compatible event log", "count": len(records),
            "generated_at": db.now_iso(), "records": records}


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

@app.get("/api/documents/{event_id}/pdf")
def download_pdf(event_id: str):
    """Ресми өтінімнің PDF нұсқасы.

    109-ға WhatsApp арқылы жүгінгенде хабарламада осы файлға сілтеме
    тұрады: .docx телефоннан ашылмайды, ал PDF кез келген жерде ашылады.
    """
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")

    from . import delivery
    from .pdfgen import build_pdf, safe_filename

    try:
        buffer = build_pdf(document, MEDIA_DIR, delivery._portal_url())
    except Exception as exc:
        log.exception("PDF жасалмады: %s", exc)
        raise HTTPException(status_code=500, detail=f"PDF жасалмады: {exc}")

    return StreamingResponse(
        buffer, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe_filename(event_id)}"'},
    )


@app.get("/api/documents/{event_id}/ikomek")
def ikomek_handoff(event_id: str):
    """iKomek 109-ға жүгінудің дайын сілтемелері мен мәтіні."""
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")

    from . import delivery, spatial

    lat = document.get("display_lat") or document.get("lat")
    lon = document.get("display_lon") or document.get("lon")
    document = {**document, "spatial": spatial.bind(lat, lon, allow_network=False)}
    return delivery.ikomek_links(document)


@app.get("/api/documents/{event_id}/whatsapp")
def whatsapp_handoff(event_id: str):
    """Өтінімді WhatsApp арқылы жіберуге дайын сілтеме.

    Бұл — ЕШҚАНДАЙ кілтсіз жұмыс істейтін жалғыз нақты арна. Жүйе
    хабарламаның толық мәтінін дайындап береді, оператор бір басып
    жібереді — хабар шынымен жетеді. Ресми 109 API-і ашылғанша
    жөндеу бригадасына хабарлаудың ең қысқа жолы осы.
    """
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")

    from . import delivery

    recipients = delivery.resolve_recipients(document)
    return {
        "event_id": event_id,
        "phone": recipients.get("whatsapp") or "",
        "text": delivery.whatsapp_text(document),
        "link": delivery.whatsapp_link(document, recipients.get("whatsapp", "")),
    }


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

SITEMAP_PAGES = (("/", "1.0"), ("/portal", "0.8"), ("/analytics", "0.6"))
SITEMAP_LASTMOD = "2026-09-30"


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    """Іздеу жүйелеріне арналған нұсқау.

    Vercel конфигурациясы әр сұрауды осы функцияға жібереді, сондықтан
    robots.txt статикалық файл емес, маршрут ретінде беріледі — әйтпесе
    іздеу роботы оның орнына басты беттің HTML-ін алар еді.
    """
    return Response(content=ROBOTS_TXT, media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml():
    urls = "".join(
        "  <url>\n"
        f"    <loc>{ORIGIN}{loc}</loc>\n"
        f"    <lastmod>{SITEMAP_LASTMOD}</lastmod>\n"
        "    <changefreq>weekly</changefreq>\n"
        f"    <priority>{priority}</priority>\n"
        "  </url>\n"
        for loc, priority in SITEMAP_PAGES
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + urls
        + "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


def _brand_asset(name: str, media_type: str):
    path = STATIC_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Файл табылмады")
    return FileResponse(path, media_type=media_type,
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    return _brand_asset("favicon.svg", "image/svg+xml")


@app.get("/og-image.png", include_in_schema=False)
def og_image():
    """Сілтеме бөліскенде көрінетін сурет (1200x630)."""
    return _brand_asset("og-image.png", "image/png")


@app.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon():
    return _brand_asset("apple-touch-icon.png", "image/png")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Браузер бұл файлды бет сайын сұрайды. Болмаса — журнал 404-пен толады.

    SVG қайтарамыз: .ico атауына қарамастан браузерлер media_type-ты
    қабылдайды, әрі кез келген экранда анық көрінеді.
    """
    from .logo import svg_icon
    return Response(
        content=svg_icon(size=64),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/media/{event_id}/{filename}")
def get_media(event_id: str, filename: str):
    path = (MEDIA_DIR / event_id / filename).resolve()
    # Қалтадан тыс шығуға жол бермеу
    if not str(path).startswith(str(MEDIA_DIR.resolve())) or not path.exists():
        raise HTTPException(status_code=404, detail="Файл табылмады")
    return FileResponse(path)


_ASSET_REF = re.compile(r'(?:href|src)="(/static/[^"?#]+\.(?:css|js))"')

# Vercel жайғастырғанда барлық файлдың mtime-ы БІР тұрақты мәнге
# теңестіріледі. Сондықтан тек mtime-ға сүйенсек, әр жаңа деплойда
# нұсқа өзгермей қалады да, браузер ескі CSS-ті кэштен беріп отырады.
# Жайғастыру идентификаторы бар болса, соны нұсқаға қосамыз.
_BUILD_ID = (
    os.environ.get("VERCEL_DEPLOYMENT_ID")
    or os.environ.get("VERCEL_GIT_COMMIT_SHA")
    or ""
)[-10:]


def _bust_cache(html: str) -> str:
    """CSS/JS сілтемелеріне нұсқа белгісін қосу.

    Себебі: стильді түзетіп, бетті жаңартқанда браузер ескі файлды
    кэштен беріп қоюы мүмкін — «түзеттім, бірақ өзгермеді» деген ең
    қолайсыз сәт. Нұсқа өзгергенде браузер міндетті түрде жаңасын сұрайды.
    """
    def stamp(match: re.Match) -> str:
        rel = match.group(1)
        path = STATIC_DIR / rel[len("/static/"):]
        try:
            version = str(int(path.stat().st_mtime))
        except OSError:
            return match.group(0)
        if _BUILD_ID:
            version = f"{version}-{_BUILD_ID}"
        return match.group(0).replace(rel, f"{rel}?v={version}")

    return _ASSET_REF.sub(stamp, html)


def _serve_template(name: str) -> HTMLResponse:
    page = TEMPLATES_DIR / name
    if not page.exists():
        return HTMLResponse(f"<h1>{name} табылмады</h1>", status_code=500)
    html = page.read_text(encoding="utf-8")

    # Логотип БІР жерде — portal/logo.py ішінде — анықталады. Бетте оның
    # орны ғана тұрады; осында SVG болып қойылады. Сондықтан логотипті
    # өзгертсең, барлық бетте бірден жаңарады.
    if "__LOGO_" in html:
        from .logo import svg_icon
        html = html.replace("__LOGO_BIG__", svg_icon(128, uid="boot"))
        # Әр логотипке бөлек clipPath id: бір бетте бірнешеуі тұрады
        # (хидер, футер), ал қайталанған id жарамсыз HTML болып саналады
        counter = [0]

        def small(_match) -> str:
            counter[0] += 1
            return svg_icon(30, uid=f"s{counter[0]}")

        html = re.sub(r"__LOGO_SM__", small, html)
    return HTMLResponse(_bust_cache(html))


@app.get("/", response_class=HTMLResponse)
def index():
    return _serve_template("index.html")


# ============================================================
#  Тұрғындардың хабарламасы
# ============================================================

@app.post("/api/report")
async def citizen_report(
    address: str = Form(...),
    note: str = Form(""),
    contact: str = Form(""),
    photo: UploadFile | None = File(None),
):
    """Тұрғын жіберген ақау туралы хабарлама.

    Патруль көлігі әлі өтпеген көшені жүйе білуі керек. Хабарлама
    оператордың тізіміне түседі де, сол ретпен тексеріледі.
    """
    address = (address or "").strip()
    if len(address) < 4:
        raise HTTPException(status_code=400, detail="Мекенжайды толық жазыңыз.")

    report_id = "R-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    saved_photo = None

    if photo is not None and photo.filename:
        data = await photo.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Сурет 10 МБ-тан аспауы керек.")
        if READ_ONLY:
            # Vercel — тек көрсетуге арналған айна, файл сақтайтын жер жоқ
            saved_photo = None
        else:
            folder = MEDIA_DIR / "reports"
            folder.mkdir(parents=True, exist_ok=True)
            suffix = Path(photo.filename).suffix.lower() or ".jpg"
            if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
                raise HTTPException(status_code=400, detail="Тек JPG, PNG немесе WEBP.")
            target = folder / f"{report_id}{suffix}"
            target.write_bytes(data)
            saved_photo = f"/media/reports/{target.name}"

    record = {
        "id": report_id,
        "address": address[:300],
        "note": (note or "").strip()[:1000],
        "contact": (contact or "").strip()[:120],
        "photo": saved_photo,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": "citizen",
    }

    if not READ_ONLY:
        try:
            line = json.dumps(record, ensure_ascii=False)
            with open(ROOT / "citizen_reports.jsonl", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass   # хабарлама жоғалса да пайдаланушыға қате көрсетпейміз

    log.info("Тұрғын хабарламасы: %s | %s", report_id, address[:60])
    return {"ok": True, "id": report_id, "photo_saved": bool(saved_photo)}


# ============================================================
#  Іздеу: мекенжай / сілтеме → картадағы нүкте
# ============================================================

# Шымкенттің шамамен алаңы — іздеу нәтижесін қаламен шектеу үшін
SHYMKENT_BOX = (42.10, 69.30, 42.48, 69.94)      # lat_min, lon_min, lat_max, lon_max


def _coords_from_text(text: str) -> Optional[tuple[float, float]]:
    """Мәтіннен координата шығару.

    Оператор картаға сілтемені жиі көшіріп әкеледі. Үш пішім танылады:
      · «42.252761, 69.725065»          — жай координата
      · Google Maps  ?q=42.25,69.72  немесе  @42.25,69.72,17z
      · 2GIS         ?m=69.72%2C42.25%2F16   (мұнда РЕТІ КЕРІ: lon,lat)
    """
    text = urllib.parse.unquote(text or "").strip()
    if not text:
        return None

    # 2GIS: m=<lon>,<lat>/<zoom>
    match = re.search(r"[?&]m=(-?\d+\.?\d*)[,%]+(-?\d+\.?\d*)", text)
    if match:
        lon, lat = float(match.group(1)), float(match.group(2))
        return (lat, lon)

    # Google: @lat,lon  немесе  q=lat,lon  немесе жай «lat, lon»
    match = re.search(r"(-?\d{1,3}\.\d{3,})\s*[,;]\s*(-?\d{1,3}\.\d{3,})", text)
    if match:
        first, second = float(match.group(1)), float(match.group(2))
        # Қайсысы ендік екенін мәннің өзінен ажыратамыз
        if -90 <= first <= 90 and abs(second) > 90:
            return (first, second)
        if -90 <= second <= 90 and abs(first) > 90:
            return (second, first)
        return (first, second)
    return None


@app.get("/api/geocode")
def geocode_search(q: str = ""):
    """Мекенжай немесе сілтеме бойынша нүкте табу.

    Дереккөз — OpenStreetMap (Nominatim), ТЗ-дағы «ашық API» талабына сай.
    Нәтиже Шымкент шегімен шектеледі: басқа қаланың көшесі шығып,
    оператор қате жерге оқиға қоймауы үшін.
    """
    query = (q or "").strip()
    if len(query) < 3:
        return {"query": query, "results": []}

    direct = _coords_from_text(query)
    if direct:
        lat, lon = direct
        return {"query": query, "results": [{
            "label": f"Координата {lat:.6f}, {lon:.6f}",
            "detail": "сілтемеден немесе қолмен енгізілген нүкте",
            "lat": lat, "lon": lon, "source": "coords",
            "in_city": SHYMKENT_BOX[0] <= lat <= SHYMKENT_BOX[2]
                       and SHYMKENT_BOX[1] <= lon <= SHYMKENT_BOX[3],
        }]}

    import requests

    lat_min, lon_min, lat_max, lon_max = SHYMKENT_BOX
    results: list[dict] = []

    def in_city(lat: float, lon: float) -> bool:
        return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max

    # 1) 2ГИС — Шымкент көшелерін әлдеқайда жақсы біледі. Nominatim
    #    «Қаражал көшесі» дегенді Астанадан тауып береді, ал 2ГИС
    #    дұрыс қаладан табады.
    key = os.getenv("AIQYN_2GIS_KEY", "").strip()
    if key:
        try:
            response = requests.get(
                "https://catalog.api.2gis.com/3.0/items/geocode",
                params={"q": f"{query}, Шымкент", "key": key, "page_size": 8,
                        "fields": "items.point,items.full_name,items.address"},
                timeout=12,
            )
            for item in ((response.json().get("result") or {}).get("items") or []):
                point = item.get("point") or {}
                lat, lon = point.get("lat"), point.get("lon")
                if lat is None or lon is None or not in_city(float(lat), float(lon)):
                    continue
                results.append({
                    "label": item.get("name") or item.get("full_name") or query,
                    "detail": item.get("full_name") or (item.get("address") or {}).get("name") or "",
                    "lat": float(lat), "lon": float(lon),
                    "source": "2gis", "in_city": True,
                })
        except Exception as exc:                   # noqa: BLE001
            log.info("2ГИС іздеуі сәтсіз: %s", exc)

    # 2) Nominatim — резерв: 2ГИС кілті болмаса да іздеу жұмыс істеуі керек
    if not results:
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "jsonv2", "limit": 8,
                        "accept-language": "kk,ru,en",
                        "viewbox": f"{lon_min},{lat_max},{lon_max},{lat_min}"},
                headers={"User-Agent": "AIQYN/1.0 (road monitoring, Shymkent)"},
                timeout=12,
            )
            for row in (response.json() if response.status_code < 400 else []):
                try:
                    lat, lon = float(row["lat"]), float(row["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not in_city(lat, lon):
                    continue
                full = row.get("display_name") or ""
                results.append({
                    "label": row.get("name") or full.split(",")[0],
                    "detail": full, "lat": lat, "lon": lon,
                    "source": "osm", "in_city": True,
                })
        except Exception as exc:                   # noqa: BLE001
            log.warning("Геокодтау сәтсіз: %s", exc)
            return {"query": query, "results": [],
                    "error": "Іздеу қызметіне қосылу мүмкін болмады"}

    return {"query": query, "results": results}


# ============================================================
#  Қолмен оқиға енгізу
# ============================================================

@app.post("/api/documents/manual")
async def create_manual_document(
    class_key: str = Form(...),
    lat: float = Form(...),
    lon: float = Form(...),
    address_text: str = Form(""),
    severity: str = Form(""),
    note: str = Form(""),
    reporter: str = Form(""),
    photo: UploadFile | None = File(None),
    operator: str = Depends(require_operator),
):
    """Операторлық қолмен енгізу.

    Не үшін: патруль көлігі әлі өтпеген көшедегі ақау туралы тұрғын да,
    диспетчер де хабарлауы мүмкін. Ондай оқиға жүйеге кірмей қалса,
    карта толық болмайды.

    Автоматты оқиғадан айырмашылығы АШЫҚ ЖАЗЫЛАДЫ: дереккөз «қолмен»
    деп белгіленеді, ал модель сенімділігі берілмейді — оны адам
    енгізген, модель емес. Фото болса, ИИ-тексеруші оны да қарайды.
    """
    if READ_ONLY:
        raise HTTPException(status_code=503,
                            detail="Демо айнасы тек оқуға арналған — жаңа оқиға қабылданбайды")

    from vision import categories

    if class_key not in categories.CATEGORIES:
        raise HTTPException(status_code=400, detail="Ақау түрі белгісіз")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Координата жарамсыз")

    category = categories.get(class_key)
    severity = severity if severity in ("low", "medium", "high") else category.base_severity
    stamp = datetime.now()
    event_id = f"AIQYN-{stamp.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"

    # Мекенжай берілмесе, координатадан кері геокодпен аламыз
    address = (address_text or "").strip()
    if not address:
        try:
            from vision.config import Config
            from vision.geocode import Geocoder
            address = (Geocoder(Config()).reverse(lat, lon) or {}).get("address_text") or ""
        except Exception:                          # noqa: BLE001
            address = ""
    if not address:
        address = f"Шымкент, координата бойынша: {lat:.5f}, {lon:.5f}"

    photo_name = None
    folder = MEDIA_DIR / event_id
    if photo is not None and photo.filename:
        data = await photo.read()
        if len(data) > 12 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Сурет 12 МБ-тан аспауы керек")
        suffix = Path(photo.filename).suffix.lower() or ".jpg"
        if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
            raise HTTPException(status_code=400, detail="Тек JPG, PNG немесе WEBP")
        folder.mkdir(parents=True, exist_ok=True)
        photo_name = f"photo{suffix}"
        (folder / photo_name).write_bytes(data)

    human = (note or "").strip()
    description = (
        f"Оқиға операторлық интерфейсте ҚОЛМЕН тіркелді "
        f"({stamp.strftime('%d.%m.%Y %H:%M')}).\n\n"
        f"Ақау түрі: {category.kk}\n"
        f"Орны: {address}\n"
        f"Координата: {lat:.6f}, {lon:.6f}\n"
        f"Тіркеген: {operator}"
        + (f"\nХабарлаушы: {reporter.strip()}" if reporter.strip() else "")
        + (f"\n\nСипаттама: {human}" if human else "")
        + "\n\nЕскерту: бұл жазба автоматты детекция емес. Жауапты органға "
          "жіберілер алдында оператор орнын да, сипаттамасын да тексеруі тиіс."
    )

    payload = {
        "event_id": event_id,
        "class_key": class_key,
        "defect_type_official": category.kk,
        "defect_type_official_ru": category.ru,
        "severity": severity,
        "severity_kk": categories.SEVERITY_KK.get(severity, severity),
        "lat": lat,
        "lon": lon,
        "address_text": address,
        "gps_trusted": True,
        "gps_source": "қолмен енгізілген",
        "gps_accuracy_m": None,
        "confidence": None,
        "timestamp": db.now_iso(),
        "timestamp_human": stamp.strftime("%d.%m.%Y %H:%M:%S"),
        "description_text": description,
        # Қолмен енгізілген жазба автоматтыдан АЖЫРАТЫЛУЫ керек: оны
        # адам жазған, детектор тапқан жоқ
        "source_type": "manual",
        "detector": "оператор",
        "created_by": operator,
        "reporter": (reporter or "").strip()[:160],
        **categories.org_for(class_key),
    }

    # ИИ-тексеру: фото болса ғана мағыналы
    if photo_name:
        try:
            from vision.aiverify import AIVerifier
            from vision.config import Config
            verdict = AIVerifier(Config()).verify(folder / photo_name, category.kk)
            if verdict is not None:
                payload["ai_verified"] = bool(getattr(verdict, "is_defect", False))
                payload["ai_confidence"] = getattr(verdict, "confidence", None)
                payload["ai_note"] = getattr(verdict, "note", "") or getattr(verdict, "reason", "")
                payload["ai_model"] = getattr(verdict, "model", "")
        except Exception as exc:                   # noqa: BLE001
            log.info("Қолмен енгізілген оқиғаны ИИ тексере алмады: %s", exc)

    db.insert_document(payload, photo_name, None)
    db.set_status(event_id, "new", f"Қолмен тіркелді ({operator})", operator)
    log.info("Қолмен оқиға тіркелді: %s | %s | %s", event_id, class_key, address[:60])

    return {"ok": True, "event_id": event_id, "address_text": address,
            "ai_verified": payload.get("ai_verified"),
            "ai_note": payload.get("ai_note", "")}


@app.post("/api/documents/from-photo")
async def create_from_photo(
    photo: UploadFile = File(...),
    lat: float = Form(None),
    lon: float = Form(None),
    note: str = Form(""),
    operator: str = Depends(require_operator),
):
    """Суретті жүктеу — қалғанын жүйе өзі істейді.

    Тізбек қолмен енгізуден МҮЛДЕ басқа: мұнда адам ештеңе таңдамайды.

        сурет → EXIF-тен GPS → детектор ақауды табады → қызыл рамка
              → 2ГИС мекенжайы → ИИ-тексеруші → құжат → карта

    Ақау табылмаса — оқиға ЖАСАЛМАЙДЫ. «Бірдеңе тапқан болып» жазба
    қосу жүйеге деген сенімді жояды.

    Координата: алдымен суреттің өзінен (EXIF), болмаса оператор
    картадан көрсеткен нүкте. Екеуі де жоқ болса — қате.
    """
    if READ_ONLY:
        raise HTTPException(status_code=503,
                            detail="Демо айнасы тек оқуға арналған")

    data = await photo.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Сурет 25 МБ-тан аспауы керек")

    import sys
    sys.path.insert(0, str(ROOT.parent))
    try:
        from scripts.ingest_photos import (
            build_event_from_image, detector_bank, load_image_bytes,
        )
    except Exception as exc:                       # noqa: BLE001
        log.exception("Детектор жүктелмеді: %s", exc)
        raise HTTPException(status_code=503,
                            detail="Бұл серверде детектор қолжетімсіз (AI бөлігі орнатылмаған)")

    try:
        frame, exif_coords, shot_at = load_image_bytes(data, photo.filename or "photo.jpg")
    except Exception as exc:                       # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Сурет оқылмады: {exc}")

    coords = exif_coords or ((lat, lon) if lat is not None and lon is not None else None)
    if not coords:
        raise HTTPException(
            status_code=422,
            detail="Суретте GPS жоқ. Картадан орнын көрсетіңіз немесе "
                   "геолокациясы қосулы телефонмен түсірілген суретті жүктеңіз.")

    try:
        result = build_event_from_image(
            frame, coords, shot_at, MEDIA_DIR,
            bank=detector_bank(), note=note, actor=operator,
        )
    except Exception as exc:                       # noqa: BLE001
        log.exception("Сурет өңделмеді: %s", exc)
        raise HTTPException(status_code=500, detail=f"Өңделмеді: {exc}")

    if result is None:
        return JSONResponse(status_code=200, content={
            "ok": False,
            "reason": "no_defect",
            "detail": "Бұл суретте жүйе ақау таппады — оқиға жасалмады.",
            "lat": coords[0], "lon": coords[1],
        })

    log.info("Суреттен оқиға: %s (%s)", result["event_id"], operator)
    return {"ok": True, **result}


@app.get("/api/categories")
def defect_categories():
    """Қолмен енгізу формасына арналған ақау түрлерінің тізімі."""
    from vision import categories
    return {"categories": [
        {"key": key, "kk": item.kk, "ru": item.ru, "severity": item.base_severity}
        for key, item in categories.CATEGORIES.items()
    ], "severities": categories.SEVERITY_KK}


@app.get("/portal", response_class=HTMLResponse)
def portal():
    return _serve_template("map.html")


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page():
    """Жол инфрақұрылымын басқаруға арналған аналитика беті."""
    return _serve_template("analytics.html")


@app.get("/documents/{event_id}/print", response_class=HTMLResponse)
def print_document(event_id: str):
    """Ресми құжаттың басып шығаруға дайын нұсқасы (браузерден PDF жасауға болады)."""
    document = db.get_document(event_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Құжат табылмады")

    page = TEMPLATES_DIR / "print.html"
    if not page.exists():
        return HTMLResponse("<h1>print.html табылмады</h1>", status_code=500)

    from .logo import svg_icon

    html = page.read_text(encoding="utf-8")
    html = html.replace("__LOGO_SM__", svg_icon(46, with_frame=False)
                        .replace("<svg ", '<svg class="logo" ', 1))
    return HTMLResponse(html.replace("__EVENT_ID__", event_id))


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return JSONResponse(status_code=404, content={"ok": False, "detail": str(exc.detail)})
