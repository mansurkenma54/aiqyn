"""RTSP/CCTV ағыны — Sergek, Кибер-Шеріф, дрон (Phase 2/3).

Бұл файл — жобаның "болашаққа дайын" екенінің НАҚТЫ дәлелі, слайдтағы
уәде емес. Код толық жұмыс істейді; жетпейтіні — ағынға ресми рұқсат.
Пилот кезеңінде тек мына команда жеткілікті:

    python -m vision.main run --source rtsp --rtsp-url rtsp://<камера>/stream

Детекция, дәлел жинау, құжат құрастыру логикасының БІР ЖОЛЫ да өзгермейді.
"""

from __future__ import annotations

from ..config import Config
from .base import ThreadedCaptureSource


class RtspSource(ThreadedCaptureSource):
    name = "rtsp"
    is_live = True

    def __init__(self, cfg: Config):
        if not cfg.rtsp_url:
            raise ValueError("--rtsp-url көрсетілмеген")
        super().__init__(url=cfg.rtsp_url, name="rtsp", reconnect=True)
        self.cfg = cfg
