"""`.env` файлынан құпия кілттерді оқу.

Неге бөлек файл: API кілттерін кодқа да, config.json-ға да жазуға болмайды —
жоба GitHub-қа шыққанда кілт ашылып қалады. `.env` файлы `.gitignore`-да тұр.

Пайдалану:
    aiqyn/.env  файлын жасап, ішіне жазыңыз:
        AIQYN_AI_API_KEY=xxxxx
        AIQYN_TELEGRAM_TOKEN=xxxxx
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def load_env(path: str | Path | None = None) -> dict[str, str]:
    """`.env` файлын оқып, os.environ ішіне қосу.

    Бұрыннан бар айнымалылар БАСЫП ЖАЗЫЛМАЙДЫ — жүйелік баптау
    әрқашан басым болады.
    """
    env_path = Path(path) if path else (Path(__file__).resolve().parent.parent / ".env")
    values: dict[str, str] = {}

    if not env_path.exists():
        return values

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if not key:
                continue

            values[key] = value
            os.environ.setdefault(key, value)

        if values:
            log.info(".env жүктелді: %d айнымалы", len(values))

    except Exception as exc:
        log.warning(".env оқылмады (%s): %s", env_path, exc)

    return values


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()
