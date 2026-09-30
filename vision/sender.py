"""Дайын құжатты AIQYN Portal сайтына жіберу.

Сенімділік ережесі: сайт өшік болса да ЕШТЕҢЕ ЖОҒАЛМАЙДЫ. Жіберілмеген
құжат outbox/ қалтасына жазылады және фондық ағын оны әр 30 секунд сайын
қайта жіберіп көреді. Далалық тестте ноутбукта интернет үзіліп қалуы
қалыпты жағдай — сол себепті бұл қажет.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from .config import Config

log = logging.getLogger(__name__)


class PortalSender:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.endpoint = cfg.portal_url.rstrip("/") + "/api/documents"
        self.outbox = cfg.outbox_path
        self.outbox.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._retry_thread: Optional[threading.Thread] = None

        self.sent_count = 0
        self.queued_count = 0

    # ---------- жіберу ----------

    def send(self, document: dict, photo: Optional[Path], video: Optional[Path]) -> bool:
        if not self.cfg.send_enabled:
            log.info("Жіберу өшірулі (--no-send) — құжат кезекке қойылды: %s",
                     document["event_id"])
            self._queue(document, photo, video)
            return False

        ok = self._post(document, photo, video)
        if ok:
            self.sent_count += 1
            log.info("Құжат сайтқа жіберілді: %s", document["event_id"])
        else:
            self._queue(document, photo, video)
        return ok

    def _post(self, document: dict, photo: Optional[Path], video: Optional[Path]) -> bool:
        files = []
        handles = []
        try:
            if photo and Path(photo).exists():
                fh = open(photo, "rb")
                handles.append(fh)
                files.append(("photo", (Path(photo).name, fh, "image/jpeg")))
            if video and Path(video).exists():
                fh = open(video, "rb")
                handles.append(fh)
                files.append(("video_clip", (Path(video).name, fh, "video/mp4")))

            # Күрделі мәндерді (dict) JSON-жол ретінде жібереміз —
            # multipart/form-data тек мәтін мен файлды түсінеді
            data = {}
            for key, value in document.items():
                if isinstance(value, (dict, list)):
                    data[key] = json.dumps(value, ensure_ascii=False)
                elif isinstance(value, bool):
                    data[key] = "true" if value else "false"
                else:
                    data[key] = str(value)

            token = os.getenv("AIQYN_INGEST_TOKEN", "").strip()
            response = self._session.post(
                self.endpoint, data=data, files=files, timeout=self.cfg.send_timeout,
                headers={"X-AIQYN-Token": token} if token else None,
            )
            if response.status_code >= 400:
                log.warning("Сайт қатемен жауап берді (%s): %s",
                            response.status_code, response.text[:200])
                return False
            return True

        except requests.RequestException as exc:
            log.warning("Сайтқа жіберілмеді (%s): %s", self.endpoint, exc)
            return False
        except Exception as exc:
            log.exception("Жіберу кезінде күтпеген қате: %s", exc)
            return False
        finally:
            for fh in handles:
                try:
                    fh.close()
                except Exception:
                    pass

    # ---------- кезек ----------

    def _queue(self, document: dict, photo: Optional[Path], video: Optional[Path]) -> None:
        """Жіберілмеген құжатты дискіге сақтау."""
        event_id = document["event_id"]
        folder = self.outbox / event_id
        folder.mkdir(parents=True, exist_ok=True)

        try:
            if photo and Path(photo).exists():
                shutil.copy2(photo, folder / Path(photo).name)
                document["_photo_file"] = Path(photo).name
            if video and Path(video).exists():
                shutil.copy2(video, folder / Path(video).name)
                document["_video_file"] = Path(video).name

            (folder / "document.json").write_text(
                json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.queued_count += 1
            log.info("Құжат кезекке қойылды (outbox): %s", event_id)
        except Exception as exc:
            log.exception("Кезекке қою сәтсіз (%s): %s", event_id, exc)

    def flush_outbox(self) -> int:
        """Кезектегі құжаттарды қайта жіберіп көру. Жіберілген санын қайтарады."""
        if not self.cfg.send_enabled:
            return 0

        sent = 0
        for folder in sorted(self.outbox.iterdir()):
            if not folder.is_dir():
                continue
            doc_file = folder / "document.json"
            if not doc_file.exists():
                continue

            try:
                document = json.loads(doc_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            photo_name = document.pop("_photo_file", None)
            video_name = document.pop("_video_file", None)
            photo = folder / photo_name if photo_name else None
            video = folder / video_name if video_name else None

            if self._post(document, photo, video):
                sent += 1
                self.sent_count += 1
                self.queued_count = max(0, self.queued_count - 1)
                shutil.rmtree(folder, ignore_errors=True)
                log.info("Кезектегі құжат жіберілді: %s", document.get("event_id"))

        return sent

    # ---------- фондық қайта жіберу ----------

    def start_retry_loop(self, interval: float = 30.0) -> None:
        if self._retry_thread and self._retry_thread.is_alive():
            return

        def loop():
            while not self._stop.wait(interval):
                try:
                    self.flush_outbox()
                except Exception as exc:
                    log.warning("Кезекті жіберу циклінің қатесі: %s", exc)

        self._stop.clear()
        self._retry_thread = threading.Thread(target=loop, name="outbox-retry", daemon=True)
        self._retry_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._retry_thread:
            self._retry_thread.join(timeout=2.0)

    # ---------- диагностика ----------

    def ping(self) -> bool:
        try:
            response = self._session.get(
                self.cfg.portal_url.rstrip("/") + "/api/health", timeout=4.0
            )
            return response.status_code < 500
        except requests.RequestException:
            return False

    def pending_count(self) -> int:
        return sum(1 for p in self.outbox.iterdir() if p.is_dir() and (p / "document.json").exists())
