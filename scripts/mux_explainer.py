"""Питч видеосына дикторды және дыбыс эффектілерін қосу.

    python scripts/mux_explainer.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "reports" / "audio" / "explainer"
SFX = ROOT / "reports" / "audio"          # эффектілер демо видеосымен ортақ

VOICE_DB = 0.0
SFX_DB = -15.0

# Қай бөлікте қандай эффект қосылады (бөліктің басынан кейінгі секунд)
SFX_MARKS = {
    "problem":  ("whoosh", 0.0),
    "sheriff":  ("scan", 0.15),
    "detect":   ("blip", 0.35),
    "filter":   ("blip", 0.5),
    "offline":  ("save", 0.3),
    "operator": ("confirm", 0.25),
    "city":     ("confirm", 0.4),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Питч видеосына дыбыс қосу")
    parser.add_argument("--video", default="reports/AIQYN-pitch-silent.mp4")
    parser.add_argument("--out", default="reports/AIQYN-pitch.mp4")
    parser.add_argument("--crf", type=int, default=20)
    args = parser.parse_args()

    marks = json.loads((AUDIO / "marks.json").read_text(encoding="utf-8"))
    narration = json.loads((AUDIO / "narration.json").read_text(encoding="utf-8"))
    voice_files = {item["key"]: AUDIO / item["file"] for item in narration["lines"]}

    video = ROOT / args.video
    out = ROOT / args.out
    if not video.exists():
        raise SystemExit("Видео жоқ: %s" % video)

    inputs: list[str] = ["-i", str(video)]
    filters: list[str] = []
    streams: list[str] = []
    index = 1

    for mark in marks["marks"]:
        if mark["kind"] != "voice":
            continue
        key = mark["name"]

        # диктор
        path = voice_files.get(key)
        if path and path.exists():
            delay = int(max(0.0, mark["at"]) * 1000)
            label = "a%d" % index
            filters.append("[%d:a]adelay=%d|%d,volume=%.1fdB[%s]"
                           % (index, delay, delay, VOICE_DB, label))
            streams.append("[%s]" % label)
            inputs += ["-i", str(path)]
            index += 1

        # эффект
        if key in SFX_MARKS:
            name, offset = SFX_MARKS[key]
            sfx_path = SFX / ("sfx_%s.wav" % name)
            if sfx_path.exists():
                delay = int(max(0.0, mark["at"] + offset) * 1000)
                label = "a%d" % index
                filters.append("[%d:a]adelay=%d|%d,volume=%.1fdB[%s]"
                               % (index, delay, delay, SFX_DB, label))
                streams.append("[%s]" % label)
                inputs += ["-i", str(sfx_path)]
                index += 1

    if not streams:
        raise SystemExit("Дыбыс табылмады.")

    filters.append("%samix=inputs=%d:normalize=0:dropout_transition=0[mixraw]"
                   % ("".join(streams), len(streams)))
    filters.append("[mixraw]alimiter=limit=0.95,apad,atrim=0:%.3f,"
                   "afade=t=out:st=%.3f:d=0.6[aout]"
                   % (marks["duration"], max(0.0, marks["duration"] - 0.6)))

    command = [
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out),
    ]
    print("Араластырылуда: %d жол…" % len(streams))
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-2500:])
        raise SystemExit("ffmpeg сәтсіз.")

    print("\nДайын: %s" % out)
    print("  өлшемі   : %.1f МБ" % (out.stat().st_size / 1e6))
    print("  ұзақтығы : %.1f сек" % marks["duration"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
