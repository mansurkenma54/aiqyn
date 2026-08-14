# -*- coding: utf-8 -*-
"""AIQYN — ноутбуктегі негізгі бағдарлама.

Скрипт емес, түймелері бар қалыпты қолданба:
    * үлкен ІСКЕ ҚОСУ / ТОҚТАТУ түймелері
    * камераның тірі көрінісі терезенің ішінде
    * оң жақта: телефон, GPS, сайт күйі және табылған ақаулар тізімі
    * баптау: сенімділік шегі, кадрды қию, дереккөз (телефон / видеофайл)

Іске қосу:  AIQYN.bat   немесе   python app.py
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vision.config import Config          # noqa: E402
from vision.pipeline import VisionPipeline  # noqa: E402

# --- түстер (логотиппен үйлесімді) ---
BG = "#0b0b0c"
PANEL = "#151519"
CARD = "#1c1c21"
LINE = "#2a2a30"
FG = "#f0f0f2"
MUTED = "#8b8b93"
ACCENT = "#ff3b1f"
GREEN = "#2ecc71"
YELLOW = "#ffc400"

VIDEO_W, VIDEO_H = 860, 484


class AiqynApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AIQYN — жол ақауын автоматты анықтау")
        self.configure(bg=BG)
        self.minsize(1240, 700)

        self.pipeline: VisionPipeline | None = None
        self.worker: threading.Thread | None = None
        self.running = False
        self._frames: queue.Queue = queue.Queue(maxsize=2)
        self._events: queue.Queue = queue.Queue()
        self._photo = None
        self._found = 0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(40, self._pump)
        self.after(300, self._check_services)

    # ------------------------------------------------------------- UI ---
    def _build_ui(self):
        header = tk.Frame(self, bg="#0a0a0b", height=58)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="AIQYN", bg="#0a0a0b", fg=FG,
                 font=("Segoe UI", 18, "bold")).pack(side="left", padx=(18, 6))
        tk.Label(header, text="●", bg="#0a0a0b", fg=ACCENT,
                 font=("Segoe UI", 12)).pack(side="left")
        tk.Label(header, text="  жол ақауын автоматты анықтау · Шымкент",
                 bg="#0a0a0b", fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left")

        tk.Button(header, text="Сайтты ашу", command=self._open_portal,
                  bg=CARD, fg=FG, relief="flat", padx=14, pady=6,
                  activebackground=LINE,
                  font=("Segoe UI", 9)).pack(side="right", padx=16)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        # --- сол жақ: бейне
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(left, width=VIDEO_W, height=VIDEO_H, bg="#000",
                                highlightthickness=1, highlightbackground=LINE)
        self.canvas.pack()
        self.canvas.create_text(VIDEO_W // 2, VIDEO_H // 2,
                                text="«ІСКЕ ҚОСУ» түймесін басыңыз",
                                fill="#44444c", font=("Segoe UI", 15))

        controls = tk.Frame(left, bg=BG)
        controls.pack(fill="x", pady=(12, 0))

        self.btn_start = tk.Button(
            controls, text="▶   ІСКЕ ҚОСУ", command=self.on_start,
            bg=GREEN, fg="#08120c", relief="flat", padx=26, pady=13,
            font=("Segoe UI", 12, "bold"), activebackground="#25b263")
        self.btn_start.pack(side="left")

        self.btn_stop = tk.Button(
            controls, text="■   ТОҚТАТУ", command=self.on_stop,
            bg=CARD, fg=MUTED, relief="flat", padx=26, pady=13,
            font=("Segoe UI", 12, "bold"), state="disabled",
            activebackground=ACCENT)
        self.btn_stop.pack(side="left", padx=10)

        self.status = tk.Label(controls, text="Дайын", bg=BG, fg=MUTED,
                               font=("Segoe UI", 10))
        self.status.pack(side="left", padx=16)

        # --- оң жақ: панель
        right = tk.Frame(body, bg=PANEL, width=330)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        self._section(right, "ЖҮЙЕ КҮЙІ")
        self.lbl_phone = self._row(right, "Телефон", "тексерілуде…")
        self.lbl_gps = self._row(right, "GPS", "—")
        self.lbl_portal = self._row(right, "Сайт", "—")
        self.lbl_fps = self._row(right, "Жылдамдық", "—")

        self._section(right, "ДЕРЕККӨЗ")
        self.source_var = tk.StringVar(value="phone")
        source_row = tk.Frame(right, bg=PANEL)
        source_row.pack(fill="x", padx=16, pady=(0, 6))
        for value, text in (("phone", "Телефон камерасы"), ("file", "Видеофайл")):
            tk.Radiobutton(source_row, text=text, value=value,
                           variable=self.source_var, command=self._on_source,
                           bg=PANEL, fg=FG, selectcolor=CARD, activebackground=PANEL,
                           activeforeground=FG, relief="flat", bd=0,
                           highlightthickness=0,
                           font=("Segoe UI", 9)).pack(anchor="w")

        self.btn_file = tk.Button(right, text="Видеофайлды таңдау…",
                                  command=self._pick_file, bg=CARD, fg=FG,
                                  relief="flat", pady=6, state="disabled",
                                  activebackground=LINE, font=("Segoe UI", 9))
        self.btn_file.pack(fill="x", padx=16, pady=(2, 4))
        self.lbl_file = tk.Label(right, text="", bg=PANEL, fg=MUTED,
                                 font=("Segoe UI", 8), wraplength=290,
                                 justify="left")
        self.lbl_file.pack(fill="x", padx=16)
        self.video_path = ""

        self._section(right, "БАПТАУ")
        self.conf = self._slider(right, "Сенімділік шегі", 45, 20, 90,
                                 "аз тапса — азайтыңыз")
        self.crop = self._slider(right, "Кадрдың төменін кесу, %", 30, 0, 50,
                                 "көліктің панелін кесу")

        self._section(right, "ТАБЫЛҒАН АҚАУЛАР")
        self.lbl_found = tk.Label(right, text="0", bg=PANEL, fg=ACCENT,
                                  font=("Segoe UI", 26, "bold"))
        self.lbl_found.pack(anchor="w", padx=16)

        list_wrap = tk.Frame(right, bg=CARD)
        list_wrap.pack(fill="both", expand=True, padx=16, pady=(6, 16))
        self.listbox = tk.Listbox(
            list_wrap, bg=CARD, fg=FG, relief="flat", bd=0,
            highlightthickness=0, selectbackground=LINE,
            font=("Segoe UI", 9), activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=8, pady=8)

    def _section(self, parent, text):
        tk.Label(parent, text=text, bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=16, pady=(16, 6))

    def _row(self, parent, key, value):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", padx=16, pady=1)
        tk.Label(row, text=key, bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        label = tk.Label(row, text=value, bg=PANEL, fg=FG,
                         font=("Segoe UI", 9, "bold"))
        label.pack(side="right")
        return label

    def _slider(self, parent, text, default, low, high, hint):
        wrap = tk.Frame(parent, bg=PANEL)
        wrap.pack(fill="x", padx=16, pady=(4, 0))
        head = tk.Frame(wrap, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text=text, bg=PANEL, fg=FG,
                 font=("Segoe UI", 9)).pack(side="left")
        value_label = tk.Label(head, text=str(default), bg=PANEL, fg=ACCENT,
                               font=("Segoe UI", 9, "bold"))
        value_label.pack(side="right")
        var = tk.IntVar(value=default)
        ttk.Scale(wrap, from_=low, to=high, variable=var,
                  command=lambda _v: value_label.config(text=str(var.get()))
                  ).pack(fill="x")
        tk.Label(wrap, text=hint, bg=PANEL, fg="#5f5f68",
                 font=("Segoe UI", 8)).pack(anchor="w")
        return var

    # --------------------------------------------------------- әрекет ---
    def _on_source(self):
        is_file = self.source_var.get() == "file"
        self.btn_file.config(state="normal" if is_file else "disabled")

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Видеофайлды таңдаңыз",
            filetypes=[("Видео", "*.mp4 *.mov *.avi *.mkv"), ("Барлығы", "*.*")])
        if path:
            self.video_path = path
            self.lbl_file.config(text=os.path.basename(path))

    def _open_portal(self):
        webbrowser.open("http://localhost:8000/portal")

    def on_start(self):
        if self.running:
            return
        if self.source_var.get() == "file" and not self.video_path:
            messagebox.showwarning("AIQYN", "Алдымен видеофайлды таңдаңыз.")
            return

        cfg = Config.load(
            source=self.source_var.get(),
            video_path=self.video_path,
            conf_threshold=self.conf.get() / 100.0,
            crop_bottom_frac=self.crop.get() / 100.0,
            show_preview=False,          # көрініс осы терезеде салынады
        )

        try:
            self.pipeline = VisionPipeline(cfg)
        except Exception as exc:
            messagebox.showerror("AIQYN", f"Іске қосу мүмкін болмады:\n{exc}")
            return

        self.pipeline.frame_callback = self._on_frame
        self.pipeline.event_callback = self._on_event

        self.running = True
        self._found = 0
        self.lbl_found.config(text="0")
        self.listbox.delete(0, "end")
        self.btn_start.config(state="disabled", bg=CARD, fg=MUTED)
        self.btn_stop.config(state="normal", bg=ACCENT, fg="#fff")
        self.status.config(text="Жұмыс істеп тұр…", fg=GREEN)

        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _run(self):
        try:
            self.pipeline.run()
        except Exception as exc:                       # noqa: BLE001
            self._events.put(("error", str(exc)))
        finally:
            self._events.put(("finished", ""))

    def on_stop(self):
        if not self.running or self.pipeline is None:
            return
        self.status.config(text="Тоқтатылуда…", fg=YELLOW)
        self.btn_stop.config(state="disabled", bg=CARD, fg=MUTED)
        self.pipeline.request_stop()

    # ------------------------------------------------------ ағындар -----
    def _on_frame(self, canvas, stats):
        """Қозғалтқыш ағынынан келеді — тек кезекке саламыз."""
        if self._frames.full():
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
        try:
            self._frames.put_nowait((canvas, stats))
        except queue.Full:
            pass

    def _on_event(self, document, files):
        self._events.put(("found", document))

    def _pump(self):
        """Tk ағынында: кадрды экранға шығару және оқиғаларды өңдеу."""
        try:
            canvas, stats = self._frames.get_nowait()
        except queue.Empty:
            canvas, stats = None, None

        if canvas is not None:
            height, width = canvas.shape[:2]
            scale = min(VIDEO_W / width, VIDEO_H / height)
            resized = cv2.resize(canvas, (int(width * scale), int(height * scale)),
                                 interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
            self.canvas.delete("all")
            self.canvas.create_image(VIDEO_W // 2, VIDEO_H // 2,
                                     image=self._photo, anchor="center")
            if stats is not None:
                self.lbl_fps.config(text=f"{stats.fps:.0f} кадр/сек")
                self.lbl_gps.config(
                    text="жұмыс істеп тұр" if stats.gps_ok else "жоқ",
                    fg=GREEN if stats.gps_ok else ACCENT)
                self.lbl_portal.config(
                    text="қосулы" if stats.portal_online else "өшік",
                    fg=GREEN if stats.portal_online else YELLOW)

        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break

            if kind == "found":
                self._found += 1
                self.lbl_found.config(text=str(self._found))
                self.listbox.insert(
                    0, f"{payload.get('timestamp_human', '')[-8:]}  "
                       f"{payload.get('defect_type_official', '')}")
                self.listbox.insert(
                    1, f"      {payload.get('address_text', '')[:44]}")
            elif kind == "error":
                messagebox.showerror("AIQYN", f"Қате:\n{payload}")
            elif kind == "finished":
                self._finish()

        self.after(33, self._pump)

    def _finish(self):
        self.running = False
        self.btn_start.config(state="normal", bg=GREEN, fg="#08120c")
        self.btn_stop.config(state="disabled", bg=CARD, fg=MUTED)
        self.status.config(
            text=f"Тоқтады · {self._found} ақау табылды", fg=MUTED)

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
            self.after(0, lambda: (
                self.lbl_phone.config(text=phone[0], fg=phone[1]),
                self.lbl_portal.config(text=portal[0], fg=portal[1])))

        threading.Thread(target=probe, daemon=True).start()
        if not self.running:
            self.after(8000, self._check_services)

    def _on_close(self):
        if self.running and self.pipeline is not None:
            self.pipeline.request_stop()
            time.sleep(0.6)
        self.destroy()


if __name__ == "__main__":
    AiqynApp().mainloop()
