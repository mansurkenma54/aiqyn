"""Шымкенттегі НАҚТЫ жол жұмыстарын картаға СЫЗЫҚ ретінде қосу.

Бұрын аймақтар дөңгелек болатын — бұл дұрыс емес еді: жол жұмысы дөңгелек
емес, көше бойымен СЫЗЫҚ болып созылады. Дөңгелек көрші кварталдарды да
жауып, ондағы нақты ақауларды жасырып қоятын.

Қазір: 2ГИС-тен көшенің НАҚТЫ геометриясы (MULTILINESTRING) алынып,
аймақ жолдың өз сызығы бойымен, екі жағына `corridor_m` метр енімен
салынады.

Дереккөзі — ашық жарияланған қала жоспарлары (inform.kz, 2025-2027
жылдарға арналған жол құрылысы мен жөндеу тізімі).

ЕСКЕРТУ: сызық — көшенің ТОЛЫҚ бойы, ал жұмыс оның бір учаскесінде ғана
жүруі мүмкін. Сондықтан әр аймақ `boundary_verified=false` деп белгіленеді:
әкімдіктен нақты учаске шекарасы алынғанша, бұл шамамен алынған шекара.
Оператор картадан кез келген аймақты түзете, қысқарта немесе өшіре алады.

Қолдану:
    python scripts/seed_zones.py                # қосу
    python scripts/seed_zones.py --dry-run      # тек көрсету
    python scripts/seed_zones.py --replace      # ескілерін өшіріп, қайта қосу
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision.env import load_env  # noqa: E402

PORTAL = os.getenv("AIQYN_PORTAL_URL", "http://127.0.0.1:8000")
AUTH = (
    os.getenv("AIQYN_ADMIN_USER", "admin"),
    os.getenv("AIQYN_ADMIN_PASSWORD", "aiqyn2026"),
)

MAX_POINTS = 120          # бір аймақтағы ең көп нүкте (JSON тым үлкен болмасын)
DEFAULT_CORRIDOR_M = 25.0  # жол осінен екі жаққа қарай (жүру бөлігі + жиек)

# Ашық дереккөздерде жарияланған жол жұмыстары.
# (2ГИС-тен іздеу атауы, көрсетілетін атауы, түрі, дәліз ені, дейін, сипаттама)
PROJECTS = [
    ("Момынова", "Мөмінов көшесі — жол өткізгіш құрылысы",
     "repair", 30, "2026-12-31", "Жол өткізгіш құрылысы, 1,6 км (2026 ж.)"),

    ("Тленшина", "Тіленшин көшесі — теміржол өткізгіші",
     "repair", 30, "2026-12-31", "Теміржол өткізгіші, 1,0 км (2025-2026 ж.)"),

    ("Казиева", "Қазиев көшесі — Аргынбеков көшесіне дейін ұзарту",
     "repair", 25, "2026-12-31", "Көшені ұзарту (2025-2026 ж.)"),

    ("Исраилова", "Е. Исраилов көшесі — реконструкция",
     "repair", 30, "2027-12-31", "Реконструкция, 4,7 км (2025-2027 ж.)"),

    ("Кунаева", "Қонаев даңғылы — 6-кезең",
     "repair", 35, "2027-12-31", "Даңғылдың жалғасы, 2,0 км (2025-2027 ж.)"),

    ("Юсупова", "Юсупов көшесі — күрделі жөндеу",
     "repair", 25, "2026-12-31", "Күрделі жөндеу (Тамшыбұлақ, Жаңа Қоныспен бірге 9,8 км)"),

    ("Тастыбулак", "Тамшыбұлақ көшесі — күрделі жөндеу",
     "repair", 25, "2026-12-31", "Күрделі жөндеу"),

    ("Сареми", "Сәремі көшесі — күрделі жөндеу",
     "repair", 25, "2026-12-31", "Күрделі жөндеу (Жаңақұрылыспен бірге 7,3 км)"),

    ("Жанакурылыс", "Жаңақұрылыс көшесі — күрделі жөндеу",
     "repair", 25, "2026-12-31", "Күрделі жөндеу"),

    ("Алтынсарина", "Алтынсарин көшесі — жалғасы",
     "repair", 25, "2026-06-30", "Көшенің жалғасы, 2,0 км"),
]

SOURCE_NOTE = (
    "Дереккөз: қаланың ашық жарияланған жол жұмыстары жоспары. "
    "Сызық — 2ГИС бойынша көшенің геометриясы; жұмыс учаскесінің нақты "
    "шекарасы әкімдіктен алынып, нақтылануы тиіс."
)


# ============================================================
#  WKT геометриясы
# ============================================================

def parse_wkt_lines(wkt: str) -> list[list[list[float]]]:
    """LINESTRING / MULTILINESTRING -> [[[lat, lon], ...], ...]

    МАҢЫЗДЫ: WKT ішінде координаталар «lon lat» ретімен жазылады,
    ал Leaflet пен біздің жүйе «lat, lon» күтеді. Оларды шатастыру —
    ең жиі кездесетін қате (нүкте Сомали жағалауына түсіп кетеді).
    """
    if not wkt:
        return []

    lines: list[list[list[float]]] = []
    for chunk in re.findall(r"\(([-0-9.,\s]+)\)", wkt):
        points: list[list[float]] = []
        for pair in chunk.split(","):
            parts = pair.split()
            if len(parts) < 2:
                continue
            try:
                lon, lat = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                points.append([round(lat, 6), round(lon, 6)])
        if len(points) >= 2:
            lines.append(points)
    return lines


def line_length_m(points: list[list[float]]) -> float:
    total = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        mean_lat = math.radians((lat1 + lat2) / 2)
        dx = math.radians(lon2 - lon1) * math.cos(mean_lat) * 6_371_000
        dy = math.radians(lat2 - lat1) * 6_371_000
        total += math.hypot(dx, dy)
    return total


def _perpendicular_distance(point, start, end) -> float:
    (py, px), (ay, ax), (by, bx) = point, start, end
    if (ay, ax) == (by, bx):
        return math.hypot(py - ay, px - ax)
    dy, dx = by - ay, bx - ax
    t = ((py - ay) * dy + (px - ax) * dx) / (dy * dy + dx * dx)
    t = max(0.0, min(1.0, t))
    return math.hypot(py - (ay + t * dy), px - (ax + t * dx))


def simplify(points: list[list[float]], tolerance: float = 0.00004) -> list[list[float]]:
    """Дуглас-Пекер: сызықтың пішінін сақтап, нүкте санын азайту."""
    if len(points) <= 2:
        return points

    max_distance = 0.0
    index = 0
    for i in range(1, len(points) - 1):
        distance = _perpendicular_distance(points[i], points[0], points[-1])
        if distance > max_distance:
            max_distance, index = distance, i

    if max_distance <= tolerance:
        return [points[0], points[-1]]

    left = simplify(points[:index + 1], tolerance)
    right = simplify(points[index:], tolerance)
    return left[:-1] + right


def fetch_street_line(query: str, key: str) -> tuple[list[list[float]], float] | None:
    """Көшенің ең ұзын геометриялық сызығын қайтарады."""
    try:
        response = requests.get(
            "https://catalog.api.2gis.com/3.0/items",
            params={
                "q": f"Шымкент {query}",
                "type": "street",
                "fields": "items.geometry.selection",
                "key": key,
            },
            timeout=25,
        )
        response.raise_for_status()
        items = (response.json().get("result") or {}).get("items") or []
    except requests.RequestException as exc:
        print(f"   2ГИС қатесі: {exc}")
        return None

    best: list[list[float]] = []
    best_length = 0.0

    for item in items:
        wkt = ((item.get("geometry") or {}).get("selection")) or ""
        for line in parse_wkt_lines(wkt):
            length = line_length_m(line)
            if length > best_length:
                best, best_length = line, length

    if not best:
        return None

    simplified = simplify(best)
    while len(simplified) > MAX_POINTS:
        step = math.ceil(len(simplified) / MAX_POINTS)
        simplified = simplified[::step] + [simplified[-1]]

    return simplified, best_length


# ============================================================
#  Портал
# ============================================================

def existing_zones() -> dict:
    try:
        response = requests.get(f"{PORTAL}/api/zones", timeout=10)
        response.raise_for_status()
        return {zone["name"]: zone["id"] for zone in response.json().get("zones", [])}
    except requests.RequestException:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="тек көрсету, қоспау")
    parser.add_argument("--replace", action="store_true", help="ескілерін өшіріп қайта қосу")
    args = parser.parse_args()

    load_env()
    key = os.getenv("AIQYN_2GIS_KEY", "").strip()
    if not key:
        print("2ГИС кілті жоқ (.env ішінде AIQYN_2GIS_KEY).")
        return 1

    print()
    print("=" * 74)
    print("  Шымкенттегі жол жұмыстары — көше сызығы бойымен картаға қосу")
    print("=" * 74)
    print()

    already = existing_zones()
    added = skipped = failed = 0

    for query, title, kind, corridor, until, note in PROJECTS:
        if title in already:
            if args.replace and not args.dry_run:
                requests.delete(f"{PORTAL}/api/zones/{already[title]}", auth=AUTH, timeout=10)
            else:
                print(f"[ бар      ] {title}")
                skipped += 1
                continue

        result = fetch_street_line(query, key)
        if result is None:
            print(f"[ ТАБЫЛМАДЫ ] {title}")
            failed += 1
            continue

        points, length = result
        print(f"[ {len(points):3d} нүкте · {length / 1000:.1f} км ] {title}")

        if args.dry_run:
            continue

        payload = {
            "name": title,
            "kind": kind,
            "shape": "polyline",
            "points": points,
            "corridor_m": float(corridor or DEFAULT_CORRIDOR_M),
            "valid_until": until,
            "boundary_verified": False,
            "note": f"{note}  ·  {SOURCE_NOTE}",
        }
        try:
            response = requests.post(
                f"{PORTAL}/api/zones", json=payload, auth=AUTH, timeout=20
            )
            if response.status_code == 200:
                added += 1
            else:
                print(f"             қосылмады: {response.status_code} {response.text[:160]}")
                failed += 1
        except requests.RequestException as exc:
            print(f"             сайтқа қосылмады: {exc}")
            failed += 1

    print()
    print("-" * 74)
    if args.dry_run:
        print("  --dry-run: ештеңе қосылмады")
    else:
        print(f"  Қосылды: {added}   ·   Бұрыннан бар: {skipped}   ·   Сәтсіз: {failed}")
    print(f"  Картаны ашыңыз: {PORTAL}")
    print("-" * 74)
    print()
    print("  Картада: САРЫ сызық — жөндеу жүріп жатыр, ҚЫЗЫЛ — жол жабық.")
    print("  Сол сызықтың бойындағы ақаулар бойынша өтінім ЖІБЕРІЛМЕЙДІ.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
