"""Карта интерфейсін көрсетуге арналған екі анық белгіленген демо сызық.

Геометрия OpenStreetMap-тағы Сарыағаш көшесінің жол осінен алынған.
Бұл жазбалар нақты коммуналдық жұмысты растамайды және AI оқиғаларын
тоқтатпайды (`boundary_verified=False`).
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portal import db


DEMO_ZONES = (
    {
        "name": "ДЕМО · Сарыағаш көшесі — жол жұмысы",
        "kind": "repair",
        "shape": "polyline",
        "points": [
            [42.3413082, 69.5923664],
            [42.3420993, 69.5891307],
            [42.3423215, 69.5882554],
        ],
        "corridor_m": 12,
        "valid_from": "2026-08-14",
        "valid_until": "2026-08-31",
        "source_url": "https://www.openstreetmap.org/way/114214945",
        "external_ref": "DEMO-ROADWORK-01",
        "responsible_org": "Көрсетілім дерегі — ресми тапсырма емес",
        "note": "Интерфейс көрсету үлгісі. Нақты жұмыс шекарасы ретінде қолданбаңыз.",
        "boundary_verified": False,
    },
    {
        "name": "ДЕМО · Сарыағаш көшесі — жабық учаске",
        "kind": "closed",
        "shape": "polyline",
        "points": [
            [42.3423215, 69.5882554],
            [42.3425358, 69.5873748],
            [42.3429536, 69.5856498],
        ],
        "corridor_m": 12,
        "valid_from": "2026-08-14",
        "valid_until": "2026-08-31",
        "source_url": "https://www.openstreetmap.org/way/114214945",
        "external_ref": "DEMO-CLOSURE-01",
        "responsible_org": "Көрсетілім дерегі — ресми тапсырма емес",
        "note": "Интерфейс көрсету үлгісі. Нақты жабылу дерегі ретінде қолданбаңыз.",
        "boundary_verified": False,
    },
)


def seed() -> list[int]:
    db.init_db()
    existing = {
        zone.get("external_ref")
        for zone in db.list_zones(active_only=False, include_expired=True)
    }
    created: list[int] = []
    for payload in DEMO_ZONES:
        if payload["external_ref"] in existing:
            continue
        created.append(db.insert_zone(payload, actor="demo-seed"))
    return created


if __name__ == "__main__":
    print(f"CREATED_ZONE_IDS={seed()}")
