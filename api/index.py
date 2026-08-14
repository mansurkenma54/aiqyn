# -*- coding: utf-8 -*-
"""Vercel-ге арналған кіру нүктесі.

Vercel әр сұрауды «функция» ретінде іске қосады. Бұл файл сол функцияға
AIQYN Portal қосымшасын береді.

МАҢЫЗДЫ ЕРЕКШЕЛІКТЕР:
    * Жоба қалтасы «тек оқу» режимінде — дерекқор /tmp ішінде тұрады
      (AIQYN_DB_PATH арқылы), әр суық стартта қайта құрылады.
    * Бос дерекқорға `portal/demo/snapshot.json` автоматты жүктеледі,
      сондықтан сілтемені ашқан адам әрқашан толы картаны көреді.
    * Жаңа құжат ҚАБЫЛДАНБАЙДЫ (AIQYN_READ_ONLY=1): serverless ортада
      жүктелген файлды сақтайтын жер жоқ. Далалық жұмыс ноутбуктегі
      сайтқа жүреді, ал Vercel — көрсетуге арналған айна.

Толық (жазуға болатын) нұсқа үшін Render/Railway қолданыңыз: render.yaml.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AIQYN_DB_PATH", "/tmp/aiqyn.db")
os.environ.setdefault("AIQYN_MEDIA_DIR", str(ROOT / "portal" / "demo" / "media"))
os.environ.setdefault("AIQYN_READ_ONLY", "1")

from portal.app import app  # noqa: E402

# Vercel осы айнымалыны іздейді
handler = app
