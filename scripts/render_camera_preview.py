"""Бір суреттен AIQYN камера интерфейсінің қауіпсіз preview нұсқасын жасау.

Бұл скрипт порталға ештеңе жібермейді және сыртқы AI сервисін шақырмайды;
жергілікті детектор, жол геометриясы және HUD ғана қолданылады.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.config import Config
from vision.detectors import DetectorBank
from vision.gps import GpsFix
from vision.images import read_image, write_image
from vision.overlay import Hud, HudStats


def render(
    input_path: Path,
    output_path: Path,
    *,
    lane_only: bool = False,
    conf: float | None = None,
) -> tuple[int, int]:
    cfg = Config.load()
    if conf is not None:
        cfg.conf_threshold = max(0.05, min(0.99, conf))
    cfg.send_enabled = False
    cfg.show_preview = False
    cfg.ai_verify = False
    if lane_only:
        cfg.enable_yolo = False
        cfg.enable_flood = False
        cfg.enable_streetlight = False

    image = read_image(input_path)
    if image is None:
        raise RuntimeError(f"Сурет оқылмады: {input_path}")

    bank = DetectorBank(cfg)
    bank.warmup()
    detections, lane, _ = bank.process(image)

    captured_at = input_path.stat().st_mtime or time.time()
    fix = GpsFix(
        lat=42.3156,
        lon=69.5869,
        ts=captured_at,
        source="preview",
        trusted=True,
    )
    hud = Hud(cfg)
    hud.show_captions = False
    annotated = hud.render(
        image,
        detections,
        lane,
        fix,
        HudStats(
            detections_total=len(detections),
            is_night=bank.is_night_now,
            timings_ms=bank.timings_ms,
            gps_ok=True,
            portal_online=True,
        ),
        captured_at=captured_at,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not write_image(output_path, annotated, quality=94):
        raise RuntimeError(f"Preview жазылмады: {output_path}")
    return len(detections), int(lane is not None and lane.polygon is not None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--lane-only", action="store_true")
    parser.add_argument("--conf", type=float, default=None)
    args = parser.parse_args()
    count, lane_ok = render(
        args.input,
        args.output,
        lane_only=args.lane_only,
        conf=args.conf,
    )
    print(f"CAMERA_PREVIEW_OK detections={count} lane={lane_ok} output={args.output}")


if __name__ == "__main__":
    main()
