"""Видеофайлға GPS трек жасау — демо/тест үшін.

НЕ ҮШІН КЕРЕК
-------------
Видеофайлда GPS жоқ. Онсыз жүйе `fallback_lat/lon` нүктесін қолданады
да, сапардағы БАРЛЫҚ ақау картада БІР нүктеде үйіліп қалады: мекенжай
да бір, дедупликация да оларды бір ақау деп санауы мүмкін.

Бұл скрипт видеоның ұзақтығына сай трек құрады: көлік нақты көшенің
бойымен жүріп келе жатқандай, әр секундқа өз координатасы болады.
Көше геометриясы 2ГИС-тен алынады, сондықтан трек ойдан шығарылған
түзу сызық емес — жолдың нақты осімен жүреді.

АДАЛДЫҚ ЕСКЕРТПЕСІ
------------------
Бұл — ЖАЗЫЛҒАН GPS емес, ҚАЙТА ҚҰРЫЛҒАН трек. Видео қай көшеде
түсірілгенін адам көрсетеді, скрипт тек сол көшенің бойына нүктелерді
таратады. Далалық тестте телефонның өз GPS-і қолданылады да, бұл
скрипт мүлдем керек емес.

ҚОЛДАНУ
-------
    # мекенжай бойынша (2ГИС іздейді)
    python scripts/make_gps_track.py --video ../докмументы/видео/test.MOV \
        --near "Bek Market, Мустафы Шокая 117, Шымкент" \
        --street "куйши Мамен" --out demo_track.jsonl

    # тікелей координатамен
    python scripts/make_gps_track.py --video жол.mp4 \
        --lat 42.378176 --lon 69.667295 --bearing 190 --speed-kmh 22

    # содан кейін
    python -m vision.main run --source file --video жол.mp4 \
        --gps-track demo_track.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.config import Config          # noqa: E402
from vision.geo import haversine_m        # noqa: E402
from vision.roadsnap import RoadSnapper    # noqa: E402

EARTH_R = 6_371_000.0


# ------------------------------------------------------------------
#  Геометрия
# ------------------------------------------------------------------

def offset(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Нүктеден белгілі бағытта, белгілі қашықтықта жатқан координата."""
    brg = math.radians(bearing_deg)
    d = distance_m / EARTH_R
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def resample_polyline(points: list[tuple[float, float]], count: int) -> list[tuple[float, float]]:
    """Сынық сызықты бірдей қашықтықтағы `count` нүктеге бөлу."""
    if len(points) < 2:
        return [points[0]] * count if points else []

    spans = [haversine_m(*points[i], *points[i + 1]) for i in range(len(points) - 1)]
    total = sum(spans)
    if total <= 0:
        return [points[0]] * count

    out: list[tuple[float, float]] = []
    for index in range(count):
        target = total * index / max(1, count - 1)
        walked = 0.0
        for seg_index, span in enumerate(spans):
            if walked + span >= target or seg_index == len(spans) - 1:
                k = 0.0 if span <= 0 else (target - walked) / span
                (la0, lo0), (la1, lo1) = points[seg_index], points[seg_index + 1]
                out.append((la0 + (la1 - la0) * k, lo0 + (lo1 - lo0) * k))
                break
            walked += span
    return out


def video_duration_sec(path: Path) -> float:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"Видео ашылмады: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    if fps <= 1 or frames <= 0:
        raise SystemExit(f"Видеоның ұзақтығы анықталмады: {path}")
    return float(frames) / float(fps)


# ------------------------------------------------------------------
#  2ГИС
# ------------------------------------------------------------------

def find_place(cfg: Config, query: str) -> tuple[float, float, str]:
    import requests
    response = requests.get(
        "https://catalog.api.2gis.com/3.0/items",
        params={"q": query, "fields": "items.point,items.full_address_name",
                "key": cfg.gis2_key, "page_size": 1},
        timeout=20,
    )
    response.raise_for_status()
    items = (response.json().get("result") or {}).get("items") or []
    if not items:
        raise SystemExit(f"2ГИС «{query}» бойынша ештеңе таппады.")
    point = items[0].get("point") or {}
    name = items[0].get("full_address_name") or items[0].get("name") or query
    return float(point["lat"]), float(point["lon"]), name


def pick_street(cfg: Config, lat: float, lon: float,
                street_hint: str) -> tuple[str, list[tuple[float, float]]]:
    """Нүктенің маңындағы көшелердің ішінен керегін таңдау."""
    snapper = RoadSnapper(cfg)
    roads = snapper._load_roads(lat, lon)          # noqa: SLF001 — әдейі
    if not roads:
        raise SystemExit("2ГИС бұл маңайдан көше геометриясын бермеді. "
                         "--bearing арқылы қолмен бағыт беріңіз.")

    hint = (street_hint or "").strip().lower()
    named = [(n, line) for n, line in roads if hint and hint in (n or "").lower()]
    if hint and not named:
        print("Осы маңайдағы көшелер:")
        for name, line in roads:
            print(f"   • {name}  ({len(line)} нүкте)")
        raise SystemExit(f"«{street_hint}» табылмады — жоғарыдағы атаудың бірін алыңыз.")

    pool = named or roads
    # Ең жақын өтетін көшені аламыз
    best = min(pool, key=lambda item: min(haversine_m(lat, lon, *p) for p in item[1]))
    return best[0], best[1]


def densify(line: list[tuple[float, float]], step_m: float = 2.0) -> list[tuple[float, float]]:
    """Сынық сызықты жиі нүктелерге бөлу.

    2ГИС көше геометриясын ірі береді: көрші екі нүктенің арасы 400 метр
    болуы мүмкін. Ондай сызықтан 38 метрлік кесінді қиып алу үшін оны
    алдымен жиілетіп алу керек.
    """
    if len(line) < 2:
        return list(line)
    out: list[tuple[float, float]] = [line[0]]
    for (la0, lo0), (la1, lo1) in zip(line, line[1:]):
        span = haversine_m(la0, lo0, la1, lo1)
        steps = max(1, int(span / step_m))
        for k in range(1, steps + 1):
            f = k / steps
            out.append((la0 + (la1 - la0) * f, lo0 + (lo1 - lo0) * f))
    return out


def trim_to_approach(line: list[tuple[float, float]], target: tuple[float, float],
                     length_m: float, stop_before_m: float) -> list[tuple[float, float]]:
    """Көше сызығынан нысанға ЖАҚЫНДАЙТЫН дәл `length_m` метрлік бөлікті кесу.

    Көлік нысанға (дүкенге) қарай жүріп келеді: трек нысанға жақындай
    түсуі керек әрі оған жетпей `stop_before_m` метр бұрын бітуі тиіс.
    """
    dense = densify(line)
    end_index = min(range(len(dense)), key=lambda i: haversine_m(*dense[i], *target))

    need = length_m + stop_before_m

    def walk(indices) -> list[tuple[float, float]]:
        """end_index-тен көрсетілген бағытта `need` метр жүріп өту."""
        path = [dense[end_index]]
        walked = 0.0
        previous = end_index
        for i in indices:
            walked += haversine_m(*dense[previous], *dense[i])
            path.append(dense[i])
            previous = i
            if walked >= need:
                break
        return path if walked >= need * 0.9 else []

    back = walk(range(end_index - 1, -1, -1))
    fwd = walk(range(end_index + 1, len(dense)))

    # Қай жақтан келу керек: ұзындығы жеткен жағы (екеуі де жетсе — ұзынырағы)
    chosen = max((back, fwd), key=len)
    if not chosen:
        raise SystemExit(
            "Көшенің осы маңайдағы бөлігі маршрутқа қысқа. "
            "--speed-kmh азайтыңыз немесе --bearing арқылы қолмен бағыт беріңіз."
        )

    chosen.reverse()                       # жүру бағыты: нысанға ҚАРАЙ

    # Соңынан `stop_before_m` метрді кесіп тастаймыз — нысанның дәл
    # үстіне тірелмей, одан біраз бұрын тоқтауымыз керек
    while len(chosen) > 2 and haversine_m(*chosen[-1], *target) < stop_before_m:
        chosen.pop()
    return chosen


# ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Видеофайлға GPS трек жасау")
    parser.add_argument("--video", required=True, help="видеофайл (ұзақтығын өлшеу үшін)")
    parser.add_argument("--near", help="нысан (2ГИС іздейді): «Bek Market, Шымкент»")
    parser.add_argument("--lat", type=float, help="нысанның координатасы (--near орнына)")
    parser.add_argument("--lon", type=float)
    parser.add_argument("--street", default="", help="қай көшенің бойымен жүру керек")
    parser.add_argument("--bearing", type=float,
                        help="көше табылмаса — қолмен бағыт (0=солтүстік, 90=шығыс)")
    parser.add_argument("--speed-kmh", type=float, default=22.0, help="көліктің жылдамдығы")
    parser.add_argument("--stop-before", type=float, default=12.0,
                        help="нысанға жетпей осынша метр бұрын тоқтау")
    parser.add_argument("--hz", type=float, default=2.0, help="секундына неше нүкте")
    parser.add_argument("--out", default="demo_track.jsonl")
    args = parser.parse_args()

    cfg = Config.load()
    video = Path(args.video)
    if not video.is_absolute():
        video = (Path.cwd() / video).resolve()
    duration = video_duration_sec(video)
    length_m = args.speed_kmh / 3.6 * duration

    if args.near:
        lat, lon, label = find_place(cfg, args.near)
        print(f"Нысан: {label}  ({lat:.6f}, {lon:.6f})")
    elif args.lat is not None and args.lon is not None:
        lat, lon, label = args.lat, args.lon, "қолмен берілген нүкте"
    else:
        raise SystemExit("--near немесе --lat/--lon керек.")

    if args.bearing is not None:
        street = f"қолмен, бағыт {args.bearing:.0f}°"
        start = offset(lat, lon, (args.bearing + 180) % 360, length_m + args.stop_before)
        finish = offset(lat, lon, (args.bearing + 180) % 360, args.stop_before)
        route = [start, finish]
    else:
        street, line = pick_street(cfg, lat, lon, args.street)
        route = trim_to_approach(line, (lat, lon), length_m, args.stop_before)
        print(f"Көше: {street}  ({len(route)} тірек нүкте)")

    count = max(2, int(duration * args.hz) + 1)
    points = resample_polyline(route, count)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    with out_path.open("w", encoding="utf-8") as handle:
        for index, (plat, plon) in enumerate(points):
            handle.write(json.dumps(
                {"t": round(index / args.hz, 3), "lat": round(plat, 6), "lon": round(plon, 6)},
                ensure_ascii=False) + "\n")

    travelled = sum(haversine_m(*points[i], *points[i + 1]) for i in range(len(points) - 1))
    print(f"\nТрек дайын: {out_path}")
    print(f"  видео        : {duration:.1f} сек")
    print(f"  нүкте        : {len(points)} ({args.hz:.0f}/сек)")
    print(f"  жүрілген жол : {travelled:.0f} м  (~{args.speed_kmh:.0f} км/сағ)")
    print(f"  басы         : {points[0][0]:.6f}, {points[0][1]:.6f}")
    print(f"  соңы         : {points[-1][0]:.6f}, {points[-1][1]:.6f}")
    print(f"\nІске қосу:\n  python -m vision.main run --source file "
          f"--video {args.video} --gps-track {out_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
