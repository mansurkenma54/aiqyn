"""RDD2022-де үйретілген YOLOv8 салмағын жүктеу.

Дереккөз: github.com/oracl4/RoadDamageDetection
Датасет:  RDD2022 — 47 420 сурет, 55 000+ белгіленген ақау, 6 елден жиналған
Кластар:  Longitudinal Crack, Transverse Crack, Alligator Crack, Potholes

Қолдану:
    python scripts/fetch_weights.py

Интернет баяу болса файлды қолмен де жүктеп, aiqyn/models/RDD_best.pt
деп қоюға болады.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "models" / "RDD_best.pt"

SOURCES = [
    "https://raw.githubusercontent.com/oracl4/RoadDamageDetection/main/models/YOLOv8_Small_RDD.pt",
    "https://media.githubusercontent.com/media/oracl4/RoadDamageDetection/main/models/YOLOv8_Small_RDD.pt",
]

MIN_SIZE_BYTES = 5_000_000     # шынайы салмақ ~89 МБ; кіші файл = қате бет


def human(size: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


def download(url: str, destination: Path) -> bool:
    temp = destination.with_suffix(".part")
    try:
        with requests.get(url, stream=True, timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", 0))
            done = 0

            with open(temp, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    if total:
                        percent = done * 100 / total
                        bar = "#" * int(percent / 2.5)
                        print(f"\r   [{bar:<40}] {percent:5.1f}%  {human(done)}", end="")
                    else:
                        print(f"\r   жүктелуде... {human(done)}", end="")
            print()

        if temp.stat().st_size < MIN_SIZE_BYTES:
            print(f"   Файл тым кішкентай ({human(temp.stat().st_size)}) — сілтеме дұрыс емес.")
            temp.unlink(missing_ok=True)
            return False

        shutil.move(str(temp), str(destination))
        return True

    except requests.RequestException as exc:
        print(f"\n   Қате: {exc}")
        temp.unlink(missing_ok=True)
        return False


def verify(path: Path) -> bool:
    """Салмақтың шынымен жүктелетінін тексеру."""
    try:
        from ultralytics import YOLO
        model = YOLO(str(path))
        names = model.names
        if isinstance(names, (list, tuple)):
            names = dict(enumerate(names))
        print(f"   Модель кластары: {', '.join(str(v) for v in names.values())}")
        return True
    except Exception as exc:
        print(f"   Модельді ашу сәтсіз: {exc}")
        return False


def main() -> int:
    print("\n" + "=" * 64)
    print("  AIQYN — YOLOv8 (RDD2022) салмағын жүктеу")
    print("=" * 64)

    TARGET.parent.mkdir(parents=True, exist_ok=True)

    if TARGET.exists() and TARGET.stat().st_size >= MIN_SIZE_BYTES:
        print(f"\nСалмақ бұрыннан бар: {TARGET} ({human(TARGET.stat().st_size)})")
        print("Қайта жүктеу үшін осы файлды өшіріңіз.\n")
        return 0

    for index, url in enumerate(SOURCES, start=1):
        print(f"\n{index}) {url}")
        if download(url, TARGET):
            print(f"\nСақталды: {TARGET} ({human(TARGET.stat().st_size)})")
            print("\nТексеру...")
            if verify(TARGET):
                print("\nДайын. Іске қосу:  python -m vision.main run\n")
                return 0
            print("\nФайл жүктелді, бірақ модель ашылмады.\n")
            return 1

    print("\nЖүктеу сәтсіз аяқталды.")
    print("Қолмен жүктеу:")
    print("   1. https://github.com/oracl4/RoadDamageDetection ашыңыз")
    print("   2. models/YOLOv8_Small_RDD.pt файлын жүктеңіз")
    print(f"   3. Оны мына жерге қойыңыз: {TARGET}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
