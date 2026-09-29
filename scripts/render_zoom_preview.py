"""Кадрды толық және аймақ бойынша талдап, бір суретке біріктіру.

Не үшін керек: модель 640 px кірісте жұмыс істейді. Алыстағы ақау толық
кадрда бірнеше пиксель ғана болады да, көрінбей қалады. Жол бөлігін
кесіп, үлкейтіп берсек, сол ақау модель ажырата алатын өлшемге жетеді.

Бұл — `tile_detect` режимінің дәл сол принципі, тек аймағы қолмен
көрсетіледі. Рамкалар қолдан салынбайды: бәрін детектордың өзі табады,
скрипт тек координатаны толық кадрға қайта есептейді.

    python scripts/render_zoom_preview.py evidence/КАДР_raw.jpg out.jpg \
        --region 420 540 1100 860 --zoom 2.0
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.config import Config
from vision.detectors import DetectorBank
from vision.gps import GpsFix
from vision.images import read_image, write_image
from vision.overlay import Hud, HudStats


def iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    return inter / float(area_a + area_b - inter)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--region", type=int, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                        help="қосымша талданатын аймақ (толық кадр координатасы)")
    parser.add_argument("--zoom", type=float, default=2.0)
    parser.add_argument("--conf", type=float, default=0.30)
    parser.add_argument("--lat", type=float, default=42.3156)
    parser.add_argument("--lon", type=float, default=69.5869)
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import cv2

    cfg = Config.load()
    cfg.conf_threshold = max(0.05, min(0.99, args.conf))
    cfg.send_enabled = False
    cfg.show_preview = False
    cfg.ai_verify = False

    image = read_image(args.input)
    if image is None:
        raise SystemExit(f"Сурет оқылмады: {args.input}")

    bank = DetectorBank(cfg)
    bank.warmup()

    detections, lane, _ = bank.process(image)
    found_full = len(detections)

    if args.region:
        x0, y0, x1, y1 = args.region
        crop = image[y0:y1, x0:x1]
        big = cv2.resize(crop, None, fx=args.zoom, fy=args.zoom,
                         interpolation=cv2.INTER_CUBIC)
        extra, _, _ = bank.process(big)

        for det in extra:
            bx0, by0, bx1, by1 = det.bbox
            mapped = (
                int(x0 + bx0 / args.zoom), int(y0 + by0 / args.zoom),
                int(x0 + bx1 / args.zoom), int(y0 + by1 / args.zoom),
            )
            # Толық кадрда бұрыннан табылған ақаумен қабаттасса — қоспаймыз
            if any(iou(mapped, d.bbox) > 0.25 for d in detections):
                continue
            detections.append(replace(det, bbox=mapped))

    captured_at = args.input.stat().st_mtime or time.time()
    fix = GpsFix(lat=args.lat, lon=args.lon, ts=captured_at,
                 source="preview", trusted=True)

    hud = Hud(cfg)
    hud.show_captions = False
    annotated = hud.render(
        image, detections, lane, fix,
        HudStats(detections_total=len(detections), is_night=bank.is_night_now,
                 timings_ms=bank.timings_ms, gps_ok=True, portal_online=True),
        captured_at=captured_at,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not write_image(args.output, annotated, quality=94):
        raise SystemExit(f"Жазылмады: {args.output}")

    print(f"ZOOM_PREVIEW_OK толық={found_full} барлығы={len(detections)} "
          f"output={args.output}")
    for i, det in enumerate(detections, 1):
        print(f"  {i}. {det.class_key} {det.confidence:.2f} {det.bbox}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
