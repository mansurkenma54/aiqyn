"""Демо аймағының карта тақтайшаларын жергілікті кэшке жүктеу.

Не үшін керек: хакатон питчингі офлайн өтуі мүмкін. Байланыс жоқ болса,
портал интерфейсі бәрібір ашылады және маркерлер координата бойынша дұрыс
орында тұрады — бірақ фонда қаланың нақты картасы болғаны әлдеқайда
түсінікті.

Не істейді: тек Шымкент шекарасындағы шағын аймақты, тек демоға қажет
масштабтарда бір рет жүктейді (әдепкі 11–14 масштаб = 260 тақтайша,
~3,6 МБ). Бұл — жаппай көшіру емес, бір демоға арналған кэш.

    python scripts/cache_tiles.py --dry-run     # алдымен санын көру
    python scripts/cache_tiles.py               # жүктеу

Нәтиже: portal/static/tiles/{z}/{x}/{y}.png + manifest.json
Атрибуция картада сол күйінде қалады: © Esri, HERE, Garmin, © OpenStreetMap.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TILES = ROOT / "portal" / "static" / "tiles"

# Шымкент пен Сайрам — далалық тест аймағы
SOUTH, WEST = 42.2350, 69.4700
NORTH, EAST = 42.4300, 69.8000

# CARTO енді кілт сұрайды — Esri Canvas кілтсіз. Esri ретінде {y}/{x}.
URL = ("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
       "World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}")
UA = {"User-Agent": "AIQYN-offline-demo/1.0 (Smart City hackathon, local cache)"}


def deg2tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def plan(min_zoom: int, max_zoom: int) -> list[tuple[int, int, int]]:
    jobs: list[tuple[int, int, int]] = []
    for zoom in range(min_zoom, max_zoom + 1):
        x0, y0 = deg2tile(NORTH, WEST, zoom)
        x1, y1 = deg2tile(SOUTH, EAST, zoom)
        for x in range(min(x0, x1), max(x0, x1) + 1):
            for y in range(min(y0, y1), max(y0, y1) + 1):
                jobs.append((zoom, x, y))
    return jobs


def main() -> int:
    parser = argparse.ArgumentParser(description="Демо аймағының карта кэші")
    parser.add_argument("--min-zoom", type=int, default=11)
    parser.add_argument("--max-zoom", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="бар файлды да қайта жүктеу")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    jobs = plan(args.min_zoom, args.max_zoom)
    print(f"Аймақ  : {SOUTH}..{NORTH} N · {WEST}..{EAST} E (Шымкент, Сайрам)")
    print(f"Масштаб: {args.min_zoom}–{args.max_zoom}")
    print(f"Тақтайша: {len(jobs)} дана (~{len(jobs) * 14 / 1024:.1f} МБ)")

    if args.dry_run:
        print("\n--dry-run: ештеңе жүктелген жоқ.")
        return 0

    if len(jobs) > 1500:
        print("\n[ТОҚТАТЫЛДЫ] 1500-ден көп тақтайша — аймақты немесе масштабты кішірейтіңіз.")
        return 1

    done = skipped = failed = 0
    for zoom, x, y in jobs:
        target = TILES / str(zoom) / str(x) / f"{y}.png"
        if target.exists() and not args.force:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        url = URL.format(z=zoom, x=x, y=y)
        try:
            request = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(request, timeout=30) as response:
                target.write_bytes(response.read())
            done += 1
        except (urllib.error.URLError, OSError) as exc:
            failed += 1
            print(f"  [ҚАТЕ] {zoom}/{x}/{y}: {exc}")
        time.sleep(0.05)          # серверді жүктемеу

    manifest = {
        "source": "Esri World Dark Gray Base (© Esri, HERE, Garmin, © OpenStreetMap)",
        "purpose": "офлайн демо үшін жергілікті кэш",
        "min_zoom": args.min_zoom,
        "max_zoom": args.max_zoom,
        "bounds": [[SOUTH, WEST], [NORTH, EAST]],
        "tiles": len(jobs),
    }
    TILES.mkdir(parents=True, exist_ok=True)
    (TILES / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nЖүктелді: {done} · бұрыннан бар: {skipped} · қате: {failed}")
    print(f"Қалта: {TILES.relative_to(ROOT)}")
    print("Байланыс үзілсе портал автоматты түрде осы кэшке ауысады.")
    return 1 if failed and done == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
