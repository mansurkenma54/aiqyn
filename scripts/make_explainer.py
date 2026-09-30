"""ПИТЧ ВИДЕОСЫ — жобаның мағынасын түсіндіретін толық жазба.

Он бөлім. Әр бөліктің ұзақтығы ДИКТОРДЫҢ сол жолына тең
(`reports/audio/explainer/narration.json`), сондықтан сурет пен дауыс
дәл сәйкес келеді.

Дереккөздер (үшеуі де араласады):
  • логотип анимациясы — бар файл
  • Higgsfield кадрлары  — reports/promo/clip*.mp4
  • НАҚТЫ жазба          — reports/AIQYN-demo-silent.mp4, портал скриншоты,
                            дрон видеосы

    python scripts/make_explainer_voice.py     # диктор (бір рет)
    python scripts/make_explainer.py
    python scripts/mux_explainer.py            # дыбысты қосу
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vision.overlay import TextLayer, rounded_rect     # noqa: E402

W, H, FPS = 1920, 1080, 30
XFADE = 0.34                    # бөліктер арасындағы жұмсақ ауысу

BG = (14, 13, 13)
WHITE = (255, 255, 255)
GREY = (176, 176, 176)
CYAN = (255, 205, 60)
SIGNAL = (48, 59, 255)
GREEN = (110, 210, 90)

MEDIA = Path(r"C:\Users\Acer\Desktop\smart citi\докмументы\видео")
PROMO = ROOT / "reports" / "promo"


# ==================================================================
#  Бөліктердің сценарийі
# ==================================================================
# kind: video | still | demo
#   video — файлдан кадрлар (start_sec-тен бастап)
#   demo  — AIQYN-demo-silent.mp4 ішіндегі нақты жазба
#   still — сурет
SCENES = [
    dict(key="logo", kind="video", src=MEDIA / "AIqyn_logo_animation_creation_202608142318.mp4",
         start=0.4, title=None, sub=None),

    dict(key="problem", kind="video", src=PROMO / "clip1_problem.mp4", start=0.2,
         title="3 846 км", sub="қалалық жол қоры · қолмен тексеру мүмкін емес"),

    dict(key="sheriff", kind="video", src=PROMO / "clip2_sheriff.mp4", start=0.2,
         title="«Кибер Шериф»", sub="камера дабыл шамының ішінде — қалада бұрыннан бар"),

    dict(key="noequip", kind="video", src=PROMO / "clip2_sheriff.mp4", start=3.4,
         title="Жаңа жабдық жоқ", sub="жаңа маршрут та құрылмайды — бар қозғалысқа көру қабілетін қосамыз"),

    dict(key="detect", kind="demo", start=5.6,
         title="Автоматты анықтау", sub="ақау әлі жарықшақ кезінде табылады"),

    dict(key="filter", kind="demo", start=17.4,
         title="Жалған дабылды сүзу", sub="нейрожелі әр үміткерді үш рет тексереді"),

    dict(key="offline", kind="video", src=PROMO / "clip3_operator.mp4", start=0.3,
         title="Интернетсіз жұмыс", sub="бәрі көлікте есептеледі, байланыс қосылғанда базаға түседі"),

    dict(key="operator", kind="still", src=ROOT / "reports" / "demo-pack" / "03-karta" / "karta.png",
         title="Оператор офисте", sub="жол араламай, дәлелді көріп растайды"),

    dict(key="scale", kind="video", src=MEDIA / "дрон видео .mp4", start=1.0,
         title="Дрон да, стационар камера да", sub="сол детектор, сол оқиға пішімі"),

    dict(key="city", kind="video", src=PROMO / "clip4_city.mp4", start=0.3,
         title="Smart City Шымкент", sub="жақсы жол — ақылды қаланың көрінетін бөлігі"),
]


# ==================================================================

def fit_cover(image, w=W, h=H):
    """Кадрды экранға толтыра орналастыру (пропорция сақталады)."""
    ih, iw = image.shape[:2]
    scale = max(w / iw, h / ih)
    view = cv2.resize(image, (int(iw * scale + 0.5), int(ih * scale + 0.5)),
                      interpolation=cv2.INTER_AREA)
    y = max(0, (view.shape[0] - h) // 2)
    x = max(0, (view.shape[1] - w) // 2)
    return view[y:y + h, x:x + w]


def fit_contain(image, w=W, h=H):
    """Кадрды қиылмай сыйдыру, қалғаны — қара фон."""
    canvas = np.full((h, w, 3), BG, dtype=np.uint8)
    ih, iw = image.shape[:2]
    scale = min(w / iw, h / ih)
    view = cv2.resize(image, (int(iw * scale), int(ih * scale)),
                      interpolation=cv2.INTER_AREA)
    y = (h - view.shape[0]) // 2
    x = (w - view.shape[1]) // 2
    canvas[y:y + view.shape[0], x:x + view.shape[1]] = view
    return canvas


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def draw_caption(canvas, title, sub, reveal: float):
    """Төменгі жазу — экранның төртінші бөлігін алатын қара градиент."""
    if not title:
        return canvas
    k = ease(min(1.0, reveal / 0.22))
    if k <= 0.01:
        return canvas

    band_h = 330
    top = H - band_h
    grad = np.linspace(0.0, 0.97, band_h).reshape(-1, 1, 1)
    region = canvas[top:H].astype(np.float32)
    canvas[top:H] = (region * (1 - grad * k)).astype(np.uint8)

    text = TextLayer()
    pad = 96
    shift = int((1.0 - k) * 26)
    cv2.rectangle(canvas, (pad, H - 168 + shift), (pad + 6, H - 66 + shift), CYAN, -1)
    text.add((pad + 28, H - 172 + shift), title, size=56, color=WHITE, bold=True)
    if sub:
        text.add((pad + 30, H - 100 + shift), sub, size=29, color=(214, 214, 214))
    return text.flush(canvas)


def draw_logo_overlay(canvas, reveal: float):
    """Соңғы кадрдағы атау."""
    text = TextLayer()
    k = ease(min(1.0, reveal / 0.3))
    if k < 0.02:
        return canvas
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (W, H), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.46 * k, canvas, 1 - 0.46 * k, 0, canvas)
    text.add((W // 2, H // 2 - 92), "AIQYN ZHOL", size=104, color=WHITE,
             bold=True, anchor="ma")
    text.add((W // 2, H // 2 + 44), "Шымкенттің жол инфрақұрылымын автоматты бақылау",
             size=32, color=GREY, anchor="ma")
    return text.flush(canvas)


class Source:
    """Бір дереккөзден кадр беретін қабат (видео да, сурет те)."""

    def __init__(self, scene, demo_path):
        self.kind = scene["kind"]
        self.frames = []
        self.cap = None
        self.still = None

        if self.kind == "still":
            self.still = fit_contain(cv2.imread(str(scene["src"])))
            return

        path = demo_path if self.kind == "demo" else scene["src"]
        if not Path(path).exists():
            raise SystemExit("Файл жоқ: %s" % path)
        self.cap = cv2.VideoCapture(str(path))
        src_fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(scene.get("start", 0) * src_fps))
        self.src_fps = src_fps
        self.last = None

    def read(self, index: int):
        if self.still is not None:
            # Баяу зум — статикалық сурет «тірі» болып көрінеді
            k = 1.0 + 0.05 * (index / max(1, FPS * 4))
            h, w = self.still.shape[:2]
            ch, cw = int(h / k), int(w / k)
            y, x = (h - ch) // 2, (w - cw) // 2
            return cv2.resize(self.still[y:y + ch, x:x + cw], (W, H),
                              interpolation=cv2.INTER_LINEAR)

        # Дереккөздің FPS-і мен шығыс FPS-і әртүрлі болуы мүмкін
        want = int(index * self.src_fps / FPS)
        while True:
            ok, frame = self.cap.read()
            if not ok:
                return self.last if self.last is not None else np.full((H, W, 3), BG, np.uint8)
            self._pos = getattr(self, "_pos", -1) + 1
            # Демо жазбасында оң жақта дерек панелі тұр — оны қиюға болмайды
            self.last = fit_contain(frame) if self.kind == "demo" else fit_cover(frame)
            if self._pos >= want:
                return self.last

    def close(self):
        if self.cap:
            self.cap.release()


def main() -> int:
    narration_path = ROOT / "reports" / "audio" / "explainer" / "narration.json"
    if not narration_path.exists():
        raise SystemExit("Алдымен: python scripts/make_explainer_voice.py")
    narration = json.loads(narration_path.read_text(encoding="utf-8"))
    seg = {item["key"]: item["duration"] for item in narration["lines"]}

    demo_path = ROOT / "reports" / "AIQYN-demo-silent.mp4"
    out_path = ROOT / "reports" / "AIQYN-pitch-silent.mp4"
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not writer.isOpened():
        raise SystemExit("Жазғыш ашылмады: %s" % out_path)

    marks, written = [], 0
    prev_tail = None            # алдыңғы бөліктің соңғы кадрлары (ауысу үшін)
    prev_kind = None

    print("Бөліктерді салу…")
    for scene in SCENES:
        key = scene["key"]
        seconds = seg.get(key, 3.0) + 0.28
        total = max(2, int(FPS * seconds))
        marks.append(("voice", key, written / FPS))

        source = Source(scene, demo_path)
        rendered = []
        for i in range(total):
            frame = source.read(i).copy()
            reveal = i / total
            if key == "city":
                frame = draw_logo_overlay(frame, reveal)
            else:
                frame = draw_caption(frame, scene["title"], scene["sub"], reveal)
            rendered.append(frame)
        source.close()

        # --- ауысу: алдыңғы бөліктің соңымен араластыру ---
        # Бір интерфейстің ішінде (демо -> демо) жайылту ыңғайсыз: екі жазу
        # қатар шығып, екі кадр бірінің үстіне бірі түседі. Ондайда — КЕСУ.
        same_ui = prev_kind == "demo" and scene["kind"] == "demo"
        blend = 0 if same_ui else int(FPS * XFADE)
        if prev_tail and blend and len(rendered) > blend:
            for j in range(blend):
                a = prev_tail[j] if j < len(prev_tail) else prev_tail[-1]
                k = (j + 1) / (blend + 1)
                writer.write(cv2.addWeighted(a, 1 - k, rendered[j], k, 0))
                written += 1
            body = rendered[blend:]
        else:
            if prev_tail:
                for frame in prev_tail:
                    writer.write(frame)
                    written += 1
            body = rendered

        keep = max(1, len(body) - blend) if blend else len(body)
        for frame in body[:keep]:
            writer.write(frame)
            written += 1
        prev_tail = body[keep:]
        prev_kind = scene["kind"]
        print("  %-9s %5.2f сек%s" % (key, seconds, "  (кесу)" if same_ui else ""))

    for frame in (prev_tail or []):
        writer.write(frame)
        written += 1
    # соңында аздап тұрып қалу
    if prev_tail:
        for _ in range(int(FPS * 0.9)):
            writer.write(prev_tail[-1])
            written += 1

    writer.release()

    (ROOT / "reports" / "audio" / "explainer" / "marks.json").write_text(
        json.dumps({"fps": FPS, "duration": round(written / FPS, 3),
                    "marks": [{"kind": k, "name": n, "at": round(t, 3)}
                              for k, n, t in marks]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n%s" % out_path)
    print("  өлшемі   : %.1f МБ  (%dx%d)" % (out_path.stat().st_size / 1e6, W, H))
    print("  ұзақтығы : %.1f сек" % (written / FPS))
    print("\nКелесі:  python scripts/mux_explainer.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
