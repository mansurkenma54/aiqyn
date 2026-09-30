"""Барлық оқиғаны жол сегментіне байлап, нәтижесін кэшке жазу.

ТЗ талабы — ақаулардың кеңістіктік байланысы (SDR / ЦС ГГ НИПД).
Байланыс OpenStreetMap геометриясы арқылы жасалады: нүкте ең жақын
жол сызығына проекцияланып, тұрақты `OSM:way/<id>` идентификаторы
беріледі.

Бір рет жүргізілгеннен кейін нәтиже дискіде жатады, сондықтан экспорт
пен карта интернетсіз де толық жұмыс істейді.

    python scripts/bind_spatial.py
    python scripts/bind_spatial.py --limit 50 --delay 1.0
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portal import db, spatial  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--delay", type=float, default=0.8,
                        help="сұраулар арасындағы кідіріс (Overpass шектеуі)")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    db.init_db()
    documents = db.list_documents(status="all", limit=args.limit)
    print(f"Оқиға: {len(documents)}")

    bound = cached = failed = 0
    for i, doc in enumerate(documents, 1):
        lat = doc.get("display_lat") or doc.get("lat")
        lon = doc.get("display_lon") or doc.get("lon")
        if lat is None or lon is None:
            failed += 1
            continue

        before = spatial.bind(lat, lon, allow_network=False)
        if before.get("bound") or before.get("reason") in ("жақын жол табылмады",):
            cached += 1
            continue

        result = spatial.bind(lat, lon, allow_network=True)
        if result.get("bound"):
            bound += 1
            if bound <= 5 or bound % 10 == 0:
                print(f"  {i:>3}. {result['spatial_id']:<22} "
                      f"{(result.get('road_name') or 'атауы жоқ'):<28} "
                      f"{result['distance_m']} м")
        else:
            failed += 1
        time.sleep(args.delay)

    print(f"\nЖаңа байланыс: {bound} · кэште болған: {cached} · байланбады: {failed}")
    print(f"Кэш: {spatial.CACHE_PATH}")
    print("Экспорт: /api/export/gis.geojson · /api/export/sdr.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
