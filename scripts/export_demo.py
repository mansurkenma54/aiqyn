"""Сайттағы деректерді ДЕМО суреті етіп сақтау (Vercel үшін).

Не істейді:
    * қазіргі құжаттар мен аймақтарды `portal/demo/snapshot.json` файлына жазады
    * дәлел фотоларын `portal/demo/media/` қалтасына көшіреді

Не үшін: Vercel — serverless, жазылған дерек сақталмайды. Ал демо суреті
репозиторийде жатса, сайт әр ашылғанда оны автоматты жүктейді де, жюри
сілтемені басқанда карта толы болып тұрады.

Қолдану:
    python scripts/export_demo.py
    python scripts/export_demo.py --limit 30
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portal import db  # noqa: E402

DEMO_DIR = ROOT / "portal" / "demo"
MEDIA_SRC = ROOT / "portal" / "media"


def copy_photo(source: Path, target: Path, max_width: int, quality: int) -> None:
    """Дәлел фотосын демо қалтасына көшіру.

    Толық сападағы кадр ресми құжат үшін керек, ал демо сайтта ол тек
    экранда көрінеді. Сондықтан көшірмені кішірейтеміз: жюри бетті тез
    ашады, Vercel бумасы да шектен аспайды.
    """
    if max_width <= 0:
        shutil.copy2(source, target)
        return
    try:
        from PIL import Image

        with Image.open(source) as image:
            image = image.convert("RGB")
            if image.width > max_width:
                height = round(image.height * max_width / image.width)
                image = image.resize((max_width, height), Image.LANCZOS)
            image.save(target, format="JPEG", quality=quality, optimize=True)
    except Exception:                      # Pillow жоқ немесе кадр бүлінген
        shutil.copy2(source, target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--with-video", action="store_true",
                        help="видео клиптерді де көшіру (файл көлемі өседі)")
    parser.add_argument("--max-width", type=int, default=1280,
                        help="демо фотосының ең үлкен ені (0 = кішірейтпеу)")
    parser.add_argument("--quality", type=int, default=82)
    parser.add_argument("--prefer-ai", action="store_true", default=True,
                        help="ИИ талдауы бар оқиғаларды алдыға шығару")
    parser.add_argument("--no-prefer-ai", dest="prefer_ai", action="store_false")
    args = parser.parse_args()

    db.init_db()

    documents = db.list_documents(status="all", limit=1000)

    # Демо картасында әр оқиғаның толық карточкасы болғаны маңызды:
    # фотосы да, ИИ қорытындысы да бар оқиғалар алдымен алынады.
    if args.prefer_ai:
        def rank(doc: dict) -> tuple:
            size = (doc.get("ai_size") or "").strip().lower()
            rich = size and size not in ("жоқ", "анықталмады")
            return (
                0 if doc.get("photo_file") else 1,
                0 if doc.get("ai_verified") else 1,   # ИИ растаған оқиғалар алдымен
                0 if rich else 1,                     # өлшемі мен орны жазылғандар
                -(doc.get("confidence") or 0),
            )
        documents.sort(key=rank)

    documents = documents[:args.limit]
    zones = db.list_zones(active_only=False)

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    media_dst = DEMO_DIR / "media"
    if media_dst.exists():
        shutil.rmtree(media_dst)
    media_dst.mkdir(parents=True, exist_ok=True)

    exported = []
    copied = 0

    for document in documents:
        document.pop("raw_json", None)
        document.pop("history", None)

        event_id = document.get("event_id")
        source = MEDIA_SRC / event_id
        target = media_dst / event_id

        if source.exists():
            target.mkdir(parents=True, exist_ok=True)
            for name in (document.get("photo_file"), document.get("after_photo_file")):
                if name and (source / name).exists():
                    copy_photo(source / name, target / name, args.max_width, args.quality)
                    copied += 1
            if args.with_video and document.get("video_file"):
                video = source / document["video_file"]
                if video.exists():
                    shutil.copy2(video, target / document["video_file"])
                    copied += 1
                else:
                    document["video_file"] = None
            elif not args.with_video:
                # Демода видео жоқ: файл көлемі GitHub үшін тым үлкен болмасын
                document["video_file"] = None

        exported.append(document)

    snapshot = {
        "generated_at": db.now_iso(),
        "documents": exported,
        "zones": [
            {
                "name": z.get("name"),
                "kind": z.get("kind"),
                "shape": z.get("shape"),
                "lat": z.get("lat"),
                "lon": z.get("lon"),
                "radius_m": z.get("radius_m"),
                "points": z.get("points") or z.get("polygon"),
                "corridor_m": z.get("corridor_m"),
                "valid_until": z.get("valid_until"),
                "boundary_verified": z.get("boundary_verified"),
                "note": z.get("note"),
            }
            for z in zones
        ],
    }

    path = DEMO_DIR / "snapshot.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")

    size_mb = sum(f.stat().st_size for f in DEMO_DIR.rglob("*") if f.is_file()) / 1e6

    print()
    print("=" * 62)
    print("  ДЕМО СУРЕТІ ДАЙЫН")
    print("=" * 62)
    print(f"  Құжат      : {len(exported)}")
    print(f"  Аймақ      : {len(snapshot['zones'])}")
    print(f"  Файл       : {copied} сурет")
    print(f"  Қалта      : {DEMO_DIR}")
    print(f"  Жалпы көлем: {size_mb:.1f} МБ")
    print()
    print("  Бұл қалтаны GitHub-қа жүктеңіз — Vercel сайтты сол деректермен")
    print("  ашады. Жаңа деректер қосылса, осы команданы қайта жүргізіңіз.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
