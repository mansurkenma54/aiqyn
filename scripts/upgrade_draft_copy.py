"""Жіберілмеген legacy өтінім жобаларын қазіргі ресми мәтінге көшіру.

Жіберілген/расталған жазбалар өзгермейді. Скрипт қайталанып іске қосылса,
әлдеқашан жаңартылған жобаларды өткізіп жібереді.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portal import db
from vision import categories
from vision.document import _description_kk, _description_ru
from vision.geo import format_coord


SAFE_PREFIX = "AIQYN автоматтандырылған мониторинг жүйесі"
ROAD_ORG_KK = categories.ORGANISATIONS["roads"]["kk"]
ROAD_ORG_RU = categories.ORGANISATIONS["roads"]["ru"]


def _analysis_kk(row: dict) -> str:
    if not row.get("ai_verified"):
        return ""
    lines = ["", "ҚОСЫМША AI-МОДЕЛЬ БАҒАСЫ (алдын ала):"]
    if row.get("ai_note"):
        lines.append(str(row["ai_note"]))
    if row.get("ai_size"):
        lines.append(f"Шамамен өлшемі: {row['ai_size']}")
    if row.get("ai_location"):
        lines.append(f"Жолдағы орны: {row['ai_location']}")
    if row.get("ai_danger"):
        lines.append(f"Ықтимал қозғалыс қаупі: {row['ai_danger']}")
    if row.get("ai_action"):
        deadline = (
            f" (бағдарлық мерзім: {int(row['ai_urgency_days'])} күн; нормативтік SLA емес)"
            if row.get("ai_urgency_days") else ""
        )
        lines.append(f"AI ұсынған ықтимал шара: {row['ai_action']}{deadline}")
    lines.append(
        f"AI бағасының сенімділігі: {float(row.get('ai_confidence') or 0) * 100:.0f}%"
        f" (модель: {row.get('ai_model') or '—'})"
    )
    lines.append(
        "Ескерту: AI бағасы инженерлік тексеруді, нормативтік қорытындыны "
        "немесе жауапты органның шешімін алмастырмайды."
    )
    return "\n".join(lines)


def _analysis_ru(row: dict) -> str:
    if not row.get("ai_verified"):
        return ""
    lines = ["", "ДОПОЛНИТЕЛЬНАЯ ОЦЕНКА AI-МОДЕЛИ (предварительно):"]
    if row.get("ai_urgency_days"):
        lines.append(
            f"Ориентировочный срок по оценке AI: {int(row['ai_urgency_days'])} дн. "
            "(не является нормативным SLA)."
        )
    lines.append(
        f"Уверенность AI-оценки: {float(row.get('ai_confidence') or 0) * 100:.0f}%"
    )
    lines.append(
        "Примечание: AI-оценка не заменяет инженерное обследование, "
        "нормативное заключение или решение ответственного органа."
    )
    return "\n".join(lines)


def upgrade() -> int:
    db.init_db()
    updated = 0
    with closing(db.connect()) as connection, connection:
        road_keys = [key for key, category in categories.CATEGORIES.items() if category.org == "roads"]
        placeholders = ",".join("?" for _ in road_keys)
        connection.execute(
            f"UPDATE documents SET responsible_org = ?, responsible_org_ru = ? "
            f"WHERE status = 'new' AND class_key IN ({placeholders})",
            (ROAD_ORG_KK, ROAD_ORG_RU, *road_keys),
        )
        rows = connection.execute(
            "SELECT * FROM documents WHERE status = 'new' "
            "AND COALESCE(description_text, '') NOT LIKE ?",
            (f"{SAFE_PREFIX}%",),
        ).fetchall()

        for record in rows:
            row = dict(record)
            category = categories.get(str(row.get("class_key") or ""))
            lat = row.get("display_lat") if row.get("display_lat") is not None else row.get("lat")
            lon = row.get("display_lon") if row.get("display_lon") is not None else row.get("lon")
            coords = format_coord(float(lat), float(lon)) if lat is not None and lon is not None else "нақтыланбаған"
            severity = category.base_severity
            common = dict(
                category=category,
                address=row.get("display_address_text") or row.get("address_text") or "Мекенжай нақтыланбаған",
                coords=coords,
                human_time=row.get("timestamp_human") or row.get("timestamp") or "уақыты нақтыланбаған",
                severity=severity,
                confidence=float(row.get("confidence") or 0),
                marked_on_road=bool(row.get("marked_on_road")),
                has_video=bool(row.get("video_file")),
                video_sec=float(row.get("video_seconds") or 0),
                gps_trusted=bool(row.get("gps_trusted")),
                gps_accuracy_m=float(row.get("gps_accuracy_m") or 0),
            )
            kk = _description_kk(**common) + _analysis_kk(row)
            ru = _description_ru(**common) + _analysis_ru(row)

            connection.execute(
                "UPDATE documents SET description_text = ?, description_text_ru = ?, "
                "severity = ?, severity_kk = ?, responsible_org = ?, updated_at = ? "
                "WHERE event_id = ? AND status = 'new'",
                (
                    kk,
                    ru,
                    severity,
                    categories.SEVERITY_KK[severity],
                    ROAD_ORG_KK if category.org == "roads" else row.get("responsible_org"),
                    db.now_iso(),
                    row["event_id"],
                ),
            )
            connection.execute(
                "INSERT INTO status_history (event_id, status, note, actor, created_at) "
                "VALUES (?, 'new', ?, 'migration', ?)",
                (
                    row["event_id"],
                    "Жіберілмеген өтінім жобасы ресми мәтіннің қауіпсіз нұсқасына жаңартылды",
                    db.now_iso(),
                ),
            )
            updated += 1
    return updated


if __name__ == "__main__":
    print(f"UPDATED_DRAFTS={upgrade()}")
