"""Түсіндірме видеосының дикторы (ҚАЗАҚША).

Негізгі желі: қалада камера бұрыннан бар («Кибер Шериф»), біз оған көру
қабілетін қосамыз. Телефон — тек тест құралы, сондықтан мұнда айтылмайды.

    python scripts/make_explainer_voice.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "audio" / "explainer"

VOICE = "kk-KZ-DauletNeural"
RATE = "+12%"

LINES = [
    ("logo",     "АЙҚЫН. Шымкенттің жол инфрақұрылымын автоматты бақылау жүйесі."),
    ("problem",  "Үш мың сегіз жүз километр көше. Мұны қолмен тексеріп шығу мүмкін емес."),
    ("sheriff",  "Бірақ қалада камера бұрыннан бар. Кибер Шериф патрульдері "
                 "күн сайын осы көшелерді аралайды."),
    ("noequip",  "Біз жабдық сатып алмаймыз. Бар камераға көру қабілетін қосамыз."),
    ("detect",   "Жүйе ақауды өзі табады — ол әлі шұңқыр емес, жарықшақ кезінде."),
    ("filter",   "Автоматиканың әлсіз тұсы — жалған дабыл. "
                 "Нейрожелі әр үміткерді үш рет тексереді."),
    ("offline",  "Интернет қажет емес: бәрі көлікте есептеледі, "
                 "байланыс қосылғанда базаға түседі."),
    ("operator", "Оператор дәлелді көріп, офистен растайды."),
    ("scale",    "Дрон да, стационар камера да сол тізбекке қосылады."),
    ("city",     "АЙҚЫН. Ақылды қала Шымкенттің бір бөлігі."),
]


async def speak(text: str, path: Path) -> None:
    import edge_tts
    raw = path.with_name("_raw_" + path.name)
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(raw))
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(raw),
         "-af", "silenceremove=start_periods=1:start_silence=0.03:"
                "start_threshold=-45dB:detection=peak,areverse,"
                "silenceremove=start_periods=1:start_silence=0.03:"
                "start_threshold=-45dB:detection=peak,areverse",
         str(path)],
        check=False, capture_output=True,
    )
    if path.exists():
        raw.unlink(missing_ok=True)
    else:
        raw.rename(path)


def duration_sec(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=False)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


async def main_async() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = []
    for index, (key, text) in enumerate(LINES, 1):
        path = OUT / ("e%02d_%s.mp3" % (index, key))
        await speak(text, path)
        seconds = duration_sec(path)
        meta.append({"key": key, "file": path.name, "text": text,
                     "duration": round(seconds, 3)})
        print("  %2d. %-9s %5.2f сек  %s" % (index, key, seconds, text[:58]))
    total = sum(m["duration"] for m in meta)
    (OUT / "narration.json").write_text(
        json.dumps({"voice": VOICE, "rate": RATE, "lines": meta,
                    "total": round(total, 3)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("\nБарлығы: %.1f сек  ->  %s" % (total, OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
