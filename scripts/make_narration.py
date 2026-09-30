"""Демо видеоға ДИКТОР дауысын және дыбыс эффектілерін дайындау.

Диктор — Microsoft Edge нейрон дауысы (`edge-tts`, тегін, кілт қажет емес).
Дыбыс эффектілері математикамен синтезделеді: сырттан файл жүктелмейді,
сондықтан пакет толығымен өз ішінде тұйықталған.

Шығысы: `reports/audio/`
    v01.mp3 … v08.mp3     диктор жолдары
    sfx_*.wav             эффектілер
    narration.json        әр жолдың ұзақтығы (видеоны соған қарай құрамыз)

    python scripts/make_narration.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "audio"

VOICE = "ru-RU-DmitryNeural"
RATE = "+18%"           # видео 30 секундқа сыюы керек

# Диктор мәтіні. Әр жол видеоның бір сатысына сәйкес келеді.
# Қысқа сөйлем әдейі: 8 жолдың бәрі 25 секундтан аспауы тиіс.
LINES = [
    ("intro",   "АйКын. Своя модель, обученная на сорока семи тысячах снимков."),
    ("scan",    "Разбираем каждый кадр: целиком и по двум зонам."),
    ("raw",     "Сорок четыре сигнала. Сигнал — ещё не дефект."),
    ("confirm", "Подтверждение по времени убирает тени и пятна."),
    ("merge",   "Тот же дефект найден дважды — система его объединила."),
    ("ai",      "Второй этап: нейросеть проверяет кандидата трижды."),
    ("size",    "Порог размера отсекает мелочь."),
    ("save",    "Дефект уходит в базу: координата, фото, видео, документ."),
    ("final",   "Итог: два наряда на ремонт."),
]

SR = 44100


# ------------------------------------------------------------------
#  Дыбыс эффектілері (синтез)
# ------------------------------------------------------------------

def _env(n: int, attack: float = 0.01, release: float = 0.25) -> np.ndarray:
    """Жұмсақ шабуыл мен өшу — «шырт» етпеуі үшін."""
    a = max(1, int(n * attack))
    r = max(1, int(n * release))
    env = np.ones(n)
    env[:a] = np.linspace(0, 1, a) ** 2
    env[-r:] = np.linspace(1, 0, r) ** 2
    return env


def _write(path: Path, wave: np.ndarray, gain: float = 0.55) -> None:
    wave = wave / (np.max(np.abs(wave)) or 1.0) * gain
    pcm = (wave * 32767).astype(np.int16)
    import wave as wavemod
    with wavemod.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())


def _bell(freq: float, seconds: float, decay: float = 6.0,
          partials=(1.0, 2.0, 3.0), weights=(1.0, 0.32, 0.12)) -> np.ndarray:
    """Қоңырау үні: негізгі тон + бірнеше обертон, экспоненциалды өшумен.

    Таза синусоида «арзан бипер» болып естіледі. Обертондар қосылғанда
    үн ағаш/металл аспапқа ұқсап, құлаққа жағымды болады.
    """
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    wave = np.zeros(len(t))
    for mult, weight in zip(partials, weights):
        wave += weight * np.sin(2 * np.pi * freq * mult * t) * np.exp(-decay * mult * t)
    return wave * _env(len(t), 0.004, 0.30)


def _lowpass(signal: np.ndarray, cutoff_start: float, cutoff_end: float) -> np.ndarray:
    """Қарапайым бір полюсті сүзгі, жиілігі уақыт бойынша өзгереді."""
    out = np.zeros(len(signal))
    state = 0.0
    for i, value in enumerate(signal):
        k = i / max(1, len(signal) - 1)
        alpha = cutoff_start + (cutoff_end - cutoff_start) * k
        state += alpha * (value - state)
        out[i] = state
    return out


def sfx_scan(seconds: float = 1.4) -> np.ndarray:
    """Сканер: тыныш ауа тәрізді сыбдыр, өткір емес."""
    n = int(SR * seconds)
    noise = np.random.default_rng(11).normal(0, 1, n)
    body = _lowpass(noise, 0.010, 0.055)
    t = np.linspace(0, seconds, n, endpoint=False)
    hum = 0.22 * np.sin(2 * np.pi * 196 * t)          # G3 — тыныш негіз
    return (body * 1.6 + hum) * _env(n, 0.22, 0.45)


def sfx_blip(seconds: float = 0.42) -> np.ndarray:
    """Детекция: жұмсақ маримба соғысы (C6)."""
    return _bell(1046.5, seconds, decay=9.0)


def sfx_confirm(seconds: float = 1.5) -> np.ndarray:
    """Растау: үш нотадан тұратын жарқын аккорд (C-E-G)."""
    wave = np.zeros(int(SR * seconds))
    for index, freq in enumerate((523.25, 659.25, 783.99)):
        note = _bell(freq, seconds, decay=2.6)
        shift = int(SR * 0.10 * index)
        wave[shift:shift + len(note) - shift] += note[:len(note) - shift] * (1.0 - 0.15 * index)
    return wave


def sfx_reject(seconds: float = 0.85) -> np.ndarray:
    """Жоққа шығару: жұмсақ, күңгірт төмен нота. Ызыңдамайды."""
    wave = _bell(220.0, seconds, decay=4.5, partials=(1.0, 1.5), weights=(1.0, 0.18))
    wave += 0.5 * _bell(174.61, seconds, decay=4.0, partials=(1.0,), weights=(1.0,))
    return wave


def sfx_merge(seconds: float = 1.1) -> np.ndarray:
    """Біріктіру: екі нота бір нотаға жиналады (октава -> унисон)."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    k = (t / seconds) ** 0.7
    low = np.sin(2 * np.pi * (392.0 + 130.0 * k) * t)      # G4 -> C5
    high = np.sin(2 * np.pi * (784.0 - 261.0 * k) * t)     # G5 -> C5
    body = (low + high) * 0.5 * np.exp(-2.2 * t)
    return body * _env(len(t), 0.03, 0.42)


def sfx_save(seconds: float = 1.2) -> np.ndarray:
    """Базаға жазылды: жоғары қарай өрлейтін үш нота (C-E-A)."""
    wave = np.zeros(int(SR * seconds))
    for index, freq in enumerate((523.25, 659.25, 880.0)):
        note = _bell(freq, seconds, decay=5.0)
        shift = int(SR * 0.13 * index)
        wave[shift:shift + len(note) - shift] += note[:len(note) - shift]
    return wave


def sfx_whoosh(seconds: float = 0.7) -> np.ndarray:
    """Бөлім ауысуы: жұмсақ ауа лебі."""
    n = int(SR * seconds)
    noise = np.random.default_rng(7).normal(0, 1, n)
    return _lowpass(noise, 0.006, 0.09) * _env(n, 0.28, 0.55)


SFX = {
    "scan": sfx_scan,
    "blip": sfx_blip,
    "confirm": sfx_confirm,
    "reject": sfx_reject,
    "merge": sfx_merge,
    "save": sfx_save,
    "whoosh": sfx_whoosh,
}


# ------------------------------------------------------------------
#  Диктор
# ------------------------------------------------------------------

async def speak(text: str, path: Path) -> None:
    """Бір жолды дауыстап, басы-соңындағы үнсіздікті кесу.

    edge-tts әр файлдың басына да, соңына да үнсіздік қосады. Сегіз жолда
    ол 3-4 секундқа жетеді — видео 30 секундқа сыймай қалады.
    """
    import edge_tts
    raw = path.with_name("_raw_" + path.name)
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(raw))
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(raw),
         "-af", "silenceremove=start_periods=1:start_silence=0.03:"
                "start_threshold=-45dB:detection=peak,"
                "areverse,"
                "silenceremove=start_periods=1:start_silence=0.03:"
                "start_threshold=-45dB:detection=peak,"
                "areverse",
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

    print("1/2  Диктор жолдары (%s)…" % VOICE)
    meta = []
    for index, (key, text) in enumerate(LINES, 1):
        path = OUT / ("v%02d_%s.mp3" % (index, key))
        await speak(text, path)
        seconds = duration_sec(path)
        meta.append({"key": key, "file": path.name, "text": text,
                     "duration": round(seconds, 3)})
        print("     %d. %-8s %5.2f сек   %s" % (index, key, seconds, text[:58]))

    total = sum(item["duration"] for item in meta)
    print("     барлығы: %.1f сек" % total)

    print("2/2  Дыбыс эффектілері…")
    for name, maker in SFX.items():
        path = OUT / ("sfx_%s.wav" % name)
        _write(path, maker())
        print("     sfx_%s.wav" % name)

    (OUT / "narration.json").write_text(
        json.dumps({"voice": VOICE, "rate": RATE, "lines": meta,
                    "total": round(total, 3)}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print("\nДайын: %s" % OUT)
    print("Диктордың жалпы ұзақтығы: %.1f сек" % total)
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
