"""Интерфейсті СУРЕТ ретінде шығару — тірі терезені ашпай көру үшін.

Видеодан бірнеше кадр алып, оларға тірі режимдегі HUD-тың дәл өзін
салады да, PNG етіп сақтайды. Презентацияға слайд дайындауға,
интерфейсті тексеруге ыңғайлы.

Қолдану:
    python scripts/preview_frames.py <видео> [--count 4] [--out preview]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vision.config import Config              # noqa: E402
from vision.detectors import DetectorBank     # noqa: E402
from vision.gps import GpsFix                 # noqa: E402
from vision.overlay import Hud, HudStats      # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", help="видеофайл")
    parser.add_argument("--count", type=int, default=4, help="неше кадр")
    parser.add_argument("--out", default="preview", help="шығатын қалта")
    parser.add_argument("--conf", type=float, default=0.45)
    parser.add_argument("--crop-bottom", type=float, default=0.0)
    parser.add_argument("--lat", type=float, default=42.3417)
    parser.add_argument("--lon", type=float, default=69.5901)
    parser.add_argument("--debug", action="store_true", help="төменгі панельді көрсету")
    args = parser.parse_args()

    cfg = Config.load(conf_threshold=args.conf, crop_bottom_frac=args.crop_bottom)
    bank = DetectorBank(cfg)
    hud = Hud(cfg)
    hud.show_debug = args.debug

    capture = cv2.VideoCapture(args.video)
    if not capture.isOpened():
        print(f"Видео ашылмады: {args.video}")
        return 1

    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        print("Кадр саны белгісіз")
        return 1

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    fix = GpsFix(lat=args.lat, lon=args.lon, ts=0.0, source="phone", trusted=True)
    saved = 0

    # Кадрларды видеоның бойына біркелкі таратып аламыз
    for index in range(args.count):
        position = int(total * (index + 0.5) / args.count)
        # Капот детекторы КӨРШІ кадрлардың айырмасына сүйенеді, сондықтан
        # секіріп келген жерден бірнеше ТІЗБЕКТІ кадрды өткіземіз —
        # тірі режимдегі жағдайды дәл қайталайды
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, position - 8))

        frame = None
        detections, lane = [], None
        for _ in range(9):
            ok, raw = capture.read()
            if not ok:
                break
            frame = cfg.crop(raw)
            detections, lane, _ = bank.process(frame)

        if frame is None:
            continue

        stats = HudStats(
            fps=24.0,
            detections_total=len(detections),
            is_night=bank.is_night_now,
            gps_ok=True,
            portal_online=True,
        )
        canvas = hud.render(frame, detections, lane, fix, stats)

        path = out_dir / f"preview_{index + 1:02d}.jpg"
        cv2.imwrite(str(path), canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        saved += 1

        source = lane.source if lane else "—"
        curves = len(lane.left_curve) if lane else 0
        print(f"  {path.name}: {len(detections)} ақау · жол шекарасы: "
              f"{source} ({curves} нүкте)")

    capture.release()
    print(f"\n{saved} кадр сақталды: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
