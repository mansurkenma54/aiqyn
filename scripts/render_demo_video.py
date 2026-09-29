"""Көрсетілімге арналған ДЕМО ВИДЕО — дикторымен, 30 секундқа сыятын.

Видео сегіз бөліктен тұрады, әр бөліктің ұзақтығы ДИКТОРДЫҢ сол жолының
ұзындығына тең (`reports/audio/narration.json`), сондықтан сурет пен дауыс
дәл сәйкес келеді.

    1  ТАНЫСТЫРУ    өз үйретілген модель
    2  СКАНЕРЛЕУ    әр кадр: толық + екі аймақ
    3  ДЕТЕКЦИЯ     шикі сигналдар
    4  РАСТАУ       көлеңке мен дақ сүзіледі, ақауға ID беріледі
    5  БІРІКТІРУ    сол ақау қайта табылды -> бір жазба
    6  НЕЙРОЖЕЛІ    әр үміткер 3 рет тексеріледі
    7  ӨЛШЕМ ШЕГІ   ұсағы тіркелмейді
    8  ҚОРЫТЫНДЫ    ресми құжаттар

НЕГЕ БҰЛ АДАЛ ЖАЗБА
-------------------
Рамкаларды да, дауыстарды да скрипт ойлап таппайды: детекция `vision/`
ішіндегі ДӘЛ СОЛ модельмен жүреді, ал нейрожелінің шешімдері нақты
жүргізу жазған `evidence/*.verdict.json` файлдарынан оқылады.

ҚОЛДАНУ
-------
    python scripts/make_narration.py           # диктор + эффектілер (бір рет)
    python scripts/render_demo_video.py --video ../докмументы/видео/test.MOV \
        --gps-track demo_track.jsonl
    python scripts/mux_demo_audio.py           # дыбысты видеоға қосу
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.config import Config                       # noqa: E402
from vision.detectors import DetectorBank              # noqa: E402
from vision.overlay import TextLayer, rounded_rect     # noqa: E402

# ---------- түстер (BGR) ----------
BG = (16, 15, 15)
PANEL = (27, 25, 25)
CARD = (38, 35, 35)
LINE = (52, 50, 50)
WHITE = (255, 255, 255)
GREY = (170, 170, 170)
DIM = (112, 112, 112)
RED = (48, 59, 255)
AMBER = (0, 170, 255)
GREEN = (110, 210, 90)
CYAN = (255, 205, 60)

LABELS = {
    "pothole": "ШҰҢҚЫР",
    "crack_alligator": "ТОРЛЫ ЖАРЫҚ",
    "crack_longitudinal": "БОЙЛЫҚ ЖАРЫҚ",
    "crack_transverse": "КӨЛДЕНЕҢ ЖАРЫҚ",
}
CLASS_COLOR = {
    "pothole": RED,
    "crack_alligator": AMBER,
    "crack_longitudinal": CYAN,
    "crack_transverse": CYAN,
}

STAGES = ["МОДЕЛЬ", "РАСТАУ", "НЕЙРОЖЕЛІ", "ӨЛШЕМ", "ҚҰЖАТ"]

TRACK_GAP = 2.2
W, H = 1920, 860


# ==================================================================
#  Талдау
# ==================================================================

def center_gap(a, b) -> float:
    ax, ay = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    bx, by = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    scale = max(12.0, max(a[2] - a[0], b[2] - b[0]))
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 / scale


def analyse(cfg: Config, video: Path):
    """Әр кадрды нақты детектормен талдау."""
    bank = DetectorBank(cfg)
    for detector in bank.detectors:
        if hasattr(detector, "warmup"):
            detector.warmup()

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit("Видео ашылмады: %s" % video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0

    frames, images, took_all = [], [], []
    index = 0
    while True:
        ok, image = cap.read()
        if not ok:
            break
        index += 1
        started = time.perf_counter()
        detections, _lane, context = bank.process(image)
        took_all.append((time.perf_counter() - started) * 1000)
        images.append(image)
        frames.append({
            "index": index,
            "detections": [{"cls": d.class_key, "conf": float(d.confidence),
                            "bbox": [int(v) for v in d.bbox]} for d in detections],
            "confirmed": [{"cls": d.class_key, "conf": float(d.confidence),
                           "bbox": [int(v) for v in d.bbox]}
                          for d in context.get("confirmed", [])],
        })
        if index % 25 == 0:
            print("     талдау: %d кадр…" % index)
    cap.release()

    meta = {"fps": float(fps), "total": index,
            "avg_ms": float(np.mean(took_all)) if took_all else 0.0,
            "hz": 1000.0 / float(np.mean(took_all)) if took_all else 0.0,
            "raw": sum(len(f["detections"]) for f in frames)}
    return frames, images, meta


def build_timeline(frames):
    """Кадрлардың үстіне ID мен «біріктіру» оқиғаларын қою.

    Растаушы қабат бір нысанды бір-ақ рет қайтарады, ал ол нысан кейін
    қайта көрінгенде — сол ID-мен жалғасады. Осы жерде сол сәтті бөлек
    белгілеп аламыз: видеода «бұл сол ақау» деп көрсету үшін.
    """
    live, events, merges = [], [], []
    for record in frames:
        record["boxes"] = []
        record["candidates"] = []
        record["merge"] = None

        for det in record["confirmed"]:
            item = {"code": "A%d" % (len(events) + 1), "cls": det["cls"],
                    "conf": det["conf"], "bbox": det["bbox"],
                    "frame": record["index"], "last": record["index"]}
            events.append(item)
            live.append(item)

        for det in record["detections"]:
            best, best_gap = None, TRACK_GAP
            for known in live:
                if known["cls"] != det["cls"]:
                    continue
                gap = center_gap(known["bbox"], det["bbox"])
                if gap <= best_gap:
                    best, best_gap = known, gap
            if best is None:
                record["candidates"].append(det)
                continue

            # Ұзақ үзілістен кейін қайта табылды — «сол ақау» сәті
            if record["index"] - best["last"] >= 8 and record["merge"] is None:
                record["merge"] = {"code": best["code"], "cls": best["cls"],
                                   "gap": record["index"] - best["last"],
                                   "bbox": det["bbox"]}
                merges.append({"code": best["code"], "frame": record["index"]})
            best["bbox"] = det["bbox"]
            best["last"] = record["index"]
            record["boxes"].append({"code": best["code"], "cls": det["cls"],
                                    "conf": det["conf"], "bbox": det["bbox"]})
        live[:] = [t for t in live if record["index"] - t["last"] <= 22]
    return events, merges


# ==================================================================
#  Ортақ сурет бөліктері
# ==================================================================

def crop_defect(photo, bbox, frame_shape, ratio: float = 1.7):
    """Ақаудың айналасын кесіп алу — торда жақын план керек.

    Дәлел фотосы бүкіл кадрды көрсетеді, ал 2x2 торда ол ұсақ болып
    кетеді. Ақаудың төңірегін ғана кессек, жюри нақты нені тапқанын
    көреді.
    """
    if photo is None:
        return None
    ph, pw = photo.shape[:2]
    if not bbox or not frame_shape or len(frame_shape) < 2:
        return photo
    # bbox ТҮПНҰСҚА кадрдың координатасында — фотоға масштабтаймыз
    sx = pw / float(frame_shape[1])
    sy = ph / float(frame_shape[0])
    x1, y1, x2, y2 = [int(v) for v in bbox]
    x1, x2 = int(x1 * sx), int(x2 * sx)
    y1, y2 = int(y1 * sy), int(y2 * sy)

    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    half_w = max(150, int((x2 - x1) * 1.9))
    half_h = int(half_w / ratio)
    if half_h < (y2 - y1) * 1.4:
        half_h = int((y2 - y1) * 1.4)
        half_w = int(half_h * ratio)

    left = max(0, min(pw - 2 * half_w, cx - half_w))
    top_ = max(0, min(ph - 2 * half_h, cy - half_h))
    right = min(pw, left + 2 * half_w)
    bottom = min(ph, top_ + 2 * half_h)
    crop = photo[top_:bottom, left:right]
    return crop if crop.size else photo


def fit_into(image, box_w, box_h):
    """Суретті қорапқа пропорциясын бұзбай сыйдыру."""
    ih, iw = image.shape[:2]
    scale = min(box_w / float(iw), box_h / float(ih))
    w, h = max(1, int(iw * scale)), max(1, int(ih * scale))
    return cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)


def ease(x: float) -> float:
    """Жұмсақ басталып, жұмсақ аяқталатын қисық (smoothstep).

    Сызықтық анимация «машина сияқты» көрінеді; осы қисықпен әр қозғалыс
    табиғи басталып, табиғи тоқтайды.
    """
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def fade_in(canvas, progress: float, portion: float = 0.12):
    """Бөлімнің басында қараңғыдан ашылу."""
    if progress >= portion:
        return canvas
    k = ease(progress / portion)
    return (canvas.astype(np.float32) * k).astype(np.uint8)


def base_canvas():
    return np.full((H, W, 3), BG, dtype=np.uint8)


def dashed_rect(canvas, pt1, pt2, color, thickness=1, dash=8):
    (x1, y1), (x2, y2) = pt1, pt2
    for x in range(x1, x2, dash * 2):
        cv2.line(canvas, (x, y1), (min(x + dash, x2), y1), color, thickness)
        cv2.line(canvas, (x, y2), (min(x + dash, x2), y2), color, thickness)
    for y in range(y1, y2, dash * 2):
        cv2.line(canvas, (x1, y), (x1, min(y + dash, y2)), color, thickness)
        cv2.line(canvas, (x2, y), (x2, min(y + dash, y2)), color, thickness)


def draw_header(canvas, text: TextLayer, active: int, subtitle: str = ""):
    """Жоғарғы жолақ: жүйенің аты + тізбектегі қай сатыда тұрғаны."""
    pad = 40
    text.add((pad, 28), "AIQYN", size=30, color=WHITE, bold=True)
    text.add((pad + 112, 37), "VISION", size=22, color=CYAN, bold=True)
    if subtitle:
        text.add((pad + 222, 40), subtitle, size=16, color=DIM)

    box_w, gap = 172, 12
    total = len(STAGES) * box_w + (len(STAGES) - 1) * gap
    x = W - pad - total
    for index, name in enumerate(STAGES):
        done, now = index < active, index == active
        color = CYAN if now else (GREEN if done else CARD)
        fg = (18, 17, 17) if now else (WHITE if done else DIM)
        rounded_rect(canvas, (x, 22), (x + box_w, 58), color, radius=8)
        text.add((x + box_w // 2, 31), "%d. %s" % (index + 1, name), size=15,
                 color=fg, bold=True, anchor="ma")
        x += box_w + gap
    cv2.line(canvas, (pad, 76), (W - pad, 76), LINE, 1)


def draw_rows(canvas, text: TextLayer, x, y, w, rows, title):
    cv2.line(canvas, (x, y), (x + w, y), LINE, 1)
    text.add((x, y + 10), title, size=13, color=DIM, bold=True)
    y += 36
    for label, value, color in rows:
        text.add((x, y), label, size=16, color=GREY)
        vw = text.measure(str(value), 17, True)[0]
        text.add((x + w - vw, y - 1), str(value), size=17, color=color, bold=True)
        y += 29
    return y + 10


# ==================================================================
#  1. Таныстыру
# ==================================================================

def frame_intro(progress: float, meta):
    canvas = base_canvas()
    text = TextLayer()
    cy = H // 2 - 168

    text.add((W // 2, cy), "AIQYN VISION", size=76, color=WHITE, bold=True, anchor="ma")
    cy += 100
    text.add((W // 2, cy), "жол ақауын автоматты анықтау", size=28, color=GREY, anchor="ma")
    cy += 80

    box_w = 960
    x = (W - box_w) // 2
    rounded_rect(canvas, (x, cy), (x + box_w, cy + 112), PANEL, radius=14)
    cv2.rectangle(canvas, (x, cy), (x + 6, cy + 112), CYAN, -1)
    text.add((x + 34, cy + 22), "ӨЗ ҮЙРЕТІЛГЕН МОДЕЛІМІЗ", size=15, color=CYAN, bold=True)
    text.add((x + 34, cy + 52), "YOLOv8 · RDD2022 · 47 420 сурет · 4 класс",
             size=28, color=WHITE, bold=True)
    cy += 158

    k = ease(min(1.0, progress * 1.8))
    stats = (("%.0f кадр/сек" % (meta["hz"] * k), "талдау жылдамдығы"),
             ("%.0f мс" % (meta["avg_ms"] * k), "бір кадрға"),
             ("Intel GPU", "құрылғы"))
    cell = 300
    sx = (W - cell * len(stats)) // 2
    for value, label in stats:
        text.add((sx + cell // 2, cy), value, size=32, color=WHITE, bold=True, anchor="ma")
        text.add((sx + cell // 2, cy + 46), label, size=15, color=DIM, anchor="ma")
        sx += cell

    cv2.line(canvas, (x, H - 96), (x + box_w, H - 96), LINE, 3)
    bar = int(box_w * ease(min(1.0, progress * 1.15)))
    if bar:
        cv2.line(canvas, (x, H - 96), (x + bar, H - 96), CYAN, 3)
    return text.flush(canvas)


# ==================================================================
#  2-4. Тірі сканерлеу
# ==================================================================

def frame_live(image, record, state, meta, cfg, stage, phase):
    canvas = base_canvas()
    text = TextLayer()
    pad = 40
    draw_header(canvas, text, stage, "тірі талдау")

    top = 104
    panel_w = 446
    view_w = W - panel_w - pad * 3
    scale = view_w / image.shape[1]
    view_h = int(image.shape[0] * scale)
    view = cv2.resize(image, (view_w, view_h), interpolation=cv2.INTER_LINEAR)
    canvas[top:top + view_h, pad:pad + view_w] = view

    ih, iw = image.shape[:2]
    tile_top = int(ih * cfg.tile_top_frac)
    tile_w = int(iw * 0.58)
    for index in range(2):
        left = int(index * iw * 0.42)
        right = min(iw, left + tile_w)
        dashed_rect(canvas,
                    (pad + int(left * scale), top + int(tile_top * scale)),
                    (pad + int(right * scale) - 2, top + view_h - 2),
                    (84, 82, 82), 1, 9)
    text.add((pad + 12, top + int(tile_top * scale) + 8), "ТАЛДАУ АЙМАҚТАРЫ",
             size=13, color=(150, 148, 148), bold=True)

    if phase is not None:
        sy = top + int(view_h * phase)
        overlay = canvas.copy()
        cv2.line(overlay, (pad, sy), (pad + view_w, sy), CYAN, 3)
        cv2.addWeighted(overlay, 0.45, canvas, 0.55, 0, canvas)

    for det in record["candidates"]:
        x1, y1, x2, y2 = [int(v * scale) for v in det["bbox"]]
        x1 += pad; x2 += pad; y1 += top; y2 += top
        dashed_rect(canvas, (x1, y1), (x2, y2), (195, 195, 195), 1, 7)
        label = "ТЕКСЕРІЛУДЕ %.0f%%" % (det["conf"] * 100)
        tw = text.measure(label, 15, True)[0]
        ly = max(top, y1 - 24)
        rounded_rect(canvas, (x1, ly), (x1 + tw + 16, ly + 22), (58, 56, 56), radius=4)
        text.add((x1 + 8, ly + 3), label, size=15, color=(215, 215, 215), bold=True)

    for det in record["boxes"]:
        x1, y1, x2, y2 = [int(v * scale) for v in det["bbox"]]
        x1 += pad; x2 += pad; y1 += top; y2 += top
        color = CLASS_COLOR.get(det["cls"], RED)
        overlay = canvas.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
        cv2.addWeighted(overlay, 0.16, canvas, 0.84, 0, canvas)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 3)
        arm = max(10, min(26, (x2 - x1) // 4))
        for cx, sx in ((x1, 1), (x2, -1)):
            for cy2, sy2 in ((y1, 1), (y2, -1)):
                cv2.line(canvas, (cx, cy2), (cx + sx * arm, cy2), WHITE, 2)
                cv2.line(canvas, (cx, cy2), (cx, cy2 + sy2 * arm), WHITE, 2)
        label = "%s · %s · %.0f%%" % (det["code"], LABELS.get(det["cls"], det["cls"]),
                                      det["conf"] * 100)
        tw = text.measure(label, 18, True)[0]
        ly = max(top, y1 - 30)
        rounded_rect(canvas, (x1, ly), (x1 + tw + 20, ly + 27), color, radius=5)
        text.add((x1 + 10, ly + 5), label, size=18, color=WHITE, bold=True)

    cv2.rectangle(canvas, (pad - 1, top - 1), (pad + view_w, top + view_h), (62, 60, 60), 1)
    rounded_rect(canvas, (pad, top), (pad + 196, top + 32), (20, 18, 18), radius=0)
    cv2.circle(canvas, (pad + 17, top + 16), 6, RED, -1)
    text.add((pad + 31, top + 7), "ТІРІ КӨРІНІС", size=16, color=WHITE, bold=True)
    stamp = "%05.2f сек" % (record["index"] / meta["fps"])
    sw = text.measure(stamp, 16, True)[0]
    rounded_rect(canvas, (pad + view_w - sw - 22, top), (pad + view_w, top + 32),
                 (20, 18, 18), radius=0)
    text.add((pad + view_w - sw - 11, top + 7), stamp, size=16, color=GREY, bold=True)

    px = pad * 2 + view_w
    rounded_rect(canvas, (px, top), (px + panel_w, top + view_h), PANEL, radius=12)
    ix, iw2 = px + 24, panel_w - 48
    y = top + 26

    y = draw_rows(canvas, text, ix, y, iw2, (
        ("Кадр", "%d / %d" % (record["index"], meta["total"]), WHITE),
        ("Талданды", "%d  (100%%)" % record["index"], GREEN),
        ("Жылдамдық", "%.0f кадр/сек" % meta["hz"], WHITE),
        ("Шикі детекция", str(state["raw"]), AMBER),
    ), "МОДЕЛЬ")

    y = draw_rows(canvas, text, ix, y, iw2, (
        ("GPS", "%.5f, %.5f" % (state["lat"], state["lon"]), GREEN),
        ("Көзі", "GPS трек", GREEN),
        ("Сайт", "онлайн", GREEN),
    ), "ОРНАЛАСУЫ")

    cv2.line(canvas, (ix, y), (ix + iw2, y), LINE, 1)
    text.add((ix, y + 10), "РАСТАЛҒАН АҚАУЛАР", size=13, color=DIM, bold=True)
    cw = text.measure(str(len(state["found"])), 13, True)[0]
    text.add((ix + iw2 - cw, y + 10), str(len(state["found"])), size=13,
             color=WHITE, bold=True)
    y += 34
    for item in state["found"][-4:]:
        color = CLASS_COLOR.get(item["cls"], RED)
        rounded_rect(canvas, (ix, y), (ix + iw2, y + 48), CARD, radius=7)
        cv2.rectangle(canvas, (ix, y), (ix + 5, y + 48), color, -1)
        text.add((ix + 18, y + 7), item["code"], size=17, color=color, bold=True)
        text.add((ix + 68, y + 7), LABELS.get(item["cls"], ""), size=17,
                 color=WHITE, bold=True)
        text.add((ix + 68, y + 28), "%.0f%%  ·  %.1f сек"
                 % (item["conf"] * 100, item["at"]), size=14, color=GREY)
        y += 56

    return text.flush(canvas)


# ==================================================================
#  5. Біріктіру
# ==================================================================

def frame_merge(image, merge, meta, reveal: float):
    canvas = base_canvas()
    text = TextLayer()
    pad = 40
    draw_header(canvas, text, 1, "бір нысан — бір жазба")

    top = 100
    view_w = int(W * 0.63)
    scale = view_w / image.shape[1]
    view_h = int(image.shape[0] * scale)
    pad = (W - view_w) // 2
    view = cv2.resize(image, (view_w, view_h), interpolation=cv2.INTER_LINEAR)
    canvas[top:top + view_h, pad:pad + view_w] = view

    x1, y1, x2, y2 = [int(v * scale) for v in merge["bbox"]]
    x1 += pad; x2 += pad; y1 += top; y2 += top
    color = CLASS_COLOR.get(merge["cls"], RED)

    grow = int(10 + 16 * abs(np.sin(reveal * np.pi * 2)))
    cv2.rectangle(canvas, (x1 - grow, y1 - grow), (x2 + grow, y2 + grow), color, 2)
    overlay = canvas.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
    cv2.addWeighted(overlay, 0.20, canvas, 0.80, 0, canvas)
    cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 4)

    label = "%s — СОЛ АҚАУ" % merge["code"]
    tw = text.measure(label, 22, True)[0]
    ly = max(top, y1 - 36)
    rounded_rect(canvas, (x1, ly), (x1 + tw + 24, ly + 32), color, radius=6)
    text.add((x1 + 12, ly + 5), label, size=22, color=WHITE, bold=True)

    if reveal > 0.22:
        side = 40
        by = top + view_h + 22
        box_h = H - by - 34
        rounded_rect(canvas, (side, by), (W - side, by + box_h), PANEL, radius=12)
        cv2.rectangle(canvas, (side, by), (side + 6, by + box_h), GREEN, -1)

        badge = "2 детекция  →  1 ақау"
        bw = text.measure(badge, 22, True)[0]
        badge_x = W - side - bw - 56
        rounded_rect(canvas, (badge_x, by + box_h // 2 - 22),
                     (W - side - 24, by + box_h // 2 + 22), GREEN, radius=9)
        text.add((badge_x + 16, by + box_h // 2 - 13), badge, size=22,
                 color=(16, 40, 16), bold=True)

        text.add((side + 30, by + 16), "ҚАЙТА ТАБЫЛДЫ — БІРІКТІРІЛДІ", size=15,
                 color=GREEN, bold=True)
        text.add((side + 30, by + 44),
                 "%s нысаны %.1f секундтан кейін қайта көрінді."
                 % (merge["code"], merge["gap"] / meta["fps"]),
                 size=21, color=WHITE)
        text.add((side + 30, by + 74),
                 "Жүйе оны кадрдан кадрға бақылағандықтан ЖАҢА ақау деп санамайды: "
                 "бір жазба, бір координата, бір құжат.",
                 size=19, color=GREY)

    return text.flush(canvas)


# ==================================================================
#  6. Нейрожелі (2x2 тор)
# ==================================================================

def frame_ai(verdicts, photos, votes_shown: int):
    canvas = base_canvas()
    text = TextLayer()
    pad = 40
    draw_header(canvas, text, 2, "екінші саты")

    text.add((W // 2, 96), "ӘР ҮМІТКЕР ҮШ РЕТ ТӘУЕЛСІЗ ТЕКСЕРІЛЕДІ", size=26,
             color=WHITE, bold=True, anchor="ma")

    top = 148
    cols = 2
    cell_w = (W - pad * (cols + 1)) // cols
    cell_h = (H - top - pad) // 2 - 10

    for index, item in enumerate(verdicts[:4]):
        cx = pad + (index % cols) * (cell_w + pad)
        cy = top + (index // cols) * (cell_h + 16)
        rounded_rect(canvas, (cx, cy), (cx + cell_w, cy + cell_h), PANEL, radius=12)

        photo = crop_defect(photos.get(item["event_id"]), item.get("bbox"),
                            item.get("frame_shape"))
        img_h = 0
        if photo is not None:
            view = fit_into(photo, cell_w - 28, cell_h - 118)
            img_h, img_w = view.shape[:2]
            ox = cx + (cell_w - img_w) // 2
            canvas[cy + 12:cy + 12 + img_h, ox:ox + img_w] = view
            cv2.rectangle(canvas, (ox - 1, cy + 11), (ox + img_w, cy + 12 + img_h),
                          (62, 60, 60), 1)

        by = cy + 24 + img_h
        text.add((cx + 16, by), "A%d" % (index + 1), size=19, color=CYAN, bold=True)
        text.add((cx + 62, by), LABELS.get(item.get("class_key"), ""), size=19,
                 color=WHITE, bold=True)
        conf = "%.0f%%" % ((item.get("yolo_confidence") or 0) * 100)
        cw = text.measure(conf, 18, True)[0]
        text.add((cx + cell_w - 16 - cw, by), conf, size=18, color=GREY, bold=True)

        votes = item.get("ai_votes") or []
        dx, dy = cx + 16, by + 32
        for vi, vote in enumerate(votes[:3]):
            shown = vi < votes_shown
            real = bool(vote.get("is_real"))
            color = (GREEN if real else RED) if shown else CARD
            rounded_rect(canvas, (dx, dy), (dx + 106, dy + 34), color, radius=6)
            text.add((dx + 53, dy + 7), ("АҚИҚАТ" if real else "ЖАЛҒАН") if shown else "…",
                     size=15, color=WHITE if shown else DIM, bold=True, anchor="ma")
            dx += 116

        if votes_shown >= 3 and votes:
            real_n = sum(1 for v in votes if v.get("is_real"))
            passed = real_n > len(votes) - real_n
            stamp = "%d / %d" % (real_n, len(votes))
            sw = text.measure(stamp, 22, True)[0]
            rounded_rect(canvas, (cx + cell_w - 32 - sw, dy), (cx + cell_w - 16, dy + 34),
                         GREEN if passed else RED, radius=6)
            text.add((cx + cell_w - 24 - sw, dy + 5), stamp, size=22,
                     color=WHITE, bold=True)

    return text.flush(canvas)


# ==================================================================
#  7. Өлшем шегі
# ==================================================================

def frame_size(verdicts, reveal: float):
    canvas = base_canvas()
    text = TextLayer()
    pad = 40
    draw_header(canvas, text, 3, "тіркеуге тұрарлық па?")

    text.add((W // 2, 108), "ӨЛШЕМ ШЕГІ", size=36, color=WHITE, bold=True, anchor="ma")
    text.add((W // 2, 160), "ұсақ қажалуға ресми жөндеу тапсырысы ашылмайды",
             size=21, color=GREY, anchor="ma")

    limit = (verdicts[0].get("min_area_frac") or 0.015) * 100
    top = 232
    row_h = 104
    chart_x = pad + 320
    chart_w = W - chart_x - pad - 260
    max_area = max((v.get("area_frac") or 0) * 100 for v in verdicts) * 1.18

    for index, item in enumerate(verdicts[:4]):
        y = top + index * row_h
        area = (item.get("area_frac") or 0) * 100
        ok = area >= limit
        color = GREEN if ok else RED

        text.add((pad + 20, y + 16), "A%d" % (index + 1), size=24, color=CYAN, bold=True)
        text.add((pad + 82, y + 19), LABELS.get(item.get("class_key"), ""), size=21,
                 color=WHITE, bold=True)

        bar = int(chart_w * min(1.0, area / max_area) * ease(min(1.0, reveal * 2.6)))
        cv2.rectangle(canvas, (chart_x, y + 10), (chart_x + chart_w, y + 62), CARD, -1)
        if bar > 0:
            cv2.rectangle(canvas, (chart_x, y + 10), (chart_x + bar, y + 62), color, -1)
        text.add((chart_x + 16, y + 22), "%.2f%%" % area, size=22,
                 color=WHITE if bar > 110 else GREY, bold=True)

        if reveal > 0.42:
            mark = "ТІРКЕЛДІ" if ok else "ҰСАҚ"
            mw = text.measure(mark, 19, True)[0]
            rounded_rect(canvas, (W - pad - mw - 36, y + 14), (W - pad - 10, y + 58),
                         color, radius=7)
            text.add((W - pad - mw - 23, y + 23), mark, size=19, color=WHITE, bold=True)

    lx = chart_x + int(chart_w * min(1.0, limit / max_area))
    cv2.line(canvas, (lx, top - 8), (lx, top + row_h * 4 - 40), WHITE, 2)
    text.add((lx + 12, top - 40), "тіркеу шегі  %.2f%%" % limit, size=19,
             color=WHITE, bold=True)

    return text.flush(canvas)


# ==================================================================
#  8. Базаға тіркеу
# ==================================================================

def frame_save(registered, photos, documents, reveal: float):
    """Ақаудың дерекқорға жазылуы — жол-жолымен толатын жазба."""
    canvas = base_canvas()
    text = TextLayer()
    pad = 40
    draw_header(canvas, text, 4, "дерекқорға жазылуда")

    text.add((W // 2, 96), "БАЗАҒА ТІРКЕЛУДЕ", size=34, color=WHITE,
             bold=True, anchor="ma")

    top = 152
    count = max(1, len(registered))
    cell_w = (W - pad * (count + 1)) // count

    for index, item in enumerate(registered):
        cx = pad + index * (cell_w + pad)
        doc = documents.get(item["event_id"], {})
        rounded_rect(canvas, (cx, top), (cx + cell_w, H - pad), PANEL, radius=14)

        # жақын план
        photo = crop_defect(photos.get(item["event_id"]), item.get("bbox"),
                            item.get("frame_shape"), ratio=2.6)
        img_h = 0
        if photo is not None:
            view = fit_into(photo, cell_w - 32, 168)
            img_h, img_w = view.shape[:2]
            ox = cx + (cell_w - img_w) // 2
            canvas[top + 16:top + 16 + img_h, ox:ox + img_w] = view
            cv2.rectangle(canvas, (ox - 1, top + 15), (ox + img_w, top + 16 + img_h),
                          (62, 60, 60), 1)

        rows = (
            ("тіркеу нөмірі", "AIQYN-…-%s" % str(item["event_id"])[-6:]),
            ("координата", "%.5f, %.5f" % (doc.get("lat") or 0, doc.get("lon") or 0)),
            ("мекенжай", str(doc.get("address_text") or "—")[:34]),
            ("санат", str(doc.get("defect_type_official") or "—")[:34]),
            ("ауырлығы", str(doc.get("severity_kk") or "—")),
            ("фото + видео дәлел", "%.0f сек" % (doc.get("video_seconds") or 0)),
            ("жауапты орган", "жол басқармасы"),
            ("жөндеу мерзімі", "%d күн" % (doc.get("ai_urgency_days") or 0)),
        )

        ry = top + 32 + img_h
        # әр жол кезекпен шығады — «жазылып жатыр» әсері
        shown = ease(reveal) * (len(rows) + 1.4)
        for ri, (key, value) in enumerate(rows):
            if ri > shown:
                break
            alpha = max(0.0, min(1.0, shown - ri))
            grey = tuple(int(c * alpha) for c in GREY)
            white = tuple(int(c * alpha) for c in WHITE)
            text.add((cx + 24, ry), key, size=15, color=grey)
            vw = text.measure(value, 16, True)[0]
            text.add((cx + cell_w - 24 - vw, ry - 1), value, size=16,
                     color=white, bold=True)
            if alpha > 0.6:
                cv2.line(canvas, (cx + 24, ry + 24), (cx + cell_w - 24, ry + 24),
                         (44, 42, 42), 1)
            ry += 34

        if shown >= len(rows) + 0.8:
            mark = "ДЕРЕКҚОРҒА ЖАЗЫЛДЫ"
            mw = text.measure(mark, 19, True)[0]
            by = H - pad - 56
            rounded_rect(canvas, (cx + (cell_w - mw - 60) // 2, by),
                         (cx + (cell_w + mw + 60) // 2, by + 42), GREEN, radius=9)
            text.add((cx + cell_w // 2, by + 9), mark, size=19,
                     color=(16, 40, 16), bold=True, anchor="ma")

    return text.flush(canvas)


# ==================================================================
#  9. Қорытынды
# ==================================================================

def frame_final(registered, photos, meta, candidates, reveal: float):
    canvas = base_canvas()
    text = TextLayer()
    pad = 40
    draw_header(canvas, text, 4, "жауапты органға дайын")

    text.add((W // 2, 100), "ЕКІ САТЫДАН ДА ӨТКЕН АҚАУЛАР", size=36,
             color=WHITE, bold=True, anchor="ma")

    funnel = (("%d" % meta["total"], "кадр"), ("%d" % meta["raw"], "детекция"),
              ("%d" % candidates, "үміткер"), ("%d" % len(registered), "ресми құжат"))
    fx = (W - 300 * len(funnel)) // 2
    for index, (value, label) in enumerate(funnel):
        color = GREEN if index == len(funnel) - 1 else WHITE
        text.add((fx + 150, 154), value, size=40, color=color, bold=True, anchor="ma")
        text.add((fx + 150, 206), label, size=17, color=DIM, anchor="ma")
        if index < len(funnel) - 1:
            text.add((fx + 300, 160), "→", size=30, color=DIM, anchor="ma")
        fx += 300

    top = 244
    count = max(1, len(registered))
    cell_w = (W - pad * (count + 1)) // count
    for index, item in enumerate(registered):
        cx = pad + index * (cell_w + pad)
        photo = photos.get(item["event_id"])
        img_h = 0
        if photo is not None:
            view = fit_into(photo, cell_w, H - top - 210)
            img_h, img_w = view.shape[:2]
            ox = cx + (cell_w - img_w) // 2
            canvas[top:top + img_h, ox:ox + img_w] = view
            cv2.rectangle(canvas, (ox - 2, top - 2), (ox + img_w + 1, top + img_h + 1),
                          GREEN, 3)

        by = top + img_h + 16
        rounded_rect(canvas, (cx, by), (cx + cell_w, by + 178), PANEL, radius=12)
        text.add((cx + 22, by + 18), "A%d" % (index + 1), size=22, color=CYAN, bold=True)
        text.add((cx + 76, by + 16), LABELS.get(item.get("class_key"), ""), size=26,
                 color=WHITE, bold=True)
        rows = (("модель", "%.0f%%" % ((item.get("yolo_confidence") or 0) * 100)),
                ("нейрожелі", item.get("ai_vote_summary") or "—"),
                ("өлшемі", "кадрдың %.2f%%-ы" % ((item.get("area_frac") or 0) * 100)))
        ry = by + 66
        for key, value in rows:
            text.add((cx + 22, ry), key, size=16, color=GREY)
            vw = text.measure(value, 17, True)[0]
            text.add((cx + cell_w - 22 - vw, ry - 1), value, size=17,
                     color=WHITE, bold=True)
            ry += 30

        if reveal > 0.4:
            mark = "РЕСМИ ҚҰЖАТ АШЫЛДЫ"
            mw = text.measure(mark, 18, True)[0]
            rounded_rect(canvas, (cx + (cell_w - mw - 44) // 2, by + 132),
                         (cx + (cell_w + mw + 44) // 2, by + 170), GREEN, radius=8)
            text.add((cx + cell_w // 2, by + 141), mark, size=18,
                     color=(16, 40, 16), bold=True, anchor="ma")

    return text.flush(canvas)


# ==================================================================

def load_track(path):
    if not path or not path.exists():
        return []
    points = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            item = json.loads(line)
            points.append((float(item["t"]), float(item["lat"]), float(item["lon"])))
    return sorted(points)


def fix_at(track, seconds):
    if not track:
        return 42.3156, 69.5869
    if seconds <= track[0][0]:
        return track[0][1], track[0][2]
    if seconds >= track[-1][0]:
        return track[-1][1], track[-1][2]
    for (t0, a0, o0), (t1, a1, o1) in zip(track, track[1:]):
        if t0 <= seconds <= t1:
            k = 0.0 if t1 == t0 else (seconds - t0) / (t1 - t0)
            return a0 + (a1 - a0) * k, o0 + (o1 - o0) * k
    return track[-1][1], track[-1][2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Демо видео рендерлеу")
    parser.add_argument("--video", required=True)
    parser.add_argument("--gps-track", default="")
    parser.add_argument("--out", default="reports/AIQYN-demo-silent.mp4")
    parser.add_argument("--fps", type=float, default=30.0)
    args = parser.parse_args()

    cfg = Config.load()
    video = Path(args.video)
    if not video.is_absolute():
        video = (Path.cwd() / video).resolve()

    narration_path = ROOT / "reports" / "audio" / "narration.json"
    if not narration_path.exists():
        raise SystemExit("Алдымен: python scripts/make_narration.py")
    narration = json.loads(narration_path.read_text(encoding="utf-8"))
    seg = {item["key"]: item["duration"] for item in narration["lines"]}
    GAP = 0.22

    print("1/4  Видеоны талдау (нақты детектормен)…")
    frames, images, meta = analyse(cfg, video)
    events, merges = build_timeline(frames)
    print("     %d кадр · %d детекция · %d үміткер · %d біріктіру"
          % (meta["total"], meta["raw"], len(events), len(merges)))

    verdicts = []
    for path in sorted((ROOT / "evidence").glob("*.verdict.json")):
        verdicts.append(json.loads(path.read_text(encoding="utf-8")))
    verdicts.sort(key=lambda d: d.get("detected_at") or 0)
    if not verdicts:
        raise SystemExit("evidence/*.verdict.json жоқ — алдымен видеоны өткізіңіз.")
    photos, documents = {}, {}
    for item in verdicts:
        photo_path = ROOT / "evidence" / item["photo"]
        if photo_path.exists():
            photos[item["event_id"]] = cv2.imread(str(photo_path))
        doc_path = ROOT / "evidence" / ("%s.json" % item["event_id"])
        if doc_path.exists():
            try:
                documents[item["event_id"]] = json.loads(
                    doc_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    track_path = Path(args.gps_track) if args.gps_track else None
    if track_path and not track_path.is_absolute():
        track_path = ROOT / track_path
    track = load_track(track_path)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             args.fps, (W, H))
    if not writer.isOpened():
        raise SystemExit("Видео жазғыш ашылмады: %s" % out_path)

    written = 0
    marks = []

    def now():
        return written / args.fps

    def put(frame, seconds):
        nonlocal written
        for _ in range(max(1, int(round(args.fps * seconds)))):
            writer.write(frame)
            written += 1

    print("2/4  Бөліктерді салу…")

    # ---------- 1. таныстыру ----------
    marks.append(("voice", "intro", now()))
    steps = max(1, int(args.fps * (seg["intro"] + GAP)))
    for i in range(steps):
        writer.write(frame_intro(i / steps, meta))
        written += 1

    # ---------- 2-4. тірі сканерлеу ----------
    marks.append(("voice", "scan", now()))
    marks.append(("sfx", "scan", now()))
    voice_raw_at = now() + seg["scan"] + GAP
    voice_confirm_at = voice_raw_at + seg["raw"] + GAP
    marks.append(("voice", "raw", voice_raw_at))
    marks.append(("voice", "confirm", voice_confirm_at))

    live_len = seg["scan"] + seg["raw"] + seg["confirm"] + GAP * 3
    per_frame = live_len / max(1, meta["total"])
    state = {"raw": 0, "found": [], "lat": 0.0, "lon": 0.0}
    for record, image in zip(frames, images):
        seconds = record["index"] / meta["fps"]
        state["raw"] += len(record["detections"])
        state["lat"], state["lon"] = fix_at(track, seconds)
        for det in record["confirmed"]:
            state["found"].append({"code": "A%d" % (len(state["found"]) + 1),
                                   "cls": det["cls"], "conf": det["conf"], "at": seconds})
            marks.append(("sfx", "blip", now()))
        stage = 0 if now() < voice_confirm_at else 1
        put(frame_live(image, record, state, meta, cfg, stage,
                       (record["index"] % 24) / 24.0), per_frame)

    # ---------- 5. біріктіру ----------
    merge_record = next((r for r in frames if r["merge"]), None)
    if merge_record is not None:
        marks.append(("voice", "merge", now()))
        marks.append(("sfx", "merge", now() + 0.15))
        image = images[merge_record["index"] - 1]
        steps = max(1, int(args.fps * (seg["merge"] + GAP)))
        for i in range(steps):
            writer.write(fade_in(frame_merge(image, merge_record["merge"], meta,
                                             i / steps), i / steps))
            written += 1
    else:
        print("     ЕСКЕРТУ: біріктіру оқиғасы табылмады — бөлім өткізілді")

    # ---------- 6. нейрожелі ----------
    marks.append(("voice", "ai", now()))
    per_vote = (seg["ai"] + GAP) / 4.0
    for shown in range(4):
        if shown > 0:
            marks.append(("sfx", "blip", now()))
        put(frame_ai(verdicts, photos, shown), per_vote)

    # ---------- 7. өлшем шегі ----------
    marks.append(("voice", "size", now()))
    marks.append(("sfx", "reject", now() + (seg["size"] + GAP) * 0.6))
    steps = max(1, int(args.fps * (seg["size"] + GAP)))
    for i in range(steps):
        writer.write(fade_in(frame_size(verdicts, i / steps), i / steps))
        written += 1

    registered = [v for v in verdicts if v.get("decision") == "registered"]

    # ---------- 8. базаға тіркеу ----------
    marks.append(("voice", "save", now()))
    marks.append(("sfx", "save", now() + seg["save"] * 0.72))
    steps = max(1, int(args.fps * (seg["save"] + GAP)))
    for i in range(steps):
        frame = frame_save(registered, photos, documents, i / steps)
        writer.write(fade_in(frame, i / steps))
        written += 1

    # ---------- 9. қорытынды ----------
    marks.append(("voice", "final", now()))
    marks.append(("sfx", "confirm", now() + 0.25))
    steps = max(1, int(args.fps * (seg["final"] + 1.6)))
    for i in range(steps):
        frame = frame_final(registered, photos, meta, len(verdicts), i / steps)
        writer.write(fade_in(frame, i / steps))
        written += 1

    writer.release()

    print("3/4  Дыбыс белгілерін жазу…")
    (ROOT / "reports" / "audio" / "marks.json").write_text(
        json.dumps({"fps": args.fps, "duration": round(written / args.fps, 3),
                    "marks": [{"kind": k, "name": n, "at": round(t, 3)}
                              for k, n, t in marks]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print("4/4  Дайын\n")
    print("%s" % out_path)
    print("  өлшемі   : %.1f МБ  (%dx%d)" % (out_path.stat().st_size / 1e6, W, H))
    print("  ұзақтығы : %.1f сек" % (written / args.fps))
    print("  тізбек   : %d кадр → %d детекция → %d үміткер → %d құжат"
          % (meta["total"], meta["raw"], len(verdicts), len(registered)))
    print("\nКелесі қадам:  python scripts/mux_demo_audio.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
