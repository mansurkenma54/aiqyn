"""Telegram хабарламасы — жаңа ақау табылғанда операторға.

Не үшін: далада жүргенде немесе кеңседе отырғанда сайтты үнемі ашып
отыру ыңғайсыз. Ақау табылған сәтте телефонға фотосымен бірге хабар
келсе, оператор бірден көреді.

Толығымен тегін (Telegram Bot API). Кілт болмаса — үнсіз өшірулі.

Баптау (.env):
    AIQYN_TELEGRAM_TOKEN=...
    AIQYN_TELEGRAM_CHAT_ID=...
    AIQYN_TELEGRAM_MIN_SEVERITY=medium
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("portal.notify")

API = "https://api.telegram.org/bot{token}/{method}"
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}
SEVERITY_EMOJI = {"low": "🔵", "medium": "🟡", "high": "🔴"}


class TelegramNotifier:
    def __init__(self):
        self._session = requests.Session()
        self._failures = 0

    # Кілттер ӘР ЖОЛЫ os.environ-нан оқылады: .env файлы бұл модуль
    # импортталғаннан кейін жүктелуі мүмкін, сондықтан оларды
    # __init__ ішінде бір рет ұстап қалуға болмайды.

    @property
    def token(self) -> str:
        return os.getenv("AIQYN_TELEGRAM_TOKEN", "").strip()

    @property
    def chat_id(self) -> str:
        return os.getenv("AIQYN_TELEGRAM_CHAT_ID", "").strip()

    @property
    def min_severity(self) -> str:
        return os.getenv("AIQYN_TELEGRAM_MIN_SEVERITY", "medium").strip().lower()

    @property
    def portal_url(self) -> str:
        return os.getenv("AIQYN_PORTAL_PUBLIC_URL", "http://localhost:8000")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id and self._failures < 5)

    def _passes_filter(self, severity: str) -> bool:
        threshold = SEVERITY_ORDER.get(self.min_severity, 1)
        return SEVERITY_ORDER.get((severity or "low").lower(), 0) >= threshold

    # ---------- жіберу ----------

    def notify_new_document(self, document: dict, photo_path: Optional[Path]) -> None:
        """Жаңа ақау туралы хабарлау (фондық ағында)."""
        if not self.enabled:
            return
        if not self._passes_filter(document.get("severity", "")):
            return

        thread = threading.Thread(
            target=self._send, args=(document, photo_path),
            name="telegram", daemon=True,
        )
        thread.start()

    def _build_caption(self, document: dict) -> str:
        severity = (document.get("severity") or "low").lower()
        emoji = SEVERITY_EMOJI.get(severity, "⚪")

        lines = [
            f"{emoji} <b>{document.get('defect_type_official', 'Ақау')}</b>",
            "",
            f"📍 {document.get('address_text', '—')}",
            f"🕓 {document.get('timestamp_human', '—')}",
            f"🎯 Сенімділік: {float(document.get('confidence') or 0) * 100:.0f}%",
        ]

        if document.get("ai_verified"):
            lines.append(
                f"🤖 ИИ растады ({float(document.get('ai_confidence') or 0) * 100:.0f}%)"
            )

        if document.get("video_seconds"):
            lines.append(f"🎬 Видео дәлел: {document['video_seconds']} сек")

        lines.append("")
        lines.append(f"🗺 {document.get('map_link', '')}")
        lines.append(f"✅ Тексеру: {self.portal_url}")

        return "\n".join(lines)

    def _send(self, document: dict, photo_path: Optional[Path]) -> None:
        caption = self._build_caption(document)

        try:
            if photo_path and Path(photo_path).exists():
                with open(photo_path, "rb") as handle:
                    response = self._session.post(
                        API.format(token=self.token, method="sendPhoto"),
                        data={
                            "chat_id": self.chat_id,
                            "caption": caption[:1024],
                            "parse_mode": "HTML",
                        },
                        files={"photo": handle},
                        timeout=25,
                    )
            else:
                response = self._session.post(
                    API.format(token=self.token, method="sendMessage"),
                    data={
                        "chat_id": self.chat_id,
                        "text": caption,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=15,
                )

            if response.status_code >= 400:
                self._failures += 1
                log.warning("Telegram қатесі (%s): %s",
                            response.status_code, response.text[:200])
                return

            self._failures = 0
            log.info("Telegram хабары жіберілді: %s", document.get("event_id"))

        except requests.RequestException as exc:
            self._failures += 1
            log.warning("Telegram-ға жіберілмеді: %s", exc)

    # ---------- тексеру ----------

    def check(self) -> tuple[bool, str]:
        """doctor үшін: бот жұмыс істей ме."""
        if not self.token:
            return False, "AIQYN_TELEGRAM_TOKEN қойылмаған"
        if not self.chat_id:
            return False, "AIQYN_TELEGRAM_CHAT_ID қойылмаған"
        try:
            response = self._session.get(
                API.format(token=self.token, method="getMe"), timeout=10
            )
            data = response.json()
            if not data.get("ok"):
                return False, str(data.get("description", "белгісіз қате"))
            return True, "@" + data["result"].get("username", "bot")
        except requests.RequestException as exc:
            return False, str(exc)


notifier = TelegramNotifier()
