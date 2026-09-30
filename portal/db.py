"""AIQYN Portal — дерекқор (SQLite).

Сайттың рөлі: ДАЙЫН құжатты сол күйінде сақтау. Мұнда ешқандай есептеу,
классификация немесе мәтін генерациясы жоқ — бәрі AIQYN Vision
программасында бұрын орындалған.

Статус тізбегі:
    new -> confirmed -> sent -> in_progress -> repaired -> closed
        -> reopened -> in_progress
        -> rejected
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent

# Дерекқордың орны. Vercel сияқты serverless ортада жоба қалтасы «тек оқу»
# режимінде болады, ал /tmp жазуға ашық. Сондықтан жолды сырттан беруге
# мүмкіндік береміз.
DB_PATH = Path(os.getenv("AIQYN_DB_PATH") or (ROOT / "aiqyn.db"))

# Демо суреті: бос дерекқорға автоматты жүктелетін дайын деректер.
# Осының арқасында Vercel-де бірінші ашқанда-ақ карта толы болады.
DEMO_SNAPSHOT = Path(os.getenv("AIQYN_DEMO_SNAPSHOT") or (ROOT / "demo" / "snapshot.json"))
SHYMKENT_TZ = timezone(timedelta(hours=5))

STATUSES = (
    "new", "confirmed", "sent", "in_progress", "repaired",
    "closed", "reopened", "rejected",
)

# Рұқсат етілген ресми жұмыс ағыны. Бір статусты қайта жазуға болады
# (мысалы, қайта жіберу), бірақ артқа еркін секіруге болмайды.
STATUS_TRANSITIONS = {
    "new": {"confirmed", "rejected"},
    "confirmed": {"sent", "rejected"},
    "sent": {"in_progress"},
    "in_progress": {"repaired"},
    # «Жөнделді» — әлі жабылған жоқ: тәуелсіз қайта тексеру қажет.
    "repaired": {"closed", "reopened"},
    "reopened": {"in_progress"},
    "closed": set(),
    "rejected": {"new"},
}

STATUS_LABELS = {
    "new": {"kk": "Жаңа — тексерілмеген", "ru": "Новое — не проверено"},
    "confirmed": {"kk": "Расталды", "ru": "Подтверждено"},
    "sent": {"kk": "Органға жіберілді", "ru": "Отправлено в орган"},
    "in_progress": {"kk": "Орындалуда", "ru": "В работе"},
    "repaired": {"kk": "Қайта тексеруде", "ru": "На повторной проверке"},
    "reopened": {"kk": "Қайта ашылды", "ru": "Открыто повторно"},
    "closed": {"kk": "Тексерілді және жабылды", "ru": "Проверено и закрыто"},
    "rejected": {"kk": "Қабылданбады", "ru": "Отклонено"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    event_id                TEXT PRIMARY KEY,
    status                  TEXT NOT NULL DEFAULT 'new',

    defect_type_official    TEXT,
    defect_type_official_ru TEXT,
    class_key               TEXT,
    description_text        TEXT,
    description_text_ru     TEXT,

    address_text            TEXT,
    map_link                TEXT,
    map_link_2gis           TEXT,
    lat                     REAL,
    lon                     REAL,
    marked_on_road          INTEGER DEFAULT 0,

    original_lat            REAL,
    original_lon            REAL,
    original_address_text   TEXT,
    display_lat             REAL,
    display_lon             REAL,
    display_address_text    TEXT,
    location_corrected      INTEGER DEFAULT 0,
    location_corrected_at   TEXT,
    location_corrected_by   TEXT,
    location_correction_note TEXT,

    severity                TEXT,
    severity_kk             TEXT,
    confidence              REAL,
    timestamp               TEXT,
    timestamp_human         TEXT,

    responsible_org         TEXT,
    responsible_org_ru      TEXT,
    responsible_email       TEXT,

    detector                TEXT,
    ai_verified             INTEGER DEFAULT 0,
    ai_confidence           REAL DEFAULT 0,
    ai_note                 TEXT,
    ai_model                TEXT,
    ai_size                 TEXT,
    ai_location             TEXT,
    ai_danger               TEXT,
    ai_action               TEXT,
    ai_urgency_days         INTEGER DEFAULT 0,
    gps_source              TEXT,
    gps_trusted             INTEGER DEFAULT 1,
    gps_accuracy_m          REAL,
    video_seconds           REAL,
    source_type             TEXT,
    app_version             TEXT,

    photo_file              TEXT,
    video_file              TEXT,
    after_photo_file        TEXT,

    raw_json                TEXT,
    created_at              TEXT,
    updated_at              TEXT,
    sent_at                 TEXT,
    approved_at             TEXT,
    approved_by             TEXT,

    external_ticket_source  TEXT,
    external_ticket_id      TEXT,
    external_ticket_status  TEXT,
    external_ticket_created_at TEXT,
    last_delivery_at        TEXT,
    last_delivery_ok        INTEGER DEFAULT 0,
    delivery_error          TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_created ON documents(created_at);

-- Жөндеу аймақтары / жабық жолдар.
-- Оператор картадан белгілейді, AIQYN Vision оларды жүктеп алады да,
-- сол аймақтағы ақаулар бойынша ҚҰЖАТ ҚҰРМАЙДЫ (жөндеу бұрыннан жүріп жатыр).
CREATE TABLE IF NOT EXISTS zones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'repair',   -- repair | closed | ignore
    shape       TEXT NOT NULL DEFAULT 'circle',   -- circle | polygon
    lat         REAL,
    lon         REAL,
    radius_m    REAL DEFAULT 100,
    corridor_m  REAL,
    polygon     TEXT,                             -- [[lat,lon], ...] JSON
    active      INTEGER NOT NULL DEFAULT 1,
    valid_from  TEXT,
    valid_until TEXT,                             -- жөндеу аяқталатын күн (бос болса — мерзімсіз)
    note        TEXT,
    source_url  TEXT,
    external_ref TEXT,
    responsible_org TEXT,
    boundary_verified INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT NOT NULL,
    updated_by  TEXT,
    updated_at  TEXT,
    deactivated_by TEXT,
    deactivated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_zones_active ON zones(active);

CREATE TABLE IF NOT EXISTS status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT,
    actor       TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_event ON status_history(event_id);

CREATE TABLE IF NOT EXISTS location_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL,
    previous_lat    REAL,
    previous_lon    REAL,
    previous_address TEXT,
    new_lat         REAL NOT NULL,
    new_lon         REAL NOT NULL,
    new_address     TEXT,
    note            TEXT NOT NULL,
    actor           TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_location_history_event
ON location_history(event_id);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL,
    ticket_source   TEXT,
    ticket_id       TEXT,
    delivery_status TEXT,
    mock_109        INTEGER DEFAULT 0,
    email           INTEGER DEFAULT 0,
    email_error     TEXT,
    actor           TEXT,
    response_json   TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delivery_event
ON delivery_attempts(event_id);
"""


def now_iso() -> str:
    return datetime.now(SHYMKENT_TZ).isoformat()


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    # PRAGMA journal_mode нәтиже қатары аяқталмай қалса, Windows файлды ашық
    # ұстайды. fetchone() cursor-ды дереу бітіреді.
    connection.execute("PRAGMA journal_mode=WAL").fetchone()
    return connection


# Кейін қосылған бағандар: бұрыннан бар дерекқор да жаңарып отырсын
MIGRATIONS = [
    ("documents", "ai_verified", "INTEGER DEFAULT 0"),
    ("documents", "ai_confidence", "REAL DEFAULT 0"),
    ("documents", "ai_note", "TEXT"),
    ("documents", "ai_model", "TEXT"),
    ("documents", "ai_size", "TEXT"),
    ("documents", "ai_location", "TEXT"),
    ("documents", "ai_danger", "TEXT"),
    ("documents", "ai_action", "TEXT"),
    ("documents", "ai_urgency_days", "INTEGER DEFAULT 0"),
    ("documents", "original_lat", "REAL"),
    ("documents", "original_lon", "REAL"),
    ("documents", "original_address_text", "TEXT"),
    ("documents", "display_lat", "REAL"),
    ("documents", "display_lon", "REAL"),
    ("documents", "display_address_text", "TEXT"),
    ("documents", "location_corrected", "INTEGER DEFAULT 0"),
    ("documents", "location_corrected_at", "TEXT"),
    ("documents", "location_corrected_by", "TEXT"),
    ("documents", "location_correction_note", "TEXT"),
    ("documents", "approved_at", "TEXT"),
    ("documents", "approved_by", "TEXT"),
    ("documents", "external_ticket_source", "TEXT"),
    ("documents", "external_ticket_id", "TEXT"),
    ("documents", "external_ticket_status", "TEXT"),
    ("documents", "external_ticket_created_at", "TEXT"),
    ("documents", "last_delivery_at", "TEXT"),
    ("documents", "last_delivery_ok", "INTEGER DEFAULT 0"),
    ("documents", "delivery_error", "TEXT"),
    ("zones", "corridor_m", "REAL"),
    ("zones", "valid_from", "TEXT"),
    ("zones", "source_url", "TEXT"),
    ("zones", "external_ref", "TEXT"),
    ("zones", "responsible_org", "TEXT"),
    ("zones", "boundary_verified", "INTEGER NOT NULL DEFAULT 0"),
    ("zones", "updated_by", "TEXT"),
    ("zones", "updated_at", "TEXT"),
    ("zones", "deactivated_by", "TEXT"),
    ("zones", "deactivated_at", "TEXT"),
]


def init_db() -> None:
    with closing(connect()) as connection, connection:
        connection.executescript(SCHEMA)

        for table, column, definition in MIGRATIONS:
            existing = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        # Бұрынғы жазбаларды жоймай, бастапқы және картада көрсетілетін
        # координаталарды бір рет толтырамыз.
        connection.execute(
            "UPDATE documents SET "
            "original_lat = COALESCE(original_lat, lat), "
            "original_lon = COALESCE(original_lon, lon), "
            "original_address_text = COALESCE(original_address_text, address_text), "
            "display_lat = COALESCE(display_lat, lat), "
            "display_lon = COALESCE(display_lon, lon), "
            "display_address_text = COALESCE(display_address_text, address_text)"
        )
        connection.execute(
            "UPDATE documents SET "
            "approved_at = COALESCE(approved_at, sent_at, updated_at, created_at), "
            "approved_by = COALESCE(approved_by, 'legacy') "
            "WHERE status IN ('confirmed', 'sent', 'in_progress', 'repaired')"
        )
        connection.execute(
            "UPDATE zones SET updated_at = COALESCE(updated_at, created_at), "
            "updated_by = COALESCE(updated_by, created_by)"
        )

    _load_demo_if_empty()


def _load_demo_if_empty() -> None:
    """Дерекқор бос әрі демо суреті бар болса — оны жүктеу.

    Vercel-де әр «суық старт» жаңа контейнер береді, дерекқор жоғалады.
    Сондықтан жоба ашылған сайын демо деректері автоматты қалпына келеді:
    жюри сілтемені ашқанда карта әрқашан толы болады.
    """
    if not DEMO_SNAPSHOT.exists():
        return

    with closing(connect()) as connection:
        count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    if count:
        return

    try:
        data = json.loads(DEMO_SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return

    for document in data.get("documents", []):
        try:
            insert_document(
                document,
                document.get("photo_file"),
                document.get("video_file"),
            )
            if document.get("status") and document["status"] != "new":
                set_status(document["event_id"], document["status"],
                           "демо деректері", "system")
        except Exception:
            continue

    for zone in data.get("zones", []):
        try:
            insert_zone(zone, actor="demo")
        except Exception:
            continue


# ============================================================
#  Жазу
# ============================================================

FIELDS = [
    "defect_type_official", "defect_type_official_ru", "class_key",
    "description_text", "description_text_ru",
    "address_text", "map_link", "map_link_2gis", "lat", "lon", "marked_on_road",
    "severity", "severity_kk", "confidence", "timestamp", "timestamp_human",
    "responsible_org", "responsible_org_ru", "responsible_email",
    "detector", "gps_source", "gps_trusted", "gps_accuracy_m",
    "video_seconds", "source_type", "app_version",
    "ai_verified", "ai_confidence", "ai_note", "ai_model",
    "ai_size", "ai_location", "ai_danger", "ai_action", "ai_urgency_days",
]


def _as_bool_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    return 1 if str(value).strip().lower() in ("1", "true", "yes", "иә") else 0


def _document_from_row(row: sqlite3.Row | dict) -> dict:
    """Картаға түзетілген координатаны беріп, бастапқы дәлелді де сақтау."""
    document = dict(row)

    source_lat = document.get("original_lat")
    source_lon = document.get("original_lon")
    source_address = document.get("original_address_text")
    if source_lat is None:
        source_lat = document.get("lat")
    if source_lon is None:
        source_lon = document.get("lon")
    if not source_address:
        source_address = document.get("address_text")

    display_lat = document.get("display_lat")
    display_lon = document.get("display_lon")
    display_address = document.get("display_address_text")
    if display_lat is None:
        display_lat = source_lat
    if display_lon is None:
        display_lon = source_lon
    if not display_address:
        display_address = source_address

    document["original_lat"] = source_lat
    document["original_lon"] = source_lon
    document["original_address_text"] = source_address
    document["original_map_link"] = document.get("map_link")
    document["original_map_link_2gis"] = document.get("map_link_2gis")
    document["display_lat"] = display_lat
    document["display_lon"] = display_lon
    document["display_address_text"] = display_address

    # Бұрынғы frontend/docgen lat/lon/address_text өрістерін қолданады.
    document["lat"] = display_lat
    document["lon"] = display_lon
    document["address_text"] = display_address
    if display_lat is not None and display_lon is not None:
        document["map_link"] = f"https://www.google.com/maps?q={display_lat:.6f},{display_lon:.6f}"
        document["map_link_2gis"] = (
            f"https://2gis.kz/shymkent/geo/{display_lon:.6f},{display_lat:.6f}"
            f"?m={display_lon:.6f},{display_lat:.6f}/18"
        )

    approved = bool(document.get("approved_at"))
    document["is_approved"] = approved
    document["is_draft"] = not approved and document.get("status") != "rejected"
    document["document_state"] = (
        "approved" if approved
        else "rejected" if document.get("status") == "rejected"
        else "draft"
    )
    document["external_ticket"] = {
        "source": document.get("external_ticket_source"),
        "id": document.get("external_ticket_id"),
        "status": document.get("external_ticket_status"),
        "created_at": document.get("external_ticket_created_at"),
    }
    document["allowed_transitions"] = sorted(allowed_transitions(document.get("status", "")))
    return document


def insert_document(payload: dict, photo_file: Optional[str], video_file: Optional[str]) -> str:
    """Vision программасынан келген ДАЙЫН құжатты өзгертпей сақтау."""
    event_id = payload.get("event_id") or f"AIQYN-{now_iso()}"
    stamp = now_iso()

    values: dict[str, Any] = {"event_id": event_id, "status": "new"}
    for field in FIELDS:
        values[field] = payload.get(field)

    values["marked_on_road"] = _as_bool_int(payload.get("marked_on_road", False))
    values["gps_trusted"] = _as_bool_int(payload.get("gps_trusted", True))
    values["ai_verified"] = _as_bool_int(payload.get("ai_verified", False))

    for numeric in ("lat", "lon", "confidence", "gps_accuracy_m", "video_seconds",
                    "ai_confidence"):
        try:
            values[numeric] = float(values[numeric]) if values[numeric] is not None else None
        except (TypeError, ValueError):
            values[numeric] = None

    values["photo_file"] = photo_file
    values["video_file"] = video_file
    values["original_lat"] = values.get("lat")
    values["original_lon"] = values.get("lon")
    values["original_address_text"] = values.get("address_text")
    values["display_lat"] = values.get("lat")
    values["display_lon"] = values.get("lon")
    values["display_address_text"] = values.get("address_text")
    values["raw_json"] = json.dumps(payload, ensure_ascii=False)
    values["created_at"] = stamp
    values["updated_at"] = stamp

    columns = ", ".join(values.keys())
    placeholders = ", ".join(f":{key}" for key in values.keys())

    with closing(connect()) as connection, connection:
        cursor = connection.execute(
            f"INSERT INTO documents ({columns}) VALUES ({placeholders}) "
            "ON CONFLICT(event_id) DO NOTHING",
            values,
        )
        if cursor.rowcount:
            connection.execute(
                "INSERT INTO status_history (event_id, status, note, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_id, "new", "AIQYN Vision-нан қабылданды", "system", stamp),
            )

    return event_id


def allowed_transitions(current_status: str) -> set[str]:
    return set(STATUS_TRANSITIONS.get(current_status, set()))


def can_transition(current_status: str, new_status: str) -> bool:
    return new_status == current_status or new_status in allowed_transitions(current_status)


def set_status(event_id: str, status: str, note: str = "", actor: str = "operator") -> bool:
    if status not in STATUSES:
        raise ValueError(f"Белгісіз статус: {status}")

    stamp = now_iso()
    with closing(connect()) as connection, connection:
        existing = connection.execute(
            "SELECT status, approved_at, approved_by, sent_at FROM documents WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if existing is None:
            return False

        current_status = existing["status"]
        if not can_transition(current_status, status):
            allowed = ", ".join(sorted(allowed_transitions(current_status))) or "—"
            raise ValueError(
                f"Статус ауысуы рұқсат етілмеген: {current_status} -> {status}. "
                f"Рұқсат: {allowed}"
            )
        if status == "rejected" and not str(note or "").strip():
            raise ValueError("Қабылдамау себебі міндетті")

        approved_at = existing["approved_at"]
        approved_by = existing["approved_by"]
        if status == "confirmed" and current_status != "confirmed":
            approved_at = approved_at or stamp
            approved_by = approved_by or actor
        elif status in ("new", "rejected") and status != current_status:
            approved_at = None
            approved_by = None

        sent_at = (
            existing["sent_at"] or stamp
            if status == "sent"
            else existing["sent_at"]
        )
        cursor = connection.execute(
            "UPDATE documents SET status = ?, updated_at = ?, sent_at = ?, "
            "approved_at = ?, approved_by = ? WHERE event_id = ?",
            (status, stamp, sent_at, approved_at, approved_by, event_id),
        )
        if cursor.rowcount == 0:
            return False
        if status != current_status or str(note or "").strip():
            connection.execute(
                "INSERT INTO status_history (event_id, status, note, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_id, status, note, actor, stamp),
            )
    return True


def correct_document_location(
    event_id: str,
    lat: float,
    lon: float,
    address_text: Optional[str],
    note: str,
    actor: str = "operator",
) -> bool:
    """Картадағы орынды түзету; бастапқы Vision координатасы өзгермейді."""
    if not str(note or "").strip():
        raise ValueError("Координатаны түзету себебі міндетті")

    stamp = now_iso()
    with closing(connect()) as connection, connection:
        row = connection.execute(
            "SELECT status, original_lat, original_lon, original_address_text, "
            "lat, lon, address_text, display_lat, display_lon, display_address_text "
            "FROM documents WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return False
        if row["status"] not in ("new", "rejected"):
            raise ValueError("Жіберілген немесе орындалудағы құжаттың орны өзгертілмейді")

        previous_lat = row["display_lat"] if row["display_lat"] is not None else row["lat"]
        previous_lon = row["display_lon"] if row["display_lon"] is not None else row["lon"]
        previous_address = row["display_address_text"] or row["address_text"]
        new_address = address_text.strip() if isinstance(address_text, str) and address_text.strip() else previous_address

        original_lat = row["original_lat"] if row["original_lat"] is not None else row["lat"]
        original_lon = row["original_lon"] if row["original_lon"] is not None else row["lon"]
        original_address = row["original_address_text"] or row["address_text"]
        corrected = not (
            original_lat is not None and original_lon is not None
            and abs(float(lat) - float(original_lat)) < 1e-9
            and abs(float(lon) - float(original_lon)) < 1e-9
            and (new_address or "") == (original_address or "")
        )

        connection.execute(
            "UPDATE documents SET display_lat = ?, display_lon = ?, "
            "display_address_text = ?, location_corrected = ?, "
            "location_corrected_at = ?, location_corrected_by = ?, "
            "location_correction_note = ?, updated_at = ? WHERE event_id = ?",
            (
                float(lat), float(lon), new_address, 1 if corrected else 0,
                stamp, actor, note.strip(), stamp, event_id,
            ),
        )
        connection.execute(
            "INSERT INTO location_history (event_id, previous_lat, previous_lon, "
            "previous_address, new_lat, new_lon, new_address, note, actor, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, previous_lat, previous_lon, previous_address,
                float(lat), float(lon), new_address, note.strip(), actor, stamp,
            ),
        )
    return True


def record_delivery(event_id: str, delivery: dict, actor: str = "operator") -> bool:
    """Сыртқы жүйе берген ticket ID мен әр жіберу әрекетін сақтау."""
    stamp = now_iso()
    ok = bool(delivery.get("ok") or delivery.get("mock_109") or delivery.get("email"))
    ticket_source = delivery.get("ticket_source") or ("mock-109" if delivery.get("mock_109") else None)
    ticket_id = delivery.get("ticket_id")
    delivery_status = delivery.get("status") or ("accepted" if ok else "failed")
    error = delivery.get("error") or delivery.get("email_error") or ""

    with closing(connect()) as connection, connection:
        cursor = connection.execute(
            "UPDATE documents SET external_ticket_source = COALESCE(?, external_ticket_source), "
            "external_ticket_id = COALESCE(?, external_ticket_id), "
            "external_ticket_status = ?, "
            "external_ticket_created_at = CASE WHEN ? IS NOT NULL THEN ? "
            "ELSE external_ticket_created_at END, last_delivery_at = ?, "
            "last_delivery_ok = ?, delivery_error = ?, updated_at = ? WHERE event_id = ?",
            (
                ticket_source, ticket_id, delivery_status, ticket_id, stamp,
                stamp, 1 if ok else 0, error, stamp, event_id,
            ),
        )
        if cursor.rowcount == 0:
            return False
        connection.execute(
            "INSERT INTO delivery_attempts (event_id, ticket_source, ticket_id, "
            "delivery_status, mock_109, email, email_error, actor, response_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id, ticket_source, ticket_id, delivery_status,
                _as_bool_int(delivery.get("mock_109")), _as_bool_int(delivery.get("email")),
                error, actor, json.dumps(delivery, ensure_ascii=False), stamp,
            ),
        )
    return True


def set_after_photo(event_id: str, filename: str) -> bool:
    with closing(connect()) as connection, connection:
        cursor = connection.execute(
            "UPDATE documents SET after_photo_file = ?, updated_at = ? WHERE event_id = ?",
            (filename, now_iso(), event_id),
        )
        return cursor.rowcount > 0


# ============================================================
#  Оқу
# ============================================================

def list_documents(
    status: Optional[str] = None,
    class_key: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    query = "SELECT * FROM documents"
    conditions: list[str] = []
    params: list[Any] = []

    if status and status != "all":
        conditions.append("status = ?")
        params.append(status)
    if class_key and class_key != "all":
        conditions.append("class_key = ?")
        params.append(class_key)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with closing(connect()) as connection, connection:
        rows = connection.execute(query, params).fetchall()
    return [_document_from_row(row) for row in rows]


def get_document(event_id: str) -> Optional[dict]:
    with closing(connect()) as connection, connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        document = _document_from_row(row)
        history = connection.execute(
            "SELECT status, note, actor, created_at FROM status_history "
            "WHERE event_id = ? ORDER BY id",
            (event_id,),
        ).fetchall()
        location_history = connection.execute(
            "SELECT previous_lat, previous_lon, previous_address, new_lat, new_lon, "
            "new_address, note, actor, created_at FROM location_history "
            "WHERE event_id = ? ORDER BY id",
            (event_id,),
        ).fetchall()
        delivery_attempts = connection.execute(
            # response_json — әр арнаның квитанциясы. Интерфейс өтінімнің
            # ҚАЙСЫ арнамен кеткенін дәл содан көрсетеді.
            "SELECT ticket_source, ticket_id, delivery_status, mock_109, email, "
            "email_error, actor, created_at, response_json FROM delivery_attempts "
            "WHERE event_id = ? ORDER BY id",
            (event_id,),
        ).fetchall()
    document["history"] = [dict(item) for item in history]
    document["location_history"] = [dict(item) for item in location_history]
    document["delivery_attempts"] = [
        {**dict(item), "channels": _json_or_none(item["response_json"])}
        for item in delivery_attempts
    ]
    return document


def _json_or_none(raw: object) -> Optional[dict]:
    """response_json өрісінен арналар квитанциясын алу (бұзық болса — None)."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data.get("channels") if isinstance(data, dict) else None


# ============================================================
#  Жөндеу аймақтары
# ============================================================

ZONE_KINDS = ("repair", "closed", "ignore")
ZONE_SHAPES = ("circle", "polygon", "polyline")

ZONE_LABELS = {
    "repair": {"kk": "Жөндеу жүріп жатыр", "color": "#ffc400"},
    "closed": {"kk": "Жабық жол", "color": "#ff3b1f"},
    "ignore": {"kk": "Есепке алынбайды", "color": "#8c8c94"},
}


def _temporal_state(value: Any, *, is_end: bool) -> Optional[bool]:
    """ISO күн/уақыт үшін: шек өтіп кетті ме (end) немесе әлі басталмады ма."""
    if not value:
        return None
    text = str(value).strip()
    now = datetime.now(SHYMKENT_TZ)
    try:
        if "T" not in text and " " not in text:
            parsed_date = date.fromisoformat(text[:10])
            return parsed_date < now.date() if is_end else parsed_date > now.date()
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHYMKENT_TZ)
        return parsed < now if is_end else parsed > now
    except (TypeError, ValueError):
        return None


def zone_is_expired(zone: dict) -> bool:
    return bool(_temporal_state(zone.get("valid_until"), is_end=True))


def zone_is_not_started(zone: dict) -> bool:
    return bool(_temporal_state(zone.get("valid_from"), is_end=False))


def _zone_from_row(row: sqlite3.Row | dict) -> dict:
    zone = dict(row)
    geometry = None
    if zone.get("polygon"):
        try:
            geometry = json.loads(zone["polygon"]) if isinstance(zone["polygon"], str) else zone["polygon"]
        except (json.JSONDecodeError, TypeError):
            geometry = None
    zone["polygon"] = geometry
    if zone.get("shape") == "polyline":
        zone["points"] = geometry or []
        zone["corridor_m"] = float(zone.get("corridor_m") or zone.get("radius_m") or 20.0)
    else:
        zone["points"] = None

    zone["expired"] = zone_is_expired(zone)
    zone["not_started"] = zone_is_not_started(zone)
    zone["effective_active"] = bool(zone.get("active")) and not zone["expired"] and not zone["not_started"]
    zone["boundary_verified"] = bool(zone.get("boundary_verified"))
    zone["kind_label"] = ZONE_LABELS.get(zone.get("kind"), {}).get("kk", zone.get("kind"))
    zone["color"] = ZONE_LABELS.get(zone.get("kind"), {}).get("color", "#888")
    return zone


def insert_zone(data: dict, actor: str = "operator") -> int:
    kind = data.get("kind", "repair")
    if kind not in ZONE_KINDS:
        raise ValueError(f"Белгісіз аймақ түрі: {kind}")

    shape = data.get("shape", "circle")
    if shape not in ZONE_SHAPES:
        raise ValueError(f"Белгісіз геометрия түрі: {shape}")
    geometry = data.get("points") if shape == "polyline" else data.get("polygon")
    if geometry is None and shape == "polyline":
        geometry = data.get("polygon")
    stamp = now_iso()
    radius_m = float(data.get("radius_m") or 100)
    corridor_m = float(data.get("corridor_m") or radius_m or 20) if shape == "polyline" else data.get("corridor_m")

    with closing(connect()) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO zones (name, kind, shape, lat, lon, radius_m, corridor_m, polygon, "
            "active, valid_from, valid_until, note, source_url, external_ref, responsible_org, "
            "boundary_verified, created_by, created_at, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data.get("name") or "Аймақ",
                kind,
                shape,
                data.get("lat"),
                data.get("lon"),
                radius_m,
                corridor_m,
                json.dumps(geometry, ensure_ascii=False) if geometry else None,
                1 if data.get("active", True) else 0,
                data.get("valid_from") or None,
                data.get("valid_until") or None,
                data.get("note") or "",
                data.get("source_url") or "",
                data.get("external_ref") or "",
                data.get("responsible_org") or "",
                1 if data.get("boundary_verified", False) else 0,
                actor,
                stamp,
                actor,
                stamp,
            ),
        )
        return int(cursor.lastrowid)


def get_zone(zone_id: int) -> Optional[dict]:
    with closing(connect()) as connection, connection:
        row = connection.execute("SELECT * FROM zones WHERE id = ?", (zone_id,)).fetchone()
    return _zone_from_row(row) if row is not None else None


def update_zone(zone_id: int, data: dict, actor: str = "operator") -> bool:
    """Аймақты орнында жаңарту; тек рұқсат етілген өрістер жазылады."""
    existing = get_zone(zone_id)
    if existing is None:
        return False
    allowed = {
        "name", "kind", "shape", "lat", "lon", "radius_m", "corridor_m",
        "valid_from", "valid_until", "note", "source_url", "external_ref",
        "responsible_org", "boundary_verified", "active",
    }
    values = {key: data[key] for key in allowed if key in data}
    shape = values.get("shape")
    if shape is not None and shape not in ZONE_SHAPES:
        raise ValueError(f"Белгісіз геометрия түрі: {shape}")
    if "kind" in values and values["kind"] not in ZONE_KINDS:
        raise ValueError(f"Белгісіз аймақ түрі: {values['kind']}")

    if "points" in data or "polygon" in data:
        target_shape = shape or existing.get("shape")
        geometry = data.get("points") if target_shape == "polyline" else data.get("polygon")
        if geometry is None and target_shape == "polyline":
            geometry = data.get("polygon")
        values["polygon"] = json.dumps(geometry, ensure_ascii=False) if geometry else None

    if not values:
        return get_zone(zone_id) is not None

    stamp = now_iso()
    assignments = [f"{key} = ?" for key in values]
    boolean_fields = {"active", "boundary_verified"}
    params: list[Any] = [
        (1 if value else 0) if key in boolean_fields else value
        for key, value in values.items()
    ]
    assignments.extend(["updated_by = ?", "updated_at = ?"])
    params.extend([actor, stamp])

    if "active" in values:
        if values["active"]:
            assignments.extend(["deactivated_by = NULL", "deactivated_at = NULL"])
        else:
            assignments.extend(["deactivated_by = ?", "deactivated_at = ?"])
            params.extend([actor, stamp])

    params.append(zone_id)
    with closing(connect()) as connection, connection:
        cursor = connection.execute(
            f"UPDATE zones SET {', '.join(assignments)} WHERE id = ?", params
        )
        return cursor.rowcount > 0


def list_zones(active_only: bool = True, include_expired: bool = False) -> list[dict]:
    query = "SELECT * FROM zones"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY id DESC"

    with closing(connect()) as connection, connection:
        rows = connection.execute(query).fetchall()

    zones = [_zone_from_row(row) for row in rows]
    if not include_expired:
        zones = [zone for zone in zones if not zone["expired"]]
    if active_only:
        zones = [zone for zone in zones if zone["effective_active"]]
    return zones


def delete_zone(zone_id: int, actor: str = "operator") -> bool:
    """Backward-compatible атау: енді жазбаны жоймай, архивке жібереді."""
    return set_zone_active(zone_id, False, actor=actor)


def set_zone_active(zone_id: int, active: bool, actor: str = "operator") -> bool:
    return update_zone(zone_id, {"active": active}, actor=actor)


def stats() -> dict:
    with closing(connect()) as connection, connection:
        total = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        by_status = connection.execute(
            "SELECT status, COUNT(*) AS count FROM documents GROUP BY status"
        ).fetchall()
        by_type = connection.execute(
            "SELECT class_key, "
            "       MAX(defect_type_official) AS name, "
            "       COUNT(*) AS count, "
            "       SUM(CASE WHEN status IN ('sent','submitted','registered','assigned',"
            "            'in_progress','resolved','repaired','closed') THEN 1 ELSE 0 END) AS sent, "
            "       SUM(CASE WHEN status = 'repaired' THEN 1 ELSE 0 END) AS repaired, "
            "       SUM(CASE WHEN ai_verified = 1 THEN 1 ELSE 0 END) AS ai_verified, "
            "       AVG(confidence) AS avg_confidence "
            "FROM documents GROUP BY class_key ORDER BY count DESC"
        ).fetchall()

    counts = {row["status"]: row["count"] for row in by_status}
    active_zones = list_zones(active_only=True, include_expired=False)
    return {
        "total": total,
        "by_status": counts,
        "by_type": [dict(row) for row in by_type],
        "pending_review": counts.get("new", 0) + counts.get("repaired", 0),
        "active_zones": len(active_zones),
        "verified_zones": sum(1 for zone in active_zones if zone.get("boundary_verified")),
    }

def status_timestamps() -> dict[str, dict[str, str]]:
    """Әр оқиға әр кезеңге ҚАШАН жеткені: {event_id: {status: created_at}}.

    Аналитикаға керек: «табылғаннан жөнделгенге дейін қанша уақыт өтті»
    деген сұрақтың жауабы documents кестесінде жоқ — ол status_history
    ішінде жатыр. Әр статустың ЕҢ АЛҒАШҚЫ уақыты алынады: оқиға қайта
    ашылып, екінші рет жөнделсе де, бірінші айналым бұрмаланбайды.
    """
    out: dict[str, dict[str, str]] = {}
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT event_id, status, MIN(created_at) AS at FROM status_history "
            "GROUP BY event_id, status"
        ).fetchall()
    for row in rows:
        out.setdefault(row["event_id"], {})[row["status"]] = row["at"]
    return out
