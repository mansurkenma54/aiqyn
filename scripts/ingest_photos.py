# -*- coding: utf-8 -*-
"""Нақты суреттерден оқиға жасау — жүйенің ӨЗ тізбегімен.

Не үшін: демо деректері нанымды болуы керек. Ойдан шығарылған
координата да, қолмен жазылған мекенжай да жарамайды — жюри бірден
байқайды. Бұл скрипт телефоннан түсірілген суреттерді жүйенің
нағыз жолымен өткізеді:

    HEIC/JPG → EXIF-тен GPS → YOLO детекторы → қызыл рамка
             → 2ГИС мекенжайы → ИИ-тексеруші → ресми құжат → дерекқор

Ойдан ештеңе қосылмайды: координата суреттің өзінде тұр, мекенжай
2ГИС-тен келеді, ақауды модель табады.

Іске қосу:
    python scripts/ingest_photos.py "C:/.../Telegram Desktop"
    python scripts/ingest_photos.py <қалта> --wipe      # ескісін өшіру
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import cv2
import numpy as np

from portal import db
from vision import categories
from vision.config import Config
from vision.detectors import DetectorBank
from vision.document import build_document, new_event_id
from vision.env import load_env
from vision.geocode import Geocoder
from vision.gps import GpsFix

MEDIA = ROOT / "portal" / "media"
LONG_SIDE = 1920           # дәлел кадры: детекторға да, көзге де жеткілікті


# ------------------------------------------------------------
#  Сурет оқу
# ------------------------------------------------------------

def load_image(path: Path) -> tuple[np.ndarray, tuple[float, float] | None, float | None]:
    """Суретті, ондағы GPS-ті және түсірілген уақытын қайтарады."""
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    image = Image.open(path)
    exif = image.getexif()

    coords = None
    gps_ifd = exif.get_ifd(0x8825) if exif else {}
    if gps_ifd and 2 in gps_ifd and 4 in gps_ifd:
        def to_deg(value, ref):
            deg = float(value[0]) + float(value[1]) / 60 + float(value[2]) / 3600
            return -deg if str(ref).upper() in ("S", "W") else deg
        coords = (to_deg(gps_ifd[2], gps_ifd.get(1, "N")),
                  to_deg(gps_ifd[4], gps_ifd.get(3, "E")))

    shot_at = None
    raw = exif.get(36867) or exif.get(306)
    if raw:
        try:
            shot_at = time.mktime(time.strptime(str(raw), "%Y:%m:%d %H:%M:%S"))
        except ValueError:
            shot_at = None

    frame = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
    height, width = frame.shape[:2]
    if max(height, width) > LONG_SIDE:
        scale = LONG_SIDE / max(height, width)
        frame = cv2.resize(frame, (int(width * scale), int(height * scale)),
                           interpolation=cv2.INTER_AREA)
    return frame, coords, shot_at


# ------------------------------------------------------------
#  Қызыл рамка — жобаның өз тілінде
# ------------------------------------------------------------

RED = (31, 59, 255)        # BGR — categories.py-дегі ORANGE


def draw_boxes(frame: np.ndarray, detections: list) -> np.ndarray:
    """Табылған ақауларды қызыл рамкамен қоршау.

    Сайттағы дәлел кадрларымен бір стиль: жіңішке рамка, жоғарғы
    сол жақта нөмір, оң жақта түрі мен сенімділігі.
    """
    canvas = frame.copy()
    overlay = canvas.copy()
    strong = canvas.copy()          # ірі ақаулардың қалың бояуы
    height, width = canvas.shape[:2]
    thickness = max(2, round(width / 640))
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.42, width / 2100)

    frame_area = float(height * width)
    for index, det in enumerate(detections, start=1):
        x1, y1, x2, y2 = det.bbox
        # ІРІ ақау бос рамкамен көрінбейді — «мұнда не бар?» деген
        # сұрақ туады. Сондықтан ол ТОЛЫҚ боялады: аймақтың бәрі
        # бүлінгені бірден көрінеді. Ұсақ ақауда керісінше — рамка
        # жеткілікті, бояу астындағы дәлелді жауып тастар еді.
        big = (x2 - x1) * (y2 - y1) > 0.045 * frame_area
        cv2.rectangle(overlay, (x1, y1), (x2, y2), RED, -1)
        if big:
            cv2.rectangle(strong, (x1, y1), (x2, y2), RED, -1)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), RED,
                      thickness + (1 if big else 0))

        label = f"{index:02d}"
        info = f"{categories.get(det.class_key).short}  {round(det.confidence * 100)}%"
        (lw, lh), _ = cv2.getTextSize(label, font, scale, 2)
        (iw, ih), _ = cv2.getTextSize(info, font, scale * 0.82, 1)

        pad = int(6 * scale / 0.5)
        cv2.rectangle(canvas, (x1, max(0, y1 - lh - pad * 2)),
                      (x1 + lw + pad * 2, y1), RED, -1)
        cv2.putText(canvas, label, (x1 + pad, max(lh, y1 - pad)),
                    font, scale, (255, 255, 255), 2, cv2.LINE_AA)

        tx = min(x1 + lw + pad * 3, width - iw - pad)
        cv2.rectangle(canvas, (tx - pad, max(0, y1 - ih - pad * 2)),
                      (tx + iw + pad, y1), (20, 20, 22), -1)
        cv2.putText(canvas, info, (tx, max(ih, y1 - pad)),
                    font, scale * 0.82, (255, 255, 255), 1, cv2.LINE_AA)

    canvas = cv2.addWeighted(overlay, 0.13, canvas, 0.87, 0)
    canvas = cv2.addWeighted(strong, 0.22, canvas, 0.78, 0)

    # Жоғарғы жолақ — сайттағы кадрлармен бірдей «REC · AI ROAD SCAN»
    bar = max(26, int(height * 0.045))
    strip = canvas[0:bar].copy()
    canvas[0:bar] = cv2.addWeighted(strip, 0.25,
                                    np.zeros_like(strip), 0.75, 0)
    cv2.circle(canvas, (int(bar * 0.55), bar // 2), max(3, bar // 7), (60, 60, 255), -1)
    cv2.putText(canvas, "REC   AI ROAD SCAN", (int(bar * 1.05), int(bar * 0.68)),
                font, scale * 0.75, (235, 235, 235), 1, cv2.LINE_AA)
    return canvas


def _contains(outer, inner, ratio: float = 0.72) -> bool:
    """inner рамкасының қандай бөлігі outer ішінде жатыр."""
    ax1, ay1, ax2, ay2 = outer
    bx1, by1, bx2, by2 = inner
    ox = max(0, min(ax2, bx2) - max(ax1, bx1))
    oy = max(0, min(ay2, by2) - max(ay1, by1))
    inner_area = max(1, (bx2 - bx1) * (by2 - by1))
    return (ox * oy) / inner_area >= ratio


def clean_boxes(detections: list, frame_shape) -> list:
    """Дәлелге жарамайтын рамкаларды алып тастау.

    Екі жағдай:
      1) рамка кадрдың төрттен бірінен астамын жауып тұр — ол ақауды
         емес, бүкіл көріністі қоршайды, дәлел бола алмайды;
      2) бір рамка екіншісінің ішінде жатыр — ұя болып қалған қосарлық,
         тек ірісі (немесе сенімдісі) қалуы керек.
    """
    height, width = frame_shape[:2]
    frame_area = float(height * width)

    kept = [d for d in detections
            if (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]) <= 0.26 * frame_area]

    # Ірісінен бастап жүреміз: кішісі ішінде қалса — тастаймыз
    kept.sort(key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]), reverse=True)
    result: list = []
    for det in kept:
        if any(_contains(other.bbox, det.bbox) for other in result):
            continue
        result.append(det)

    result.sort(key=lambda d: d.confidence, reverse=True)
    return result


# ------------------------------------------------------------
#  Екі өтпелі детекция
# ------------------------------------------------------------

def detect_tiled(bank, frame, min_conf: float) -> list:
    """Кадрды екі рет қарау: толық көрініс + АЛДЫҢҒЫ жарты.

    Неге керек: детектор кадрды 640-қа кішірейтіп қарайды. Телефон
    суреті 1920px болғандықтан, көрерменнің дәл алдындағы ІРІ шұңқыр
    сол кішірейтуде жағылып, шектен төмен түсіп қалады — көзге анық
    көрініп тұрса да табылмайды.

    Сондықтан төменгі 55% бөлек кесіліп, өз алдына қаралады да,
    рамкалар бастапқы координатаға қайтарылып, қабаттасқандары
    біріктіріледі.
    """
    from vision.detectors.base import merge_overlapping

    height, width = frame.shape[:2]
    found = list(bank.process(frame)[0])

    top = int(height * 0.45)
    crop = frame[top:, :]
    if crop.shape[0] > 200:
        for det in bank.process(crop)[0]:
            x1, y1, x2, y2 = det.bbox
            det.bbox = (x1, y1 + top, x2, y2 + top)
            found.append(det)

    found = [d for d in found if d.confidence >= min_conf]
    found = merge_overlapping(found, iou_threshold=0.35)

    # Рамканы ақаудың НАҚТЫ шекарасына дейін тарылту. YOLO бүлінген
    # аймақты кең қоршайды, ал дәлелде ақаудың өзі көрінуі керек.
    from vision.refine import refine_all
    return clean_boxes(refine_all(frame, found), frame.shape)


# ------------------------------------------------------------
#  Сайт та қолданатын бөлік
# ------------------------------------------------------------

_BANK = None
_CFG = None
_GEO = None
_VERIFIER = None


def _setup(min_conf: float = 0.20):
    """Детекторды бір рет қана жүктеу (сұрау сайын емес)."""
    global _BANK, _CFG, _GEO, _VERIFIER
    if _BANK is not None:
        return
    load_env()
    _CFG = Config.load()
    _CFG.road_only = True
    _CFG.confirm_frames = 1
    _CFG.enable_pothole_cv = True
    _CFG.conf_threshold = min(_CFG.conf_threshold, min_conf)
    _BANK = DetectorBank(_CFG)
    _GEO = Geocoder(_CFG)
    try:
        from vision.aiverify import AIVerifier
        _VERIFIER = AIVerifier(_CFG)
    except Exception:                              # noqa: BLE001
        _VERIFIER = None


def detector_bank():
    _setup()
    return _BANK


def load_image_bytes(data: bytes, filename: str):
    """Жүктелген файлды кадрға айналдыру (HEIC те, JPG та)."""
    import io
    import tempfile

    suffix = Path(filename).suffix.lower() or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    try:
        return load_image(temp)
    finally:
        temp.unlink(missing_ok=True)


def build_event_from_image(frame, coords, shot_at, media_dir, bank=None,
                           note: str = "", actor: str = "", min_conf: float = 0.20):
    """Кадрдан толық оқиға жасау. Ақау табылмаса — None."""
    _setup(min_conf)
    bank = bank or _BANK

    detections = detect_tiled(bank, frame, min_conf)
    if not detections:
        return None

    detections.sort(key=lambda d: d.confidence, reverse=True)
    main_det = detections[0]

    lat, lon = coords
    address = _GEO.reverse(lat, lon) or {}
    event_id = new_event_id()
    folder = Path(media_dir) / event_id
    folder.mkdir(parents=True, exist_ok=True)

    cv2.imwrite(str(folder / "photo.jpg"), draw_boxes(frame, detections),
                [int(cv2.IMWRITE_JPEG_QUALITY), 88])

    ai = None
    if _VERIFIER is not None:
        raw_path = folder / "raw.jpg"
        cv2.imwrite(str(raw_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        try:
            verdict = _VERIFIER.verify(
                raw_path, main_det.class_key, float(main_det.confidence),
                address.get("address_text", ""),
                time.strftime("%d.%m.%Y %H:%M", time.localtime(shot_at or time.time())),
            )
            if verdict is not None:
                ai = verdict.as_dict()
        except Exception:                          # noqa: BLE001
            pass
        raw_path.unlink(missing_ok=True)

    document = build_document(
        cfg=_CFG,
        event_id=event_id,
        class_key=main_det.class_key,
        confidence=float(main_det.confidence),
        area_frac=main_det.area_frac(frame.shape),
        fix=GpsFix(lat=lat, lon=lon, ts=shot_at or time.time(), accuracy_m=8.0,
                   source="EXIF (телефон камерасы)"),
        address=address,
        detected_at=shot_at or time.time(),
        detector_name=main_det.detector,
        video_seconds=0.0,
        has_video=False,
        marked_on_road=False,
        extra={"detections": len(detections), "pipeline": "photo-upload",
               "operator_note": note, "uploaded_by": actor},
        ai=ai,
    )
    document["source_type"] = "photo"
    db.insert_document(document, "photo.jpg", None)

    return {
        "event_id": event_id,
        "defect_type": document.get("defect_type_official"),
        "severity": document.get("severity_kk") or document.get("severity"),
        "confidence": round(float(main_det.confidence), 3),
        "detections": len(detections),
        "address_text": document.get("address_text"),
        "lat": lat, "lon": lon,
        "ai_verified": bool(document.get("ai_verified")),
        "ai_note": document.get("ai_note") or "",
    }


# ------------------------------------------------------------
#  Негізгі жұмыс
# ------------------------------------------------------------

def wipe_documents() -> int:
    """Демо оқиғаларын тазалау (медиасымен бірге)."""
    rows = db.list_documents(status="all", limit=5000)
    for row in rows:
        event_id = row["event_id"]
        shutil.rmtree(MEDIA / event_id, ignore_errors=True)
    with db.closing(db.connect()) as connection, connection:
        for table in ("delivery_attempts", "location_history", "status_history", "documents"):
            connection.execute(f"DELETE FROM {table}")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", help="суреттер жатқан қалта")
    parser.add_argument("--wipe", action="store_true", help="ескі оқиғаларды өшіру")
    parser.add_argument("--min-conf", type=float, default=0.25)
    parser.add_argument("--pattern", default="", help="файл атының басы")
    args = parser.parse_args()

    load_env()

    if args.wipe:
        removed = wipe_documents()
        print(f"  Өшірілді: {removed} оқиға\n")

    # Config.load() — МІНДЕТТІ: жай Config() .env файлын оқымайды да,
    # ИИ кілті мен 2ГИС кілті бос қалады. Сонда ИИ-тексеру үнсіз
    # өшіп, мекенжай тек ауданға дейін ғана анықталады.
    cfg = Config.load()
    # road_only ҚОСУЛЫ болуы керек: жобаның өз RoadBoundaryDetector-і
    # жол бетінен тыс қалған рамкаларды сүзеді. Оны өшіргенде рамка
    # шөпке де, қиыршық тасты жиекке де түсіп кететін.
    # Шекара табылмаса, сүзгі өзі жұмыс істемейді — сондықтан қауіпсіз.
    cfg.road_only = True
    cfg.confirm_frames = 1         # бір суретте уақыттық растау болмайды
    # Екінші детектор — МІНДЕТТІ. YOLO (RDD2022) дашкам көрінісіне
    # үйретілген: көрерменнің дәл алдындағы ІРІ шұңқырды танымайды.
    # Ал pothole_cv шұңқырды физикасы бойынша табады (ойық → қараңғы),
    # үйретуді қажет етпейді. Екеуі қатар жүргенде кадр толық қаралады.
    cfg.enable_pothole_cv = True
    # imgsz-ды ӨЗГЕРТУГЕ БОЛМАЙДЫ: OpenVINO-ға экспортталған модельдің
    # кірісі 640-қа бекітілген, басқа мән берілсе ол ештеңе таппай қалады.
    # Оның орнына кадрдың өзін ірілеу сақтаймыз (LONG_SIDE).
    cfg.conf_threshold = min(cfg.conf_threshold, args.min_conf)
    bank = DetectorBank(cfg)
    geocoder = Geocoder(cfg)

    verifier = None
    try:
        from vision.aiverify import AIVerifier
        verifier = AIVerifier(cfg)
    except Exception as exc:                       # noqa: BLE001
        print(f"  ИИ-тексеруші қосылмады: {exc}")

    folder = Path(args.folder)
    pattern = args.pattern
    files = sorted([p for p in folder.iterdir()
                    if p.suffix.lower() in (".heic", ".jpg", ".jpeg", ".png")
                    and (not pattern or p.name.startswith(pattern))])
    print(f"  Суреттер: {len(files)}\n")

    created = skipped = 0
    for path in files:
        try:
            frame, coords, shot_at = load_image(path)
        except Exception as exc:                   # noqa: BLE001
            print(f"  {path.name:16} оқылмады: {exc}")
            continue

        if not coords:
            print(f"  {path.name:16} GPS жоқ — өткізілді")
            skipped += 1
            continue

        detections = detect_tiled(bank, frame, args.min_conf)
        if not detections:
            print(f"  {path.name:16} ақау табылмады")
            skipped += 1
            continue

        # Ең сенімдісі — құжаттың негізгі ақауы
        detections.sort(key=lambda d: d.confidence, reverse=True)
        main_det = detections[0]

        lat, lon = coords
        address = geocoder.reverse(lat, lon) or {}
        event_id = new_event_id()
        folder_out = MEDIA / event_id
        folder_out.mkdir(parents=True, exist_ok=True)

        marked = draw_boxes(frame, detections)
        cv2.imwrite(str(folder_out / "photo.jpg"), marked,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 88])

        # ИИ-ге ӨҢДЕЛМЕГЕН кадр беріледі: рамка салынған суретте модель
        # өз рамкасын «ақау» деп қайта тауып, өзін-өзі растап жіберуі мүмкін
        raw_path = folder_out / "raw.jpg"
        cv2.imwrite(str(raw_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])

        ai = None
        if verifier is not None:
            try:
                verdict = verifier.verify(
                    raw_path,
                    main_det.class_key,
                    float(main_det.confidence),
                    address.get("address_text", ""),
                    time.strftime("%d.%m.%Y %H:%M", time.localtime(shot_at or time.time())),
                )
                # Вердиктің ӨЗ пішімін береміз (as_dict) — құжат генераторы
                # дәл соны күтеді: сипаттама, өлшем, қауіп, ұсынылған шара.
                # Қолмен жинақтаған сөздік жарамайды: өріс аттары басқа
                # (is_real, reason), сонда бәрі «расталмады» болып шығады.
                if verdict is not None:
                    ai = verdict.as_dict()
            except Exception as exc:               # noqa: BLE001
                print(f"      ИИ тексере алмады: {str(exc)[:60]}")

        document = build_document(
            cfg=cfg,
            event_id=event_id,
            class_key=main_det.class_key,
            confidence=float(main_det.confidence),
            area_frac=main_det.area_frac(frame.shape),
            fix=GpsFix(lat=lat, lon=lon, ts=shot_at or time.time(), accuracy_m=8.0,
                      source="EXIF (телефон камерасы)"),
            address=address,
            detected_at=shot_at or time.time(),
            detector_name=main_det.detector,
            video_seconds=0.0,
            has_video=False,
            marked_on_road=False,
            extra={"detections": len(detections), "source_file": path.name,
                   "pipeline": "photo-ingest"},
            ai=ai,
        )
        # Детектор тапқан оқиға — АВТОМАТТЫ. Қолмен енгізілгеннен
        # ажыратылып тұруы керек: жауапкершілік те, сенім де басқа.
        document["source_type"] = "photo"
        db.insert_document(document, "photo.jpg", None)

        raw_path.unlink(missing_ok=True)
        created += 1
        mark = ("тексерілмеді" if ai is None
                else ("расталды" if ai.get("is_real") else "расталмады"))
        print(f"  {path.name:16} {len(detections)} ақау · "
              f"{categories.get(main_det.class_key).kk[:34]:36} "
              f"{round(main_det.confidence * 100):>3}% · ИИ: {mark}")
        print(f"  {'':16} {address.get('address_text', '—')}")

    print(f"\n  Қорытынды: {created} оқиға тіркелді, {skipped} сурет өткізілді")


if __name__ == "__main__":
    main()
