"""Промо видеосының дикторы (орысша).

Техникалық демоның дикторынан бөлек: мұнда МОДЕЛЬ туралы емес, ЖҮЙЕНІҢ
ҚАЛАЙ ЖҰМЫС ІСТЕЙТІНІ мен ерекшелігі айтылады — қосымша жабдық керек емес,
интернетсіз жұмыс істейді, бірден кірісуге болады.

Шығысы: `reports/audio/promo/`

    python scripts/make_promo_voice.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "audio" / "promo"

VOICE = "ru-RU-DmitryNeural"
RATE = "+12%"

LINES = [
    ("logo",    "АйКын. Автоматический контроль состояния дорог."),
    ("problem", "В городе тысячи дефектов. Сегодня их считают вручную — "
                "медленно и не полностью."),
    ("phone",   "Наше решение: обычный смартфон на лобовом стекле. "
                "Больше никакого оборудования."),
    ("work",    "Поехали. Система сама находит дефект, проверяет его "
                "и оценивает размер."),
    ("doc",     "Координата, фото, видеодоказательство и официальный документ — "
                "создаются автоматически."),
    ("offline", "Интернет не нужен: всё считается прямо в машине."),
    ("operator","Оператор подтверждает — заявка уходит в дорожное управление."),
    ("city",    "АйКын. Часть умного города Шымкент."),
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
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


async def main_async() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = []
    for index, (key, text) in enumerate(LINES, 1):
        path = OUT / ("p%02d_%s.mp3" % (index, key))
        await speak(text, path)
        seconds = duration_sec(path)
        meta.append({"key": key, "file": path.name, "text": text,
                     "duration": round(seconds, 3)})
        print("  %d. %-9s %5.2f сек  %s" % (index, key, seconds, text[:56]))
    total = sum(m["duration"] for m in meta)
    (OUT / "promo_narration.json").write_text(
        json.dumps({"voice": VOICE, "rate": RATE, "lines": meta,
                    "total": round(total, 3)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("\nБарлығы: %.1f сек  ->  %s" % (total, OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
