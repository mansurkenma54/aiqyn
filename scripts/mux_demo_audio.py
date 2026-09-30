"""Диктор мен дыбыс эффектілерін демо видеоға қосу.

`render_demo_video.py` әр бөлікті салғанда «дыбыс белгілерін» жазып
қалдырады (`reports/audio/marks.json`): қай секундта қай диктор жолы,
қай секундта қай эффект қосылуы керек. Бұл скрипт соны оқып, барлығын
бір жолға араластырады да, видеоға жабыстырады.

    python scripts/make_narration.py
    python scripts/render_demo_video.py --video ... --gps-track ...
    python scripts/mux_demo_audio.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "reports" / "audio"

VOICE_DB = 0.0        # диктор — негізгі қабат
SFX_DB = -13.0        # эффектілер басым болмауы керек


def main() -> int:
    parser = argparse.ArgumentParser(description="Дыбысты видеоға қосу")
    parser.add_argument("--video", default="reports/AIQYN-demo-silent.mp4")
    parser.add_argument("--out", default="reports/AIQYN-demo.mp4")
    parser.add_argument("--crf", type=int, default=20)
    args = parser.parse_args()

    marks_path = AUDIO / "marks.json"
    narration_path = AUDIO / "narration.json"
    if not marks_path.exists() or not narration_path.exists():
        raise SystemExit("marks.json / narration.json жоқ — алдымен видеоны рендерлеңіз.")

    marks = json.loads(marks_path.read_text(encoding="utf-8"))
    narration = json.loads(narration_path.read_text(encoding="utf-8"))
    voice_files = {item["key"]: AUDIO / item["file"] for item in narration["lines"]}

    video = Path(args.video)
    if not video.is_absolute():
        video = ROOT / video
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if not video.exists():
        raise SystemExit("Видео табылмады: %s" % video)

    inputs: list[str] = ["-i", str(video)]
    filters: list[str] = []
    streams: list[str] = []
    index = 1

    for mark in marks["marks"]:
        if mark["kind"] == "voice":
            path = voice_files.get(mark["name"])
            gain = VOICE_DB
        else:
            path = AUDIO / ("sfx_%s.wav" % mark["name"])
            gain = SFX_DB
        if path is None or not path.exists():
            print("  өткізілді (файл жоқ): %s %s" % (mark["kind"], mark["name"]))
            continue

        delay_ms = int(max(0.0, mark["at"]) * 1000)
        label = "a%d" % index
        filters.append(
            "[%d:a]adelay=%d|%d,volume=%.1fdB[%s]" % (index, delay_ms, delay_ms, gain, label)
        )
        streams.append("[%s]" % label)
        inputs += ["-i", str(path)]
        index += 1

    if not streams:
        raise SystemExit("Қосылатын дыбыс табылмады.")

    filters.append("%samix=inputs=%d:normalize=0:dropout_transition=0[mixraw]"
                   % ("".join(streams), len(streams)))
    # Шектен шықпауы үшін жұмсақ шектеу + видеоның дәл ұзындығына кесу
    filters.append("[mixraw]alimiter=limit=0.95,apad,atrim=0:%.3f,"
                   "afade=t=out:st=%.3f:d=0.45[aout]"
                   % (marks["duration"], max(0.0, marks["duration"] - 0.45)))

    command = [
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out),
    ]
    print("Араластырылуда: %d дыбыс жолы…" % len(streams))
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-2500:])
        raise SystemExit("ffmpeg сәтсіз аяқталды.")

    size_mb = out.stat().st_size / 1e6
    print("\nДайын: %s" % out)
    print("  өлшемі   : %.1f МБ" % size_mb)
    print("  ұзақтығы : %.1f сек" % marks["duration"])
    print("  дыбыс    : %d диктор жолы + эффектілер"
          % sum(1 for m in marks["marks"] if m["kind"] == "voice"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
