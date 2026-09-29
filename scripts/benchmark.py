"""AIQYN — жергілікті өнімділік өлшемі (benchmark).

Мақсаты бір: сайтта және питчте айтылатын әр санның артында
қайталауға болатын өлшеу тұрсын. Ойдан жазылған метрика жоқ.

Не өлшейді:
    * бір кадрды талдау уақыты (мс) — медиана, орташа, p95
    * секундына неше кадр талдана алады (детекция FPS)
    * құрылғы: процессор (.pt) және Intel графикасы (OpenVINO, бар болса)
    * табылған ақау саны — сенімділік шегі бойынша

Іске қосу:
    python scripts/benchmark.py
    python scripts/benchmark.py --images evidence --runs 20
    python scripts/benchmark.py --device cpu --conf 0.30

Нәтиже `docs/benchmark.json` файлына жазылады — model-card.md сол
файлдағы сандарға сілтейді.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_WEIGHTS = ROOT / "models" / "RDD_best.pt"
OPENVINO_DIR = ROOT / "models" / "RDD_best_openvino_model"
OUT_PATH = ROOT / "docs" / "benchmark.json"


def collect_images(folder: Path, limit: int) -> list[Path]:
    """Дәлел қалтасынан ӨҢДЕЛМЕГЕН кадрларды алу.

    `_raw.jpg` — камерадан шыққан таза кадр. Белгіленген `{id}.jpg`
    файлында рамка мен HUD бар, оны модельге беру дұрыс емес.
    """
    if not folder.exists():
        return []
    raws = sorted(folder.glob("*_raw.jpg"))
    if not raws:
        raws = sorted(p for p in folder.glob("*.jpg") if "_raw" not in p.name)
    return raws[:limit]


def bench_pipeline(folder: Path) -> dict | None:
    """Детекция сәтінен құжат дайын болғанға дейінгі уақыт.

    `evidence/{id}.json` ішіндегі `timestamp` — ақау кадрда көрінген сәт.
    Сол файлдың жазылу уақыты — құжат толық дайын болған сәт (дәлел клипі
    жазылып, мекенжай алынып, ИИ қорытындысы қосылғаннан кейін).
    Айырмасы — «кадрдан құжатқа дейін» деген нақты сан.
    """
    if not folder.exists():
        return None

    deltas: list[float] = []
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith(".rejected.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            detected = datetime.fromisoformat(data["timestamp"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        written = datetime.fromtimestamp(path.stat().st_mtime, tz=detected.tzinfo)
        delta = (written - detected).total_seconds()
        # 0 < t < 120 сек: қалғаны — кейін қолмен көшірілген файл, өлшем емес
        if 0 < delta < 120:
            deltas.append(delta)

    if len(deltas) < 5:
        return None

    deltas.sort()
    p90 = deltas[min(len(deltas) - 1, int(len(deltas) * 0.90))]
    return {
        "events": len(deltas),
        "seconds_median": round(statistics.median(deltas), 1),
        "seconds_mean": round(statistics.fmean(deltas), 1),
        "seconds_min": round(deltas[0], 1),
        "seconds_p90": round(p90, 1),
        "seconds_max": round(deltas[-1], 1),
        "method": "evidence/*.json: timestamp -> file mtime",
    }


def describe_host() -> dict:
    info = {
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu": platform.processor() or "белгісіз",
    }
    try:
        import openvino as ov

        core = ov.Core()
        info["openvino"] = ov.__version__
        info["openvino_devices"] = list(core.available_devices)
        for dev in core.available_devices:
            if dev.startswith("GPU"):
                try:
                    info["gpu_name"] = core.get_property(dev, "FULL_DEVICE_NAME")
                except Exception:
                    pass
                break
    except Exception:
        info["openvino"] = None
    try:
        import ultralytics

        info["ultralytics"] = ultralytics.__version__
    except Exception:
        pass
    return info


def bench_one(model, images: list, runs: int, conf: float, imgsz: int,
              device: str | None = None) -> dict:
    """Бір модельді өлшеу. Алдымен жылыту, содан кейін ғана санау."""
    import cv2

    frames = [cv2.imread(str(p)) for p in images]
    frames = [f for f in frames if f is not None]
    if not frames:
        raise SystemExit("Кадр табылмады — өлшейтін ештеңе жоқ.")

    kwargs = {"conf": conf, "imgsz": imgsz, "verbose": False}
    if device:
        kwargs["device"] = device

    # Жылыту: бірінші шақыру әрқашан баяу (граф құрылады, кэш толады)
    for _ in range(3):
        model.predict(frames[0], **kwargs)

    latencies: list[float] = []
    detections = 0
    per_class: dict[str, int] = {}

    for i in range(runs):
        frame = frames[i % len(frames)]
        t0 = time.perf_counter()
        result = model.predict(frame, **kwargs)[0]
        latencies.append((time.perf_counter() - t0) * 1000.0)

        boxes = result.boxes
        if boxes is not None and len(boxes):
            detections += len(boxes)
            names = result.names or {}
            for cls_id in boxes.cls.tolist():
                name = names.get(int(cls_id), str(int(cls_id)))
                per_class[name] = per_class.get(name, 0) + 1

    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    median = statistics.median(latencies)
    return {
        "runs": len(latencies),
        "unique_frames": len(frames),
        "frame_size": f"{frames[0].shape[1]}x{frames[0].shape[0]}",
        "latency_ms_median": round(median, 1),
        "latency_ms_mean": round(statistics.fmean(latencies), 1),
        "latency_ms_min": round(latencies[0], 1),
        "latency_ms_p95": round(p95, 1),
        "detect_fps_median": round(1000.0 / median, 1),
        "detections_total": detections,
        "detections_per_class": per_class,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AIQYN жергілікті benchmark")
    parser.add_argument("--images", default="evidence", help="кадрлар қалтасы")
    parser.add_argument("--limit", type=int, default=8, help="неше түрлі кадр")
    parser.add_argument("--runs", type=int, default=30, help="барлық өлшеу саны")
    parser.add_argument("--conf", type=float, default=0.30)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", choices=("all", "cpu", "gpu"), default="all")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    images = collect_images(ROOT / args.images, args.limit)
    if not images:
        print(f"[ҚАТЕ] {args.images} ішінде кадр жоқ.")
        return 1

    if not DEFAULT_WEIGHTS.exists():
        print(f"[ҚАТЕ] Салмақ жоқ: {DEFAULT_WEIGHTS}")
        print("       python scripts/fetch_weights.py")
        return 1

    from ultralytics import YOLO

    print("AIQYN benchmark")
    print("=" * 62)
    host = describe_host()
    for key, value in host.items():
        print(f"  {key:18} {value}")
    print(f"  {'кадр':18} {len(images)} дана · {args.runs} өлшеу · conf={args.conf}")
    print("=" * 62)

    report = {
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "host": host,
        "settings": {"conf": args.conf, "imgsz": args.imgsz, "runs": args.runs},
        "weights": str(DEFAULT_WEIGHTS.relative_to(ROOT)),
        "frames": [str(p.relative_to(ROOT)) for p in images],
        "devices": {},
    }

    targets: list[tuple[str, Path, str | None]] = []
    if args.device in ("all", "cpu"):
        targets.append(("cpu · процессор", DEFAULT_WEIGHTS, "cpu"))
    if args.device in ("all", "gpu") and OPENVINO_DIR.exists():
        targets.append(("intel:gpu · OpenVINO iGPU", OPENVINO_DIR, "intel:gpu"))

    for label, path, device in targets:
        print(f"\n▸ {label}")
        try:
            model = YOLO(str(path), task="detect")
            stats = bench_one(model, images, args.runs, args.conf, args.imgsz, device)
        except Exception as exc:                     # noqa: BLE001 — есепте қалсын
            print(f"  өлшенбеді: {exc}")
            report["devices"][label] = {"error": str(exc)}
            continue

        report["devices"][label] = stats
        print(f"  медиана      {stats['latency_ms_median']} мс")
        print(f"  орташа       {stats['latency_ms_mean']} мс")
        print(f"  p95          {stats['latency_ms_p95']} мс")
        print(f"  детекция/сек {stats['detect_fps_median']}")
        print(f"  табылған     {stats['detections_total']} · {stats['detections_per_class']}")

    pipeline = bench_pipeline(ROOT / "evidence")
    if pipeline:
        report["pipeline_detection_to_document"] = pipeline
        print("\n▸ кадрдан құжатқа дейін (нақты оқиғалар)")
        print(f"  оқиға        {pipeline['events']}")
        print(f"  медиана      {pipeline['seconds_median']} сек")
        print(f"  ең жылдам    {pipeline['seconds_min']} сек")
        print(f"  p90          {pipeline['seconds_p90']} сек")
    else:
        print("\n▸ кадрдан құжатқа дейін: өлшеуге дәлел жеткіліксіз (5+ оқиға керек)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nЖазылды: {OUT_PATH.relative_to(ROOT)}")
    print("Осы файлдағы сандар ғана сайтта/питчте айтылуы тиіс.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
