"""Сыртқы кітапханаларды жобаның ішіне көшіру (offline demo үшін).

Мәселе: оператор порталы Leaflet пен Font Awesome-ды CDN-нен алатын.
Интернет жоқ болса — карта да, белгішелер де жүктелмейді, яғни демо күні
Wi-Fi құласа жоба құлайды. Хакатон офлайн питчингті көздейді.

Шешім: осы файлдарды бір рет жүктеп, `portal/static/vendor/` ішінде
сақтаймыз. Одан кейін порталға интернет тек карта тақтайшалары үшін ғана
керек, ол да үзілсе — интерфейс жұмысын жалғастырады.

Іске қосу (интернет бар кезде, бір рет):
    python scripts/vendor_assets.py

Лицензиялар:
    Leaflet       — BSD-2-Clause
    Font Awesome  — иконкалар CC BY 4.0, шрифт SIL OFL 1.1, CSS MIT
Екеуі де осы файлдардың құрамында өз лицензия ескертпесімен келеді.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "portal" / "static" / "vendor"

LEAFLET = "1.9.4"
FA = "6.5.2"

# Шрифт екеу ғана: Inter (мәтін) және JetBrains Mono (сан, координата, ID).
# Екеуінің де кириллицасы толық — Ә Ғ Қ Ң Ө Ұ Ү Һ І дұрыс көрінеді.
# Variable нұсқасы: бір файлда 100–900 салмақ.
FONT = "https://cdn.jsdelivr.net/npm/@fontsource-variable"

FILES: list[tuple[str, str]] = [
    # ---- Leaflet ----
    (f"https://unpkg.com/leaflet@{LEAFLET}/dist/leaflet.js", "leaflet/leaflet.js"),
    (f"https://unpkg.com/leaflet@{LEAFLET}/dist/leaflet.css", "leaflet/leaflet.css"),
    (f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/marker-icon.png", "leaflet/images/marker-icon.png"),
    (f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/marker-icon-2x.png", "leaflet/images/marker-icon-2x.png"),
    (f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/marker-shadow.png", "leaflet/images/marker-shadow.png"),
    (f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/layers.png", "leaflet/images/layers.png"),
    (f"https://unpkg.com/leaflet@{LEAFLET}/dist/images/layers-2x.png", "leaflet/images/layers-2x.png"),
    # ---- Font Awesome (тек solid қолданылады) ----
    (f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{FA}/css/all.min.css",
     "fontawesome/css/all.min.css"),
    (f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{FA}/webfonts/fa-solid-900.woff2",
     "fontawesome/webfonts/fa-solid-900.woff2"),
    (f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{FA}/webfonts/fa-solid-900.ttf",
     "fontawesome/webfonts/fa-solid-900.ttf"),
    # ---- Шрифттер ----
    (f"{FONT}/inter/files/inter-latin-wght-normal.woff2", "fonts/inter-latin.woff2"),
    (f"{FONT}/inter/files/inter-cyrillic-wght-normal.woff2", "fonts/inter-cyrillic.woff2"),
    (f"{FONT}/inter/files/inter-cyrillic-ext-wght-normal.woff2", "fonts/inter-cyrillic-ext.woff2"),
    (f"{FONT}/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2", "fonts/mono-latin.woff2"),
    (f"{FONT}/jetbrains-mono/files/jetbrains-mono-cyrillic-wght-normal.woff2", "fonts/mono-cyrillic.woff2"),
    # Manrope — тақырыптарға. Тығыз, геометриялық, ірі кегльде әдемі.
    (f"{FONT}/manrope/files/manrope-latin-wght-normal.woff2", "fonts/manrope-latin.woff2"),
    (f"{FONT}/manrope/files/manrope-cyrillic-wght-normal.woff2", "fonts/manrope-cyrillic.woff2"),
    (f"{FONT}/manrope/files/manrope-cyrillic-ext-wght-normal.woff2", "fonts/manrope-cyrillic-ext.woff2"),
]

UA = {"User-Agent": "AIQYN-offline-vendor/1.0 (hackathon offline demo)"}


def fetch(url: str, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read()
    target.write_bytes(data)
    return len(data)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    print(f"Кітапханалар көшірілуде → {VENDOR.relative_to(ROOT)}")
    total = 0
    failed = 0

    for url, rel in FILES:
        target = VENDOR / rel
        try:
            size = fetch(url, target)
        except (urllib.error.URLError, OSError) as exc:
            print(f"  [ҚАТЕ] {rel}: {exc}")
            failed += 1
            continue
        total += size
        print(f"  [ OK ] {rel:46} {size / 1024:7.1f} КБ")

    print(f"\nБарлығы: {total / 1024:.1f} КБ")
    if failed:
        print(f"{failed} файл жүктелмеді — интернетті тексеріп, қайта жүргізіңіз.")
        return 1

    print("Енді портал CDN-ге тәуелді емес.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
