# -*- coding: utf-8 -*-
"""AIQYN — жол ақауын автоматты анықтау (жұмыс үстелі қолданбасы).

Скрипт емес, толыққанды қолданба:
    * видео ТОЛЫҚ жылдамдықпен жүреді — талдау фонда, бөлек ағында
    * кадрға тек ақаудың өзі белгіленеді (сызық жоқ, кесу жоқ)
    * баптау ЖҰМЫС КЕЗІНДЕ бірден әсер етеді
    * табылған ақау сол сәтте тізімге түседі және күйі жаңарып отырады:
      «табылды» -> «дәлел жазылуда» -> «құжат жіберілді»

Іске қосу:  AIQYN.bat   немесе   python app.py
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox

import cv2
from PIL import Image, ImageTk

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _enable_hidpi() -> None:
    """Экран масштабын (125%, 150%) Windows-қа ӨЗІМІЗ өңдейміз деп айту.

    Бұл болмаса Windows терезені жай ғана СОЗЫП үлкейтеді: жазу да,
    видео да бұлдыр болып көрінеді. Осы шақыруды Tk терезесін құрмас
    БҰРЫН жасау керек.
    """
    if sys.platform != "win32":
        return
    import ctypes
    for call in (
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),   # per-monitor
        lambda: ctypes.windll.user32.SetProcessDPIAware(),        # ескі Windows
    ):
        try:
            call()
            return
        except Exception:
            continue


_enable_hidpi()

from vision.config import Config                      # noqa: E402
from vision.overlay import LIVE_LABELS, LiveHud       # noqa: E402
from vision.pipeline import VisionPipeline            # noqa: E402
from vision.report import build_report, default_report_path   # noqa: E402

CONFIG_PATH = os.path.join(HERE, "config.json")

# ---------------------------------------------------------------- түстер ---
BG = "#0b0b0c"          # терезенің фоны
PANEL = "#141417"       # оң жақ панель
CARD = "#1b1b20"        # карточка / түйме
CARD_HI = "#24242b"     # тінтуір үстіндегі
LINE = "#2a2a31"        # жиек
FG = "#f2f2f4"          # негізгі мәтін
MUTED = "#8a8a93"       # көмекші мәтін
DIM = "#5c5c66"         # ең солғын
ACCENT = "#ff3b1f"      # AIQYN қызылы
GREEN = "#2ecc71"
YELLOW = "#ffc400"
BLUE = "#3d9bff"

UI = "Segoe UI"

# Күй тізбегі: ақау табылған сәттен ресми құжатқа дейін
STATUS_TEXT = {
    "found": "ақау табылды",
    "evidence": "дәлел жазылуда",
    "sent": "құжат жіберілді",
    "doubt": "жіберілді · ЖИ күмәнданды",
    "rejected": "ЖИ жоққа шығарды",
}
STATUS_COLOR = {
    "found": YELLOW,
    "evidence": BLUE,
    "sent": GREEN,
    "doubt": YELLOW,
    "rejected": DIM,
}


def _save_settings(values: dict) -> None:
    """config.json ішіндегі БІРНЕШЕ кілтті ғана жаңарту.

    Толық Config.save() қолданбаймыз: ол .env-тен келген құпия кілттерді
    де файлға жазып жіберер еді.
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        data = {}
    data.update(values)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
#  Кішкентай UI бөлшектері
# ============================================================

class FlatButton(tk.Frame):
    """tk.Button Windows-та жиегі мен «басылу» эффектісін тастамайды,
    сондықтан түймені Frame + Label-ден құрастырамыз."""

    def __init__(self, parent, text, command, *, bg=CARD, fg=FG, hover=CARD_HI,
                 font=(UI, 10), padx=16, pady=9, width=None):
        super().__init__(parent, bg=bg, cursor="hand2")
        self._bg, self._hover = bg, hover
        self._command = command
        self._enabled = True
        self.label = tk.Label(self, text=text, bg=bg, fg=fg, font=font,
                              padx=padx, pady=pady)
        if width:
            self.label.config(width=width)
        self.label.pack(fill="both", expand=True)
        for widget in (self, self.label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<Button-1>", self._on_click)

    def _on_enter(self, _event=None):
        if self._enabled:
            self._paint(self._hover)

    def _on_leave(self, _event=None):
        self._paint(self._bg)

    def _on_click(self, _event=None):
        if self._enabled and self._command:
            self._command()

    def _paint(self, color):
        self.config(bg=color)
        self.label.config(bg=color)

    def configure_style(self, *, text=None, bg=None, fg=None, hover=None, enabled=None):
        if text is not None:
            self.label.config(text=text)
        if bg is not None:
            self._bg = bg
            self._paint(bg)
        if fg is not None:
            self.label.config(fg=fg)
        if hover is not None:
            self._hover = hover
        if enabled is not None:
            self._enabled = enabled
            self.config(cursor="hand2" if enabled else "arrow")


class Slider(tk.Frame):
    """Атауы, мәні және түсіндірмесі бар жүгірткі.

    ttk.Scale қара тақырыпта дұрыс боялмайды, сондықтан Canvas-пен
    өзіміз саламыз — әрі сүйретіп тұрғанда бірден жаңарады.
    """

    def __init__(self, parent, text, hint, value, low, high, on_change,
                 suffix=""):
        super().__init__(parent, bg=PANEL)
        self.low, self.high = low, high
        self.value = value
        self.on_change = on_change
        self.suffix = suffix

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text=text, bg=PANEL, fg=FG, font=(UI, 9)).pack(side="left")
        self.value_label = tk.Label(head, text=f"{value}{suffix}", bg=PANEL,
                                    fg=ACCENT, font=(UI, 9, "bold"))
        self.value_label.pack(side="right")

        self.canvas = tk.Canvas(self, height=20, bg=PANEL, highlightthickness=0,
                                cursor="hand2")
        self.canvas.pack(fill="x", pady=(3, 0))
        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_drag)
        self.canvas.bind("<B1-Motion>", self._on_drag)

        tk.Label(self, text=hint, bg=PANEL, fg=DIM,
                 font=(UI, 8)).pack(anchor="w", pady=(1, 0))

    def _frac(self) -> float:
        span = max(1, self.high - self.low)
        return (self.value - self.low) / span

    def _redraw(self):
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        if width < 10:
            return
        y = 10
        self.canvas.create_line(0, y, width, y, fill=LINE, width=4,
                                capstyle="round")
        x = 6 + (width - 12) * self._frac()
        self.canvas.create_line(0, y, x, y, fill=ACCENT, width=4, capstyle="round")
        self.canvas.create_oval(x - 6, y - 6, x + 6, y + 6, fill=FG, outline="")

    def _on_drag(self, event):
        width = max(1, self.canvas.winfo_width() - 12)
        frac = min(1.0, max(0.0, (event.x - 6) / width))
        new = int(round(self.low + frac * (self.high - self.low)))
        if new != self.value:
            self.value = new
            self.value_label.config(text=f"{new}{self.suffix}")
            if self.on_change:
                self.on_change(new)
        self._redraw()

    def get(self) -> int:
        return self.value


class Toggle(tk.Frame):
    """Қара тақырыпқа сай құсбелгі."""

    def __init__(self, parent, text, value, on_change):
        super().__init__(parent, bg=PANEL, cursor="hand2")
        self.value = bool(value)
        self.on_change = on_change
        self._enabled = True

        self.box = tk.Label(self, text="", bg=PANEL, fg=FG, width=2,
                            font=(UI, 9, "bold"))
        self.box.pack(side="left")
        self.text = tk.Label(self, text=text, bg=PANEL, fg=MUTED, font=(UI, 9))
        self.text.pack(side="left")
        for widget in (self, self.box, self.text):
            widget.bind("<Button-1>", self._toggle)
        self._paint()

    def _paint(self):
        self.box.config(text="◉" if self.value else "○",
                        fg=ACCENT if self.value else DIM)
        self.text.config(fg=FG if self.value else MUTED)

    def _toggle(self, _event=None):
        if not self._enabled:
            return
        self.value = not self.value
        self._paint()
        if self.on_change:
            self.on_change(self.value)

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self.config(cursor="hand2" if enabled else "arrow")
        self.text.config(fg=FG if (enabled and self.value) else
                         (MUTED if enabled else DIM))

    def get(self) -> bool:
        return self.value


HELP_TEXT = [
    ("Бұл бағдарлама не істейді?", [
        "Көліктің әйнегіндегі камерадан келген видеоны нақты уақытта талдап,",
        "жол ақауларын (шұңқыр, жарық, қирау) өзі табады.",
        "Әр ақау бойынша дәлел жинап (фото + 8 секундтық видео), координатасын,",
        "мекенжайын анықтап, дайын ресми құжат құрып, сайтқа жібереді.",
        "Сізден бір ғана нәрсе қажет: «ІСКЕ ҚОСУ» түймесін басу.",
    ]),
    ("1-қадам. Дереккөзді таңдау", [
        "«Телефон» — көліктегі телефонның камерасы (нақты жұмыс режимі).",
        "   Телефонда IP Webcam ашық, «Start server» басылған, USB модем",
        "   мен GPS қосулы болуы керек.",
        "«Видеофайл» — бұрын жазылған видео. Телефонсыз тексеру үшін.",
        "   «Видеофайлды таңдау…» түймесін басып, mp4 немесе mov файлын",
        "   көрсетесіз (Ctrl+O).",
    ]),
    ("2-қадам. Іске қосу", [
        "Жасыл «ІСКЕ ҚОСУ» түймесін басасыз.",
        "Модель дайындалғанша бірнеше секунд кетеді — терезеде жазылып тұрады.",
        "Содан кейін видео жүре бастайды, ақау табылса қызыл рамкамен",
        "белгіленеді және оң жақтағы тізімге түседі.",
        "«КІДІРТУ» (Space) — уақытша тоқтату, «ТОҚТАТУ» (Esc) — аяқтау.",
    ]),
    ("3-қадам. Ақаудың жолы", [
        "Тізімдегі әр жазба үш кезеңнен өтеді:",
        "   «ақау табылды»      — модель көрді",
        "   «дәлел жазылуда»    — 8 секундтық видео мен фото жиналуда",
        "   «құжат жіберілді»   — мекенжайы анықталып, сайтқа кетті",
        "Егер ЖИ-тексеруші оны жалған деп тапса — «ЖИ жоққа шығарды» деп",
        "жазылады да, себебі көрсетіледі. Бұл қалыпты жағдай.",
    ]),
    ("4-қадам. Есеп (презентация үшін)", [
        "Тексеру аяқталғанда «ЕСЕПТІ АШУ» түймесі жанады.",
        "Ол — БІР HTML файл: барлық ақау, суреттерімен, координатасымен,",
        "мекенжайымен. Суреттер файлдың ішінде, сондықтан интернетсіз де",
        "ашылады, флешкамен көшіруге де болады.",
        "«СКРИНШОТ» (Ctrl+S) — ағымдағы көріністі толық сапада сақтайды,",
        "ол да есепке қосылады.",
        "Барлық файл «Есептер қалтасы» түймесінің астында.",
    ]),
    ("Баптау", [
        "Сенімділік шегі — төмендетсеңіз көбірек табады, бірақ жалған",
        "   сигнал да көбеюі мүмкін. Өлшеніп таңдалған мән: 30.",
        "Кадрдың төменін кесу — әдепкіде 0. Телефон панелі (торпедо) кадрды",
        "   жауып тұрғанда ғана көтеріңіз. Артық кессеңіз ақау жоғалады.",
        "Дәлдеу режимі — жол бөлігін бөлек кесінділермен де талдайды.",
        "   3 есе көп табады, талдау сәл баяулайды. Қосулы тұрғаны дұрыс.",
        "Баптау жұмыс кезінде де бірден әсер етеді.",
    ]),
    ("Пернелер", [
        "Space — кідірту/жалғастыру",
        "Esc      — тоқтату",
        "Ctrl+O   — видеофайл таңдау",
        "Ctrl+S   — скриншот",
        "F1       — осы көмек",
    ]),
]


class HelpWindow(tk.Toplevel):
    """Бағдарламаны қалай қолдану керегі — терезенің ішінде, қазақша."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("AIQYN — қалай қолдану керек")
        self.configure(bg=BG)
        self.transient(parent)
        width = min(880, parent.winfo_screenwidth() - 80)
        height = min(760, parent.winfo_screenheight() - 120)
        self.geometry(f"{width}x{height}"
                      f"+{parent.winfo_rootx() + 60}+{parent.winfo_rooty() + 40}")

        head = tk.Frame(self, bg="#08080a", height=52)
        head.pack(fill="x")
        head.pack_propagate(False)
        tk.Label(head, text="AIQYN — нұсқаулық", bg="#08080a", fg=FG,
                 font=(UI, 13, "bold")).pack(side="left", padx=20)
        FlatButton(head, "Жабу", self.destroy, padx=16, pady=6,
                   font=(UI, 9)).pack(side="right", padx=16, pady=9)

        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=4, pady=4)
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scroll.set)

        inner = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(window, width=e.width))
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        for title, lines in HELP_TEXT:
            block = tk.Frame(inner, bg=CARD)
            block.pack(fill="x", padx=18, pady=(14, 0))
            tk.Label(block, text=title, bg=CARD, fg=ACCENT,
                     font=(UI, 11, "bold"), anchor="w").pack(
                         fill="x", padx=16, pady=(12, 4))
            for line in lines:
                tk.Label(block, text=line, bg=CARD, fg=FG if not line.startswith("   ")
                         else MUTED, font=(UI, 9), anchor="w",
                         justify="left").pack(fill="x", padx=16, pady=0)
            tk.Frame(block, bg=CARD, height=12).pack()

        tk.Frame(inner, bg=BG, height=18).pack()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.focus_set()


class DefectCard(tk.Frame):
    """Тізімдегі бір ақау: суреті, атауы, уақыты және күйі."""

    def __init__(self, parent, event_id, label, confidence, thumb_image):
        super().__init__(parent, bg=CARD)
        self.event_id = event_id
        self._photo = thumb_image        # сілтемені ұстап тұру керек

        inner = tk.Frame(self, bg=CARD)
        inner.pack(fill="x", padx=8, pady=7)

        if thumb_image is not None:
            holder = tk.Label(inner, image=thumb_image, bg=CARD, bd=0)
            holder.pack(side="left", padx=(0, 9))

        info = tk.Frame(inner, bg=CARD)
        info.pack(side="left", fill="x", expand=True)

        top = tk.Frame(info, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text=label, bg=CARD, fg=FG,
                 font=(UI, 9, "bold")).pack(side="left")
        tk.Label(top, text=f"{confidence * 100:.0f}%", bg=CARD, fg=ACCENT,
                 font=(UI, 9, "bold")).pack(side="right")

        self.status_label = tk.Label(info, text=STATUS_TEXT["found"], bg=CARD,
                                     fg=STATUS_COLOR["found"], font=(UI, 8),
                                     anchor="w")
        self.status_label.pack(fill="x", pady=(2, 0))

        self.detail_label = tk.Label(
            info, text=datetime.now().strftime("%H:%M:%S"), bg=CARD, fg=DIM,
            font=(UI, 8), anchor="w", justify="left", wraplength=190)
        self.detail_label.pack(fill="x")

    def set_status(self, key: str, detail: str = ""):
        self.status_label.config(text=STATUS_TEXT.get(key, key),
                                 fg=STATUS_COLOR.get(key, MUTED))
        if detail:
            self.detail_label.config(text=detail)


# ============================================================
#  Негізгі терезе
# ============================================================

class AiqynApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AIQYN — жол ақауын автоматты анықтау")
        self.configure(bg=BG)

        # Терезенің өлшемін ЭКРАНҒА қарап аламыз. Бекітілген 1400x860
        # кіші ноутбукте экраннан асып кетіп, оң жақ панель көрінбей
        # қалатын.
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1520, max(1120, int(screen_w * 0.86)))
        height = min(940, max(700, int(screen_h * 0.86)))
        self.geometry(f"{width}x{height}"
                      f"+{max(0, (screen_w - width) // 2)}"
                      f"+{max(0, (screen_h - height) // 3)}")
        self.minsize(1060, 680)

        base = Config.load()
        self.pipeline: VisionPipeline | None = None
        self.worker: threading.Thread | None = None
        self.running = False
        self.paused = False

        self.hud = LiveHud()
        self._display_size = (960, 540)     # frame_callback осыны оқиды
        self._frames: queue.Queue = queue.Queue(maxsize=1)
        self._events: queue.Queue = queue.Queue()
        self._photo = None
        self._cards: dict[str, DefectCard] = {}
        self._defects: dict[str, dict] = {}   # есеп үшін толық дерек
        self._thumbs: list = []             # PhotoImage-тердің сілтемелері
        self._screenshots: list = []
        self._found = 0
        self._sent = 0
        self._rejected = 0
        self._streaming = False      # бірінші кадр келді ме
        self._last_raw = None        # соңғы кадр + детекциялар (скриншот үшін)
        self._started_at = 0.0
        self._device_label = "—"
        self._last_report = None

        self.video_path = base.video_path or ""
        self.source = base.source if base.source in ("phone", "file") else "phone"

        self._build_ui(base)
        self.bind("<space>", lambda _e: self.on_pause())
        self.bind("<Escape>", lambda _e: self.on_stop())
        self.bind("<Control-o>", lambda _e: self._pick_file())
        self.bind("<Control-s>", lambda _e: self.on_screenshot())
        self.bind("<F1>", lambda _e: self._open_help())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(33, self._pump)
        self.after(400, self._check_services)

    # ------------------------------------------------------------- UI ---

    def _build_ui(self, base: Config):
        # ---------- жоғарғы жолақ ----------
        header = tk.Frame(self, bg="#08080a", height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        brand = tk.Frame(header, bg="#08080a")
        brand.pack(side="left", padx=(20, 0))
        tk.Label(brand, text="AIQYN", bg="#08080a", fg=FG,
                 font=(UI, 17, "bold")).pack(side="left")
        tk.Label(brand, text="●", bg="#08080a", fg=ACCENT,
                 font=(UI, 11)).pack(side="left", padx=(6, 0))
        tk.Label(brand, text="  жол ақауын автоматты анықтау · Шымкент",
                 bg="#08080a", fg=MUTED, font=(UI, 9)).pack(side="left")

        FlatButton(header, "?  Көмек", self._open_help,
                   padx=14, pady=7, font=(UI, 9)).pack(side="right", padx=(0, 18),
                                                       pady=11)
        FlatButton(header, "Сайтты ашу", self._open_portal,
                   padx=14, pady=7, font=(UI, 9)).pack(side="right", padx=(0, 8),
                                                       pady=11)
        FlatButton(header, "Есептер қалтасы", self._open_reports,
                   padx=14, pady=7, font=(UI, 9)).pack(side="right", padx=(0, 8),
                                                       pady=11)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=14)

        # ---------- оң жақ панель ----------
        right = tk.Frame(body, bg=PANEL, width=310)
        right.pack(side="right", fill="y", padx=(16, 0))
        right.pack_propagate(False)
        self._build_side_panel(right, base)

        # ---------- сол жақ: видео ----------
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(left, bg="#000", highlightthickness=1,
                                highlightbackground=LINE, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self._placeholder = None
        self._draw_placeholder()

        # прогресс жолағы (видеофайл үшін)
        self.progress = tk.Canvas(left, height=3, bg=LINE, highlightthickness=0)
        self.progress.pack(fill="x", pady=(1, 0))

        controls = tk.Frame(left, bg=BG)
        controls.pack(fill="x", pady=(14, 0))

        self.btn_start = FlatButton(
            controls, "▶   ІСКЕ ҚОСУ", self.on_start,
            bg=GREEN, fg="#06170d", hover="#3fdd84",
            font=(UI, 11, "bold"), padx=26, pady=13)
        self.btn_start.pack(side="left")

        self.btn_pause = FlatButton(
            controls, "❚❚   КІДІРТУ", self.on_pause,
            bg=CARD, fg=DIM, hover=CARD, font=(UI, 11, "bold"), padx=20, pady=13)
        self.btn_pause.pack(side="left", padx=9)
        self.btn_pause.configure_style(enabled=False)

        self.btn_stop = FlatButton(
            controls, "■   ТОҚТАТУ", self.on_stop,
            bg=CARD, fg=DIM, hover=CARD, font=(UI, 11, "bold"), padx=22, pady=13)
        self.btn_stop.pack(side="left")
        self.btn_stop.configure_style(enabled=False)

        # Презентацияға арналған: ағымдағы көріністі суретке түсіру
        self.btn_shot = FlatButton(
            controls, "📷  СКРИНШОТ", self.on_screenshot,
            bg=CARD, fg=DIM, hover=CARD, font=(UI, 10, "bold"), padx=16, pady=13)
        self.btn_shot.pack(side="left", padx=9)
        self.btn_shot.configure_style(enabled=False)

        self.btn_report = FlatButton(
            controls, "📄  ЕСЕПТІ АШУ", self._open_report,
            bg=CARD, fg=DIM, hover=CARD, font=(UI, 10, "bold"), padx=16, pady=13)
        self.btn_report.pack(side="left")
        self.btn_report.configure_style(enabled=False)

        self.status = tk.Label(controls, text="Дайын", bg=BG, fg=MUTED,
                               font=(UI, 10))
        self.status.pack(side="left", padx=16)

        self.hint = tk.Label(
            left, text="Space — кідірту · Esc — тоқтату · Ctrl+O — видеофайл "
                       "· Ctrl+S — скриншот",
            bg=BG, fg=DIM, font=(UI, 8))
        self.hint.pack(anchor="w", pady=(9, 0))

    def _build_side_panel(self, parent, base: Config):
        # ---------- қадамдық нұсқау ----------
        # Терезені бірінші рет ашқан адам не істеу керегін бірден көруі тиіс.
        self._section(parent, "ҚАЛАЙ ҚОЛДАНУ КЕРЕК")
        steps_wrap = tk.Frame(parent, bg=PANEL)
        steps_wrap.pack(fill="x", padx=16)
        self.step_rows = []
        for index, text in enumerate((
            "Дереккөзді таңдаңыз",
            "«ІСКЕ ҚОСУ» түймесін басыңыз",
            "Ақаулар өзі табылады",
            "«ЕСЕПТІ АШУ» — қорытынды",
        )):
            row = tk.Frame(steps_wrap, bg=PANEL)
            row.pack(fill="x", pady=1)
            num = tk.Label(row, text=str(index + 1), bg=CARD, fg=MUTED,
                           font=(UI, 8, "bold"), width=3)
            num.pack(side="left")
            label = tk.Label(row, text="  " + text, bg=PANEL, fg=MUTED,
                             font=(UI, 9), anchor="w")
            label.pack(side="left", fill="x", expand=True)
            self.step_rows.append((num, label))
        self._set_step(0)

        self._section(parent, "ДЕРЕККӨЗ")

        seg = tk.Frame(parent, bg=PANEL)
        seg.pack(fill="x", padx=16)
        self.src_buttons = {}
        for key, text in (("phone", "Телефон"), ("file", "Видеофайл")):
            button = FlatButton(seg, text, lambda k=key: self._set_source(k),
                                padx=6, pady=7, font=(UI, 9))
            button.pack(side="left", fill="x", expand=True, padx=(0, 4))
            self.src_buttons[key] = button

        self.btn_file = FlatButton(parent, "Видеофайлды таңдау…", self._pick_file,
                                   padx=6, pady=7, font=(UI, 9))
        self.btn_file.pack(fill="x", padx=16, pady=(6, 3))
        self.lbl_file = tk.Label(parent, text="", bg=PANEL, fg=MUTED,
                                 font=(UI, 8), wraplength=270, justify="left",
                                 anchor="w")
        self.lbl_file.pack(fill="x", padx=16)
        if self.video_path:
            self.lbl_file.config(text=os.path.basename(self.video_path))
        self._set_source(self.source)

        self._section(parent, "ЖҮЙЕ КҮЙІ")
        self.lbl_phone = self._row(parent, "Телефон", "тексерілуде…")
        self.lbl_gps = self._row(parent, "GPS", "—")
        self.lbl_portal = self._row(parent, "Сайт", "—")
        self.lbl_fps = self._row(parent, "Көрініс", "—")
        self.lbl_detect = self._row(parent, "Талдау", "—")
        self.lbl_device = self._row(parent, "Есептеу", "—")

        # Баптау әдепкіде ЖАБЫҚ: 900 px биік экранда ол ашық тұрса, ең
        # керек бөлім — табылған ақаулар тізімі — панельден сыртқа шығып,
        # мүлдем көрінбей қалатын. Баптауды бір басумен ашуға болады.
        self._settings_open = False
        self.lbl_settings = tk.Label(parent, text="БАПТАУ  ▸", bg=PANEL, fg=MUTED,
                                     font=(UI, 8, "bold"), cursor="hand2")
        self.lbl_settings.pack(anchor="w", padx=16, pady=(18, 6))
        self.lbl_settings.bind("<Button-1>", lambda _e: self._toggle_settings())
        settings_box = tk.Frame(parent, bg=PANEL)
        settings_box.pack(fill="x", padx=16)
        settings = self._settings_frame = tk.Frame(settings_box, bg=PANEL)

        self.slider_conf = Slider(
            settings, "Сенімділік шегі",
            "төмендетсеңіз — көбірек табады", int(round(base.conf_threshold * 100)),
            15, 90, self._on_conf_change)
        self.slider_conf.pack(fill="x", pady=(0, 10))

        self.slider_crop = Slider(
            settings, "Кадрдың төменін кесу",
            "тек көліктің панелі көрінсе қажет",
            int(round(base.crop_bottom_frac * 100)), 0, 40,
            self._on_crop_change, suffix="%")
        self.slider_crop.pack(fill="x", pady=(0, 10))

        self.toggle_tile = Toggle(settings, "Дәлдеу режимі (3 есе көп табады)",
                                  base.tile_detect, None)
        self.toggle_tile.pack(fill="x", pady=1)
        self.toggle_cv = Toggle(settings, "Физикалық детектор (үйретусіз)",
                                base.enable_pothole_cv, None)
        self.toggle_cv.pack(fill="x", pady=1)
        self.toggle_lanes = Toggle(settings, "Жол сызықтарын салу",
                                   base.draw_lanes, None)
        self.toggle_lanes.pack(fill="x", pady=1)
        self.toggle_road = Toggle(settings, "Тек жол бетінен іздеу",
                                  base.road_only, None)
        self.toggle_road.pack(fill="x", pady=1)
        self.toggle_labels = Toggle(settings, "Кадрда жазуын көрсету",
                                    True, self._on_labels_change)
        self.toggle_labels.pack(fill="x", pady=1)

        # ---------- табылған ақаулар ----------
        head = tk.Frame(parent, bg=PANEL)
        head.pack(fill="x", padx=16, pady=(18, 4))
        tk.Label(head, text="ТАБЫЛҒАН АҚАУЛАР", bg=PANEL, fg=MUTED,
                 font=(UI, 8, "bold")).pack(side="left")
        self.lbl_found = tk.Label(head, text="0", bg=PANEL, fg=ACCENT,
                                  font=(UI, 15, "bold"))
        self.lbl_found.pack(side="right")

        list_wrap = tk.Frame(parent, bg=BG)
        list_wrap.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.list_canvas = tk.Canvas(list_wrap, bg=BG, highlightthickness=0, bd=0)
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.list_inner = tk.Frame(self.list_canvas, bg=BG)
        self._list_window = self.list_canvas.create_window(
            (0, 0), window=self.list_inner, anchor="nw")
        self.list_inner.bind(
            "<Configure>",
            lambda _e: self.list_canvas.configure(
                scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind(
            "<Configure>",
            lambda e: self.list_canvas.itemconfig(self._list_window, width=e.width))
        self.list_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.list_canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        self.lbl_empty = tk.Label(self.list_inner, text="Іске қосқаннан кейін табылған ақау осында шығады",
                                  bg=BG, fg=DIM, font=(UI, 8), wraplength=240)
        self.lbl_empty.pack(pady=14)

    def _toggle_settings(self):
        self._settings_open = not self._settings_open
        if self._settings_open:
            self._settings_frame.pack(fill="x")
        else:
            self._settings_frame.pack_forget()
        self.lbl_settings.config(text="БАПТАУ  ▾" if self._settings_open else "БАПТАУ  ▸",
                                 fg=FG if self._settings_open else MUTED)

    def _set_step(self, active: int):
        """Ағымдағы қадамды белгілеу — оператор қай кезеңде екенін көреді."""
        for index, (num, label) in enumerate(self.step_rows):
            done = index < active
            now = index == active
            num.config(
                bg=ACCENT if now else (CARD if not done else PANEL),
                fg="#fff" if now else (GREEN if done else DIM),
                text="✓" if done else str(index + 1))
            label.config(fg=FG if now else (MUTED if done else DIM))

    def _section(self, parent, text):
        tk.Label(parent, text=text, bg=PANEL, fg=MUTED,
                 font=(UI, 8, "bold")).pack(anchor="w", padx=16, pady=(18, 6))

    def _row(self, parent, key, value):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=16, pady=1)
        tk.Label(row, text=key, bg=PANEL, fg=MUTED, font=(UI, 9)).pack(side="left")
        label = tk.Label(row, text=value, bg=PANEL, fg=FG, font=(UI, 9, "bold"))
        label.pack(side="right")
        return label

    # --------------------------------------------------------- көрініс ---

    def _on_canvas_resize(self, event):
        # frame_callback кадрды ОСЫ өлшемге кішірейтеді — Tk ағыны
        # тек дайын суретті экранға шығарады.
        self._display_size = (max(160, event.width - 2),
                              max(90, event.height - 2))
        if not self.running:
            self._draw_placeholder()

    def _draw_placeholder(self):
        self.canvas.delete("all")
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self.canvas.create_text(
            width // 2, height // 2 - 12, text="«ІСКЕ ҚОСУ» түймесін басыңыз",
            fill="#3d3d45", font=(UI, 15))
        self.canvas.create_text(
            width // 2, height // 2 + 16,
            text="дереккөзді оң жақтан таңдаңыз",
            fill="#2c2c33", font=(UI, 9))

    def _draw_progress(self, fraction: float):
        self.progress.delete("all")
        width = self.progress.winfo_width()
        if width > 4 and fraction > 0:
            self.progress.create_rectangle(0, 0, int(width * fraction), 4,
                                           fill=ACCENT, outline="")

    # ---------------------------------------------------------- баптау ---

    def _set_source(self, key: str):
        if self.running:
            return
        self.source = key
        if not self.running and (key == "phone" or self.video_path):
            self._set_step(1)
        for name, button in self.src_buttons.items():
            active = name == key
            button.configure_style(
                bg=ACCENT if active else CARD,
                fg="#fff" if active else MUTED,
                hover="#ff5238" if active else CARD_HI)
        self.btn_file.configure_style(enabled=(key == "file"),
                                      fg=FG if key == "file" else DIM)

    def _pick_file(self):
        if self.running:
            return
        path = filedialog.askopenfilename(
            title="Видеофайлды таңдаңыз",
            filetypes=[("Видео", "*.mp4 *.mov *.avi *.mkv *.MOV *.MP4"),
                       ("Барлығы", "*.*")])
        if path:
            self.video_path = path
            self.lbl_file.config(text=os.path.basename(path))
            self._set_source("file")

    def _on_conf_change(self, value: int):
        # Жұмыс кезінде де бірден әсер етеді: детектор әр кадрда
        # cfg.conf_threshold мәнін қайта оқиды.
        if self.pipeline is not None:
            self.pipeline.cfg.conf_threshold = value / 100.0

    def _on_crop_change(self, value: int):
        if self.pipeline is not None:
            self.pipeline.cfg.crop_bottom_frac = value / 100.0

    def _on_labels_change(self, value: bool):
        self.hud.show_labels = value

    def _open_portal(self):
        webbrowser.open("http://localhost:8000/portal")

    # ------------------------------------------- скриншот пен есеп ------

    @property
    def _analysed(self) -> int:
        return int(getattr(self.pipeline, "frames_analysed", 0) or 0)

    @property
    def _reports_dir(self):
        path = Config.load().path("reports")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def on_screenshot(self):
        """Ағымдағы көріністі ТОЛЫҚ сапада суретке түсіру.

        Презентацияға арналған: ақаулар белгіленген нақты кадр сақталады
        әрі сессия есебіне де қосылады.
        """
        if self._last_raw is None:
            messagebox.showinfo("AIQYN", "Алдымен тексеруді іске қосыңыз.")
            return

        image, detections = self._last_raw
        shot = self.hud.render(image.copy(), list(detections), scale=1.0)
        folder = self._reports_dir / "screens"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"AIQYN-{datetime.now():%Y%m%d-%H%M%S}.jpg"
        try:
            cv2.imwrite(str(path), shot, [int(cv2.IMWRITE_JPEG_QUALITY), 94])
        except Exception as exc:                       # noqa: BLE001
            messagebox.showerror("AIQYN", f"Скриншот сақталмады:\n{exc}")
            return

        self._screenshots.append(path)
        self.status.config(text=f"Скриншот сақталды: {path.name}", fg=BLUE)
        self.after(3500, self._restore_status)

    def _restore_status(self):
        if self.running and not self.paused:
            self.status.config(text="Жұмыс істеп тұр…", fg=GREEN)

    def _build_session_report(self):
        """Сессия аяқталғанда бір HTML есеп құру."""
        duration = time.time() - self._started_at if self._started_at else 0
        stats = {
            "date": datetime.now().strftime("%d.%m.%Y  %H:%M"),
            "source": ("Видеофайл: " + os.path.basename(self.video_path))
                      if self.source == "file" else "Телефон камерасы",
            "found": self._found,
            "sent": self._sent,
            "analysed": self._analysed,
            "duration": f"{int(duration // 60)} мин {int(duration % 60)} сек",
            "device": self._device_label,
            "settings": {
                "сенімділік шегі": f"{self.slider_conf.get()}%",
                "кадрды кесу": f"{self.slider_crop.get()}%",
                "дәлдеу режимі": "қосулы" if self.toggle_tile.get() else "өшірулі",
                "физикалық детектор": "қосулы" if self.toggle_cv.get() else "өшірулі",
            },
        }
        order = list(self._defects.values())
        try:
            path = build_report(default_report_path(Config.load().path(".")),
                                order, stats, self._screenshots)
        except Exception as exc:                       # noqa: BLE001
            log_msg = f"Есеп құрылмады: {exc}"
            self.status.config(text=log_msg, fg=YELLOW)
            return None
        self._last_report = path
        return path

    def _open_report(self):
        if self._last_report and os.path.exists(self._last_report):
            webbrowser.open(str(self._last_report))
        else:
            messagebox.showinfo(
                "AIQYN", "Есеп әзірге жоқ — алдымен тексеруді аяқтаңыз.")

    def _open_reports(self):
        path = self._reports_dir
        if hasattr(os, "startfile"):
            os.startfile(str(path))      # noqa: S606
        else:
            webbrowser.open(path.as_uri())

    def _open_help(self):
        HelpWindow(self)

    def _open_evidence(self):
        path = Config.load().evidence_path
        path.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(str(path))      # noqa: S606 — Windows жұмыс үстелі
        else:
            webbrowser.open(path.as_uri())

    # --------------------------------------------------------- әрекет ----

    def on_start(self):
        if self.running:
            return
        if self.source == "file" and not self.video_path:
            messagebox.showwarning("AIQYN", "Алдымен видеофайлды таңдаңыз.")
            return

        cfg = Config.load(
            source=self.source,
            video_path=self.video_path,
            conf_threshold=self.slider_conf.get() / 100.0,
            crop_bottom_frac=self.slider_crop.get() / 100.0,
            draw_lanes=self.toggle_lanes.get(),
            road_only=self.toggle_road.get(),
            tile_detect=self.toggle_tile.get(),
            enable_pothole_cv=self.toggle_cv.get(),
            async_detect=True,
            show_preview=False,          # көрініс осы терезеде салынады
        )

        self.running = True
        self.paused = False
        self._found = 0
        self._sent = 0
        self._rejected = 0
        self._streaming = False
        self._last_raw = None
        self._last_report = None
        self._started_at = time.time()
        self.lbl_found.config(text="0")
        self._clear_list()
        self._set_step(1)

        self.btn_start.configure_style(bg=CARD, fg=DIM, hover=CARD, enabled=False)
        self.btn_stop.configure_style(bg=ACCENT, fg="#fff", hover="#ff5238",
                                      enabled=True)
        self.btn_pause.configure_style(bg=CARD, fg=FG, hover=CARD_HI, enabled=True,
                                       text="❚❚   КІДІРТУ")
        self.btn_shot.configure_style(bg=CARD, fg=FG, hover=CARD_HI, enabled=True)
        self.btn_report.configure_style(bg=CARD, fg=DIM, hover=CARD, enabled=False)
        for button in self.src_buttons.values():
            button.configure_style(enabled=False)
        self.btn_file.configure_style(enabled=False, fg=DIM)
        self.toggle_lanes.set_enabled(False)
        self.toggle_road.set_enabled(False)
        self.toggle_tile.set_enabled(False)
        self.toggle_cv.set_enabled(False)
        self.status.config(text="Модель жүктелуде…", fg=YELLOW)

        self.worker = threading.Thread(target=self._run, args=(cfg,), daemon=True)
        self.worker.start()

    def _run(self, cfg: Config):
        """Пайплайнды құру ДА, жүргізу ДЕ осы ағында.

        YOLO салмағын жүктеу бірнеше секунд алады — оны Tk ағынында
        жасасақ, терезе сол уақытта қатып тұрар еді.
        """
        try:
            pipeline = VisionPipeline(cfg)
        except Exception as exc:                       # noqa: BLE001
            self._events.put(("error", str(exc)))
            self._events.put(("finished", ""))
            return

        pipeline.frame_callback = self._on_frame
        pipeline.event_started_callback = self._on_event_started
        pipeline.event_dropped_callback = self._on_event_dropped
        pipeline.event_callback = self._on_event_done
        self.pipeline = pipeline
        # Жүгірткіні модель жүктелгенше жылжытып үлгерген болса — қолданамыз
        pipeline.cfg.conf_threshold = self.slider_conf.get() / 100.0
        pipeline.cfg.crop_bottom_frac = self.slider_crop.get() / 100.0
        self._events.put(("ready", pipeline.detectors.device_label))

        try:
            pipeline.run()
        except Exception as exc:                       # noqa: BLE001
            self._events.put(("error", str(exc)))
        finally:
            self._events.put(("finished", ""))

    def on_pause(self):
        if not self.running or self.pipeline is None:
            return
        self.paused = not self.paused
        self.pipeline.set_paused(self.paused)
        if self.paused:
            self.btn_pause.configure_style(text="▶   ЖАЛҒАСТЫРУ")
            self.status.config(text="Кідіртілді", fg=YELLOW)
        else:
            self.btn_pause.configure_style(text="❚❚   КІДІРТУ")
            self.status.config(text="Жұмыс істеп тұр…", fg=GREEN)

    def on_stop(self):
        if not self.running or self.pipeline is None:
            return
        self.status.config(text="Тоқтатылуда — дәлелдер сақталуда…", fg=YELLOW)
        self.btn_stop.configure_style(bg=CARD, fg=DIM, hover=CARD, enabled=False)
        self.btn_pause.configure_style(bg=CARD, fg=DIM, hover=CARD, enabled=False)
        self.pipeline.set_paused(False)
        self.pipeline.request_stop()

    # ------------------------------------------------- pipeline ағыны ----
    # Бұл үш әдіс ПАЙПЛАЙН ағынында шақырылады. Tk-ға тікелей тиюге
    # болмайды, сондықтан нәтиже кезекке салынады.

    def _on_frame(self, image, detections, stats):
        """Кадрды дәл экранға керек өлшемге келтіріп, қабатты саламыз.

        Бұл жұмыс осы жерде — пайплайн ағынында — жасалады: Tk ағыны
        тек дайын суретті шығарады да, интерфейс ешқашан қатып қалмайды.
        """
        if self._frames.full():
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass

        try:
            box_w, box_h = self._display_size
            height, width = image.shape[:2]
            scale = min(box_w / float(width), box_h / float(height))
            view = cv2.resize(image, (max(1, int(width * scale)),
                                      max(1, int(height * scale))),
                              interpolation=cv2.INTER_AREA)
            view = self.hud.render(view, detections, scale=scale)
            rgb = cv2.cvtColor(view, cv2.COLOR_BGR2RGB)
            # Скриншот ТОЛЫҚ сапада жасалуы үшін шикі кадрды сақтап қоямыз
            self._last_raw = (image, list(detections))
            self._frames.put_nowait((rgb, stats))
        except Exception:                              # noqa: BLE001
            pass

    def _on_event_started(self, event_id, detection, thumb):
        self._events.put(("started", (event_id, detection, thumb)))

    def _on_event_dropped(self, event_id, reason):
        self._events.put(("dropped", (event_id, reason)))

    def _on_event_done(self, document, files):
        self._events.put(("done", document))

    # ------------------------------------------------------- Tk ағыны ----

    def _pump(self):
        try:
            rgb, stats = self._frames.get_nowait()
        except queue.Empty:
            rgb, stats = None, None

        if rgb is not None:
            if not self._streaming:
                self._streaming = True
                if self.running and not self.paused:
                    self.status.config(text="Жұмыс істеп тұр…", fg=GREEN)
            self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
            self.canvas.delete("all")
            self.canvas.create_image(
                self.canvas.winfo_width() // 2,
                self.canvas.winfo_height() // 2,
                image=self._photo, anchor="center")

            self.lbl_fps.config(text=f"{stats.fps:.0f} кадр/сек",
                                fg=FG if stats.fps >= 18 else YELLOW)
            self.lbl_detect.config(
                text=f"{stats.detect_fps:.1f} талдау/сек" if stats.detect_fps
                else "дайындалуда…", fg=FG)
            self.lbl_gps.config(text="жұмыс істеп тұр" if stats.gps_ok else "жоқ",
                                fg=GREEN if stats.gps_ok else ACCENT)
            self.lbl_portal.config(text="қосулы" if stats.portal_online else "өшік",
                                   fg=GREEN if stats.portal_online else YELLOW)
            self._draw_progress(stats.progress)

        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(kind, payload)

        self.after(33, self._pump)

    def _handle_event(self, kind, payload):
        if kind == "ready":
            # Әлі «жұмыс істеп тұр» ЕМЕС: Intel графикасы алғаш іске
            # қосылғанда ядроларын ~10 секунд жинайды. Бірінші кадр
            # келгенде ғана күйді ауыстырамыз — әйтпесе жазу өтірік болады.
            if payload:
                self.lbl_device.config(
                    text=payload.split(" (")[0],
                    fg=GREEN if "графика" in payload else FG)
            if payload:
                self._device_label = payload
            if payload and "графика" in payload:
                self.status.config(text="Intel графикасы дайындалуда…", fg=YELLOW)
            else:
                self.status.config(text="Камера ашылуда…", fg=YELLOW)

        elif kind == "started":
            event_id, detection, thumb = payload
            self._found += 1
            self.lbl_found.config(text=str(self._found))
            self._add_card(event_id, detection, thumb)
            self._set_step(2)

        elif kind == "dropped":
            event_id, reason = payload
            card = self._cards.get(event_id)
            if card is not None:
                card.set_status("rejected", reason)
            record = self._defects.get(event_id)
            if record is not None:
                record["status"] = "rejected"
                record["note"] = reason
            self._rejected += 1

        elif kind == "done":
            document = payload
            event_id = document.get("event_id", "")
            card = self._cards.get(event_id)
            self._sent += 1
            # ИИ күмәнданса да құжат құрылады, бірақ ол көрініп тұруы керек
            status = "sent" if document.get("ai_verified", True) else "doubt"
            detail = document.get("address_text", "")[:70]
            if status == "doubt":
                detail = (document.get("ai_note") or "ЖИ растамады")[:70]
            if card is not None:
                card.set_status(status, detail)
            record = self._defects.get(event_id)
            if record is not None:
                record.update(
                    status=status,
                    label=document.get("defect_type_official", record["label"]),
                    confidence=document.get("confidence", record["confidence"]),
                    address=document.get("address_text", ""),
                    coords=document.get("coords_text", ""),
                    map_link=document.get("map_link", ""),
                    note=document.get("ai_note", "") or record.get("note", ""),
                    photo=Config.load().evidence_path / f"{event_id}.jpg",
                )
            self.status.config(
                text=f"Жұмыс істеп тұр…  ·  {self._sent} құжат жіберілді",
                fg=GREEN)

        elif kind == "error":
            self.status.config(text="Қате", fg=ACCENT)
            messagebox.showerror("AIQYN", f"Қате:\n{payload}")

        elif kind == "finished":
            self._finish()

    def _add_card(self, event_id, detection, thumb):
        self.lbl_empty.pack_forget()

        photo = None
        if thumb is not None:
            try:
                rgb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
                photo = ImageTk.PhotoImage(Image.fromarray(rgb))
                self._thumbs.append(photo)
            except Exception:
                photo = None

        label = LIVE_LABELS.get(detection.class_key, detection.class_key)
        card = DefectCard(self.list_inner, event_id, label,
                          detection.confidence, photo)
        # Жаңасы әрқашан жоғарыда тұрады
        first = next(iter(self._cards.values()), None)
        if first is not None:
            card.pack(fill="x", pady=(0, 6), before=first)
        else:
            card.pack(fill="x", pady=(0, 6))
        self._cards = {event_id: card, **self._cards}

        # Есеп үшін жазба. Толық дерек (мекенжай, координата, фото)
        # құжат дайын болғанда қосылады.
        self._defects[event_id] = {
            "label": label,
            "confidence": detection.confidence,
            "status": "found",
            "time": datetime.now().strftime("%H:%M:%S"),
            "address": "", "coords": "", "map_link": "", "note": "",
            "photo": None,
        }

        # Дәлел жазылуда — клип толғанша ~8 секунд
        def mark_evidence():
            card.set_status("evidence")
            record = self._defects.get(event_id)
            if record is not None and record["status"] == "found":
                record["status"] = "evidence"

        self.after(700, mark_evidence)
        self.list_canvas.yview_moveto(0.0)

    def _clear_list(self):
        for card in self._cards.values():
            card.destroy()
        self._cards.clear()
        self._defects.clear()
        self._thumbs.clear()
        self._screenshots.clear()
        self.lbl_empty.pack(pady=14)

    def _finish(self):
        # Есепті пайплайн толық аяқталғанда құрамыз: соңғы құжаттар
        # (мекенжай + ИИ тексеруі) осы сәтке дейін келіп үлгереді.
        report = self._build_session_report()

        self.running = False
        self.paused = False
        self.btn_start.configure_style(bg=GREEN, fg="#06170d", hover="#3fdd84",
                                       enabled=True)
        self.btn_stop.configure_style(bg=CARD, fg=DIM, hover=CARD, enabled=False)
        self.btn_pause.configure_style(bg=CARD, fg=DIM, hover=CARD, enabled=False,
                                       text="❚❚   КІДІРТУ")
        self.btn_shot.configure_style(bg=CARD, fg=DIM, hover=CARD, enabled=False)
        for button in self.src_buttons.values():
            button.configure_style(enabled=True)
        self._set_source(self.source)
        self.toggle_lanes.set_enabled(True)
        self.toggle_road.set_enabled(True)
        self.toggle_tile.set_enabled(True)
        self.toggle_cv.set_enabled(True)

        if report is not None:
            self.btn_report.configure_style(bg=BLUE, fg="#04131f",
                                            hover="#63b4ff", enabled=True)
            self._set_step(3)
        else:
            self._set_step(0)

        summary = (f"Тоқтады  ·  {self._found} ақау табылды, "
                   f"{self._sent} құжат жіберілді")
        if self._rejected:
            summary += f", {self._rejected} жоққа шығарылды"
        if report is not None:
            summary += "  ·  есеп дайын"
        self.status.config(text=summary, fg=MUTED)
        self._draw_progress(0)
        self._draw_placeholder()

    # ------------------------------------------------------ тексеру -----

    def _check_services(self):
        """Телефон мен сайттың күйін фонда тексеру."""
        def probe():
            import requests
            cfg = Config.load()
            try:
                requests.get(cfg.phone_base, timeout=3).raise_for_status()
                phone = ("қосулы", GREEN)
            except Exception:
                phone = ("қосылмаған", ACCENT)
            try:
                requests.get("http://127.0.0.1:8000/api/health", timeout=3)
                portal = ("қосулы", GREEN)
            except Exception:
                portal = ("өшік", YELLOW)
            try:
                self.after(0, lambda: self._apply_probe(phone, portal))
            except RuntimeError:
                pass                     # терезе жабылып кеткен

        threading.Thread(target=probe, daemon=True).start()
        self.after(10000, self._check_services)

    def _apply_probe(self, phone, portal):
        self.lbl_phone.config(text=phone[0], fg=phone[1])
        if not self.running:
            self.lbl_portal.config(text=portal[0], fg=portal[1])

    # ------------------------------------------------------- жабылу -----

    def _on_close(self):
        _save_settings({
            "source": self.source,
            "video_path": self.video_path,
            "conf_threshold": round(self.slider_conf.get() / 100.0, 2),
            "crop_bottom_frac": round(self.slider_crop.get() / 100.0, 2),
            "draw_lanes": self.toggle_lanes.get(),
            "road_only": self.toggle_road.get(),
            "tile_detect": self.toggle_tile.get(),
            "enable_pothole_cv": self.toggle_cv.get(),
        })
        if self.running and self.pipeline is not None:
            self.pipeline.set_paused(False)
            self.pipeline.request_stop()
            if self.worker is not None:
                self.worker.join(timeout=6.0)
        self.destroy()


if __name__ == "__main__":
    AiqynApp().mainloop()
