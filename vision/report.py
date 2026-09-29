# -*- coding: utf-8 -*-
"""Сессия есебі — презентацияға арналған БІР файл.

Не үшін керек: тексеру аяқталғанда «не таптық?» деген сұраққа жауап
беретін дайын құжат болуы керек. Журналды қарау да, evidence қалтасын
ақтару да презентацияда жарамайды.

Нәтижесі: `reports/AIQYN-есеп-<күні>.html` — ӨЗІ ЖЕТКІЛІКТІ бір файл.
Суреттер файлдың ішіне base64 болып кіреді, сондықтан оны флешкамен де,
поштамен де жіберуге болады, кез келген компьютерде ашылады.
"""

from __future__ import annotations

import base64
import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

STATUS_LABEL = {
    "sent": ("Құжат жіберілді", "#2ecc71"),
    "doubt": ("Жіберілді · ЖИ күмәнданды", "#ffc400"),
    "evidence": ("Дәлел жазылды", "#3d9bff"),
    "found": ("Ақау табылды", "#ffc400"),
    "rejected": ("ЖИ жоққа шығарды", "#8a8a93"),
}


def _b64_image(path: Optional[Path], max_width: int = 1400,
               max_bytes: int = 900_000) -> str:
    """Суретті HTML ішіне енгізуге дайындау.

    Үлкен сурет кездессе — оны АЛЫП ТАСТАМАЙМЫЗ, кішірейтеміз. Есеп
    презентацияға арналған: суреті жоқ есеп мағынасын жоғалтады. Әрі
    файл поштамен жіберілетіндей жеңіл болуы керек (1080p скриншот
    base64-те 2,9 МБ орын алады).
    """
    if not path:
        return ""
    try:
        path = Path(path)
        if not path.exists():
            return ""
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            import cv2                      # тек қажет болғанда
            image = cv2.imread(str(path))
            if image is None:
                return ""
            if image.shape[1] > max_width:
                scale = max_width / float(image.shape[1])
                image = cv2.resize(
                    image, (max_width, max(1, int(image.shape[0] * scale))),
                    interpolation=cv2.INTER_AREA)
            ok, buffer = cv2.imencode(
                ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                return ""
            raw = buffer.tobytes()
        return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    except Exception as exc:
        log.debug("Суретті есепке қосу сәтсіз (%s): %s", path, exc)
        return ""


CSS = """
:root{--bg:#0e0e11;--card:#17171c;--line:#26262e;--fg:#f2f2f4;
      --muted:#9a9aa4;--accent:#ff3b1f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:15px/1.55 "Segoe UI",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
       border-bottom:1px solid var(--line);padding-bottom:18px}
h1{font-size:26px;margin:0;letter-spacing:.5px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--accent);
     display:inline-block}
.sub{color:var(--muted);font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
      gap:12px;margin:24px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;
      padding:14px 16px}
.stat b{display:block;font-size:26px;line-height:1.2}
.stat span{color:var(--muted);font-size:12px;text-transform:uppercase;
           letter-spacing:.6px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:1px;
   color:var(--muted);margin:34px 0 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      overflow:hidden;margin-bottom:16px;display:flex;flex-wrap:wrap}
.card img{width:340px;max-width:100%;object-fit:cover;display:block;
          background:#000}
.body{padding:16px 18px;flex:1;min-width:260px}
.title{font-size:17px;font-weight:600;margin:0 0 4px}
.chip{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;
      font-weight:600;color:#0b0b0c}
table{border-collapse:collapse;width:100%;margin-top:10px;font-size:13.5px}
td{padding:4px 0;vertical-align:top}
td:first-child{color:var(--muted);width:150px;white-space:nowrap}
a{color:#5aa9ff}
.settings{background:var(--card);border:1px solid var(--line);
          border-radius:10px;padding:14px 18px;font-size:13.5px}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:12px;
       padding:38px;text-align:center;color:var(--muted)}
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);
       color:var(--muted);font-size:12.5px}
@media print{body{background:#fff;color:#111}
  .card,.stat,.settings{background:#fff;border-color:#ccc}
  h2,.sub,td:first-child,footer{color:#555}}
"""


def build_report(
    path: Path,
    defects: list[dict],
    stats: dict,
    screenshots: Optional[list[Path]] = None,
) -> Path:
    """Сессия есебін HTML файлға жазып, оның жолын қайтарады.

    defects — әр ақау туралы сөздік:
        label, confidence, status, time, address, coords, map_link,
        photo (Path), note
    stats — жалпы көрсеткіштер (сөздік)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    esc = html.escape

    parts: list[str] = [
        "<meta charset='utf-8'>",
        f"<title>AIQYN есебі — {esc(stats.get('date', ''))}</title>",
        f"<style>{CSS}</style>",
        "<div class='wrap'>",
        "<header><span class='dot'></span><h1>AIQYN</h1>",
        "<span class='sub'>жол ақауын автоматты анықтау · Шымкент</span></header>",
        f"<p class='sub'>Тексеру: <b>{esc(stats.get('date', ''))}</b> · "
        f"дереккөз: {esc(stats.get('source', ''))}</p>",
    ]

    cards = [
        ("Табылған ақау", stats.get("found", 0)),
        ("Құжат жіберілді", stats.get("sent", 0)),
        ("Талданған кадр", stats.get("analysed", 0)),
        ("Ұзақтығы", stats.get("duration", "—")),
        ("Есептеу", stats.get("device", "—")),
    ]
    parts.append("<div class='grid'>")
    for label, value in cards:
        parts.append(
            f"<div class='stat'><b>{esc(str(value))}</b><span>{esc(label)}</span></div>"
        )
    parts.append("</div>")

    parts.append("<h2>Табылған ақаулар</h2>")
    if not defects:
        parts.append("<div class='empty'>Бұл тексеруде ақау табылмады.</div>")

    for index, defect in enumerate(defects, start=1):
        status_text, status_color = STATUS_LABEL.get(
            defect.get("status", "found"), STATUS_LABEL["found"]
        )
        image = _b64_image(defect.get("photo"))
        parts.append("<div class='card'>")
        if image:
            parts.append(f"<img src='{image}' alt='ақау {index}'>")
        parts.append("<div class='body'>")
        parts.append(
            f"<p class='title'>{index}. {esc(str(defect.get('label', '—')))}</p>"
        )
        parts.append(
            f"<span class='chip' style='background:{status_color}'>{esc(status_text)}</span>"
        )
        parts.append("<table>")
        rows = [
            ("Сенімділік", f"{float(defect.get('confidence', 0)) * 100:.0f}%"),
            ("Уақыты", defect.get("time", "")),
            ("Мекенжайы", defect.get("address", "")),
            ("Координата", defect.get("coords", "")),
            ("Ескерту", defect.get("note", "")),
        ]
        for key, value in rows:
            if not value:
                continue
            parts.append(f"<tr><td>{esc(key)}</td><td>{esc(str(value))}</td></tr>")
        link = defect.get("map_link")
        if link:
            parts.append(
                f"<tr><td>Картада</td><td><a href='{esc(link)}'>ашу</a></td></tr>"
            )
        parts.append("</table></div></div>")

    # Алдымен суреттерді дайындаймыз — біреуі де шықпаса, тақырып та
    # жазылмауы керек (бос бөлім есепті шатастырады)
    shots = [img for img in (_b64_image(Path(s)) for s in (screenshots or []))
             if img]
    if shots:
        parts.append("<h2>Қолмен түсірілген скриншоттар</h2>")
        for image in shots:
            parts.append(
                f"<div class='card'><img src='{image}' "
                f"style='width:100%;max-width:100%'></div>"
            )

    settings = stats.get("settings") or {}
    if settings:
        parts.append("<h2>Қолданылған баптау</h2><div class='settings'>")
        parts.append(" · ".join(f"{esc(k)}: <b>{esc(str(v))}</b>"
                                for k, v in settings.items()))
        parts.append("</div>")

    parts.append(
        "<footer>Бұл есеп AIQYN бағдарламасымен автоматты құрылды. "
        "Суреттер файлдың ішінде — интернетсіз де ашылады.</footer></div>"
    )

    path.write_text("\n".join(parts), encoding="utf-8")
    log.info("Есеп дайын: %s", path)
    return path


def default_report_path(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(root) / "reports" / f"AIQYN-esep-{stamp}.html"
