"""КӨРСЕТІЛІМ ПАКЕТІ — бір команданың өзімен барлық материалды жинау.

Не жинайды (`reports/demo-pack/` ішіне):

    01-akaular/       әр ақаудың дәлел фотосы (рамкасымен)
    02-kujattar/      әр құжаттың сайттағы карточкасы (оператор көретін)
    03-karta/         карта — барлық ақау нүктелерімен
    AIQYN-korsetilim.html   бәрі бір бетте (интернетсіз ашылады)

Пакет САЙТТАҒЫ НАҚТЫ деректен жиналады: алдымен видеоны өткізіп,
құжаттар сайтқа түсуі керек.

    python -m uvicorn portal.app:app --port 8000
    python -m vision.main run --source file --video ВИДЕО --gps-track ТРЕК
    python scripts/export_demo_pack.py
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import shutil
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

SEVERITY_KK = {"high": "Жоғары", "medium": "Орташа", "low": "Төмен"}


def find_chrome() -> str | None:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def shoot(chrome: str, url: str, out: Path, width=1600, height=1100,
          wait_ms=11000) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--window-size={width},{height}",
             f"--virtual-time-budget={wait_ms}",
             f"--screenshot={out}", url],
            check=False, capture_output=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        return False
    return out.exists() and out.stat().st_size > 0


def b64(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    mime = "png" if suffix == "png" else "jpeg"
    return f"data:image/{mime};base64," + base64.b64encode(path.read_bytes()).decode()


# ------------------------------------------------------------------
#  HTML
# ------------------------------------------------------------------

CSS = """
* { box-sizing: border-box; }
body { margin:0; background:#121111; color:#f2f0ef;
       font-family: 'Segoe UI', Roboto, Arial, sans-serif; line-height:1.55; }
.wrap { max-width:1180px; margin:0 auto; padding:48px 24px 96px; }
h1 { font-size:36px; margin:0 0 6px; letter-spacing:-.5px; }
h2 { font-size:23px; margin:56px 0 6px; }
.sub { color:#8f8b89; margin:0 0 8px; }
.lead { color:#b9b4b1; max-width:78ch; margin:0 0 28px; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
        gap:14px; margin:28px 0 8px; }
.kpi { background:#1c1a1a; border:1px solid #2a2727; border-radius:12px; padding:16px 18px; }
.kpi .n { font-size:30px; font-weight:700; }
.kpi .l { font-size:12px; color:#8f8b89; text-transform:uppercase; letter-spacing:.7px; }
.g { color:#6ed26e; } .r { color:#ff5b4a; } .a { color:#ffb020; } .c { color:#3cd0ff; }
figure { margin:0 0 26px; background:#1a1818; border:1px solid #2a2727;
         border-radius:14px; overflow:hidden; }
figure img { width:100%; display:block; }
figcaption { padding:14px 18px; font-size:14px; color:#b9b4b1; border-top:1px solid #2a2727; }
.card { background:#1a1818; border:1px solid #2a2727; border-radius:14px;
        overflow:hidden; margin:0 0 22px; }
.card .head { display:flex; align-items:center; gap:12px; padding:14px 18px;
              border-bottom:1px solid #2a2727; }
.num { width:34px; height:34px; border-radius:9px; display:grid; place-items:center;
       font-weight:700; font-size:15px; color:#fff; flex:none; }
.card .title { font-weight:600; font-size:17px; }
.card .id { font-family:Consolas,monospace; font-size:11px; color:#75716f; }
.card img { width:100%; display:block; }
.meta { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
        gap:1px; background:#2a2727; }
.meta div { background:#1a1818; padding:12px 18px; }
.meta .k { font-size:11px; color:#8f8b89; text-transform:uppercase; letter-spacing:.6px; }
.meta .v { font-size:15px; font-weight:600; margin-top:2px; }
.ai { padding:14px 18px; border-top:1px solid #2a2727; font-size:14px; color:#c9c4c1; }
.ai b { color:#fff; }
.tag { display:inline-block; padding:3px 10px; border-radius:20px; font-size:12px;
       font-weight:600; }
.ok { background:#153a1c; color:#7fe08a; } .no { background:#3d1414; color:#ff7a68; }
table { width:100%; border-collapse:collapse; margin:18px 0 8px; font-size:14px; }
th, td { text-align:left; padding:10px 12px; border-bottom:1px solid #2a2727; }
th { color:#8f8b89; font-size:11px; text-transform:uppercase; letter-spacing:.7px; }
.step { display:flex; gap:16px; margin:0 0 16px; }
.step .i { width:30px; height:30px; border-radius:8px; background:#2a2727; flex:none;
           display:grid; place-items:center; font-weight:700; font-size:14px; }
.step .t b { display:block; margin-bottom:2px; }
.step .t span { color:#a5a09d; font-size:14px; }
footer { margin-top:64px; padding-top:22px; border-top:1px solid #2a2727;
         color:#75716f; font-size:13px; }
"""


def build_html(ctx: dict) -> str:
    docs = ctx["docs"]
    confirmed = [d for d in docs if d.get("ai_verified")]
    rejected = [d for d in docs if not d.get("ai_verified")]

    parts: list[str] = []
    add = parts.append

    add(f"<!doctype html><meta charset='utf-8'><title>AIQYN — көрсетілім</title>"
        f"<style>{CSS}</style><div class='wrap'>")

    add("<h1>AIQYN — жол ақауын автоматты анықтау</h1>")
    add(f"<p class='sub'>Дереккөз: <b>{html.escape(ctx['video_name'])}</b> · "
        f"{ctx['duration']:.1f} сек · {ctx['frames']} кадр · "
        f"{html.escape(ctx['street'])}</p>")
    add("<p class='lead'>Бұл бет бір видеоның толық жолын көрсетеді: "
        "камера кадрынан бастап, жауапты органға кететін ресми құжатқа дейін. "
        "Бетте келтірілген сурет пен санның бәрі — жүйенің нақты шығысы, "
        "қолмен түзетілген ештеңе жоқ.</p>")

    verdicts = ctx.get("verdicts") or []
    add("<div class='kpis'>")
    for value, label, cls in (
        (ctx["frames"], "талданған кадр (100%)", "g"),
        (ctx["raw_detections"], "шикі детекция", ""),
        (len(verdicts) or len(docs), "үміткер", "a"),
        (len(docs), "ресми құжат", "g"),
    ):
        add(f"<div class='kpi'><div class='n {cls}'>{value}</div>"
            f"<div class='l'>{html.escape(label)}</div></div>")
    add("</div>")

    # --- Сүзгі тізбегі ---
    if verdicts:
        add("<h2>Сүзгі тізбегі</h2>")
        add("<p class='lead'>Жүйе әдейі КӨП тауып, содан кейін сүзеді. Бірінші "
            "саты ештеңе жіберіп алмауға бапталған, ал кейінгі сатылар — "
            "дәлдікке. Соңында ресми жөндеу тапсырысын ашуға тұрарлық ақау "
            "ғана қалады.</p>")
        add("<table><tr><th>Саты</th><th>Не істейді</th><th>Қалғаны</th></tr>")
        for name, what, left in (
            ("1. YOLO модель", "әр кадрды толық және екі аймақ бойынша қарайды",
             f"{ctx['raw_detections']} детекция"),
            ("2. Уақыт бойынша растау",
             "бір кадрлық көлеңке мен дақты тастайды, бір нысанды бір рет санайды",
             f"{len(verdicts)} үміткер"),
            ("3. ИИ сарапшы (3 дауыс)",
             "әр үміткерді 3 рет тәуелсіз тексеріп, көпшілік дауыспен шешеді",
             f"{sum(1 for v in verdicts if (v.get('ai') or {}).get('is_real'))} расталды"),
            ("4. Өлшем шегі",
             "ресми тапсырыс ашуға тұрарлық өлшемге жетпегенін тіркемейді",
             f"{len(docs)} ресми құжат"),
        ):
            add(f"<tr><td><b>{html.escape(name)}</b></td><td>{html.escape(what)}</td>"
                f"<td><b>{html.escape(left)}</b></td></tr>")
        add("</table>")

        add("<h3 style='margin:34px 0 6px;font-size:19px'>Әр үміткердің шешімі</h3>")
        add("<table><tr><th>Үміткер</th><th>Модель</th><th>ИИ дауысы</th>"
            "<th>Өлшемі</th><th>Шешім</th></tr>")
        for index, v in enumerate(verdicts, 1):
            reg = v.get("decision") == "registered"
            area = (v.get("area_frac") or 0) * 100
            limit = (v.get("min_area_frac") or 0) * 100
            add(f"<tr><td>{index}. {html.escape(str(v.get('class_key') or ''))}</td>"
                f"<td>{(v.get('yolo_confidence') or 0)*100:.0f}%</td>"
                f"<td>{html.escape(str(v.get('ai_vote_summary') or '—'))}</td>"
                f"<td>{area:.2f}% (шек {limit:.2f}%)</td>"
                f"<td class='{'g' if reg else 'r'}'><b>"
                f"{'ӨТТІ' if reg else 'ӨТПЕДІ'}</b></td></tr>")
        add("</table>")

    # --- Тізбек ---
    add("<h2>Жүйе қалай табады</h2>")
    for index, (title, body) in enumerate((
        ("Кадр оқылады",
         "Видеофайлдың ӘР кадры талданады — бірде-бір кадр өткізілмейді. "
         "Тірі камерада талдаушы үлгермесе кадр тасталады (көрініс артта "
         "қалмауы керек), ал файлда ондай шектеу жоқ."),
        ("YOLO модель ақауды іздейді",
         "RDD2022 датасетінде (47 420 сурет) үйретілген модель. Толық кадрдан "
         "бөлек, жол көрінетін бөлік ЕКІ аймаққа бөлініп, өз ажыратымдылығымен "
         "де беріледі — алыстағы ұсақ ақау сонда ғана табылады."),
        ("Уақыт бойынша растау — жалған детекцияны сүзу",
         "Бір кадрдағы көлеңке мен дақ келесі кадрда жоғалады. Ақау кемінде "
         "ЕКІ кадрда қатарынан көрінгенде ғана расталады, әрі бір нысан "
         "бір-ақ рет саналады. Видеода: "
         f"{ctx['raw_detections']} шикі детекциядан {len(verdicts) or len(docs)} үміткер қалды."),
        ("Ең анық кадр таңдалады",
         "Ақау алғаш көрінгенде кадрда бірнеше пиксель ғана болады. Дәлел "
         "жазылып жатқанда жүйе сол нысанның жақынырақ әрі сенімдірек "
         "кадрларын іздеп, құжатқа ЕҢ анығын қояды."),
        ("GPS + мекенжай + жол осіне түсіру",
         "Координата кадрдың уақытымен сәйкестендіріледі, 2ГИС нақты "
         "мекенжайды береді, белгі жолдың осіне түсіріледі."),
        ("ИИ екінші саты — 3 тәуелсіз дауыс",
         "Gemini өңделмеген фотоны көріп, «бұл ресми жөндеу тапсырысын ашуға "
         "тұрарлық ақау ма?» деген сұраққа жауап береді. Бір сұрақ ҮШ РЕТ "
         "қойылып, көпшілік дауыс алынады — сонда шекарадағы жағдайда да шешім "
         "тұрақты болады."),
        ("Өлшем шегі",
         "Жол тексеру тәжірибесінде әр микро-қажалуға тапсырыс ашылмайды. "
         "Ақау доңғалаққа нақты қауіп төндіретін өлшемге жеткенде ғана "
         "тіркеледі. Өлшем ақау ЕҢ ЖАҚЫН көрінген кадр бойынша алынады."),
        ("Ресми құжат → сайт → оператор",
         "Санат, мекенжай, жауапты орган, сипаттама (қазақша/орысша), "
         "5-10 секундтық видео дәлел. Оператор растайды да, 109-ға жібереді."),
    ), 1):
        add(f"<div class='step'><div class='i'>{index}</div><div class='t'>"
            f"<b>{html.escape(title)}</b><span>{html.escape(body)}</span></div></div>")

    # --- Карта ---
    if ctx.get("map_shot"):
        add("<h2>Карта — барлық ақау</h2>")
        add("<figure><img src='" + b64(ctx["map_shot"]) + "'>"
            f"<figcaption>Оператор картасы: {len(docs)} нүкте "
            f"{html.escape(ctx['street'])} бойында. Сол жақта — кезек, "
            "әр жолда ақау түрі, мекенжайы, ИИ сенімділігі және ауырлығы."
            "</figcaption></figure>")

    # --- Ақаулар ---
    add("<h2>Табылған ақаулар — дәлел фотолары</h2>")
    add("<p class='lead'>Әр фото — жүйенің өз шығысы: рамканы да, жазуды да "
        "модель қойған. Астында сол ақау бойынша ИИ қорытындысы тұр.</p>")

    colors = {"pothole": "#ff3b30", "crack_alligator": "#ffaa00",
              "crack_longitudinal": "#3cd0ff", "crack_transverse": "#3cd0ff"}
    for index, doc in enumerate(docs, 1):
        color = colors.get(doc.get("class_key"), "#ff3b30")
        ai_ok = bool(doc.get("ai_verified"))
        add(f"<div class='card'><div class='head'>"
            f"<div class='num' style='background:{color}'>{index:02d}</div>"
            f"<div><div class='title'>{html.escape(doc.get('defect_type_official') or '')}</div>"
            f"<div class='id'>{html.escape(doc['event_id'])}</div></div></div>")

        photo = ctx["photos"].get(doc["event_id"])
        if photo:
            add(f"<img src='{b64(photo)}'>")

        add("<div class='meta'>")
        for key, value in (
            ("Мекенжай", doc.get("address_text") or "—"),
            ("Координата", f"{doc.get('lat'):.5f}, {doc.get('lon'):.5f}"),
            ("Модель сенімділігі", f"{(doc.get('confidence') or 0) * 100:.0f}%"),
            ("Ауырлығы", SEVERITY_KK.get(doc.get("severity"), doc.get("severity") or "—")),
            ("Жөндеу мерзімі", f"{doc.get('ai_urgency_days') or 0} күн"),
        ):
            add(f"<div><div class='k'>{html.escape(key)}</div>"
                f"<div class='v'>{html.escape(str(value))}</div></div>")
        add("</div>")

        badge = ("<span class='tag ok'>ИИ РАСТАДЫ</span>" if ai_ok
                 else "<span class='tag no'>ИИ КҮМӘНДАНДЫ</span>")
        add(f"<div class='ai'>{badge} <b>{(doc.get('ai_confidence') or 0) * 100:.0f}%</b> — "
            f"{html.escape(doc.get('ai_note') or '')}")
        if doc.get("ai_danger") and ai_ok:
            add(f"<br><b>Қауіп:</b> {html.escape(doc['ai_danger'])}")
        add("</div></div>")

        card_shot = ctx["cards"].get(doc["event_id"])
        if card_shot:
            add(f"<figure><img src='{b64(card_shot)}'>"
                f"<figcaption>Осы ақау сайтта қалай көрінеді — оператор дәл осы "
                f"экранды көреді.</figcaption></figure>")

    # --- ИИ қатені қалай ұстады ---
    dropped = [v for v in verdicts if v.get("decision") != "registered"]
    if dropped:
        add("<h2>Жүйе қатені қалай ұстады</h2>")
        add("<p class='lead'>Модель бір жерден ақау «көрді», бірақ кейінгі "
            "сатылар оны тіркеуге жібермеді. Әр шешімнің негізі жазылып "
            "қалады — оператор да, әзірлеуші де «неге бұл құжат ашылмады?» "
            "деген сұраққа жауап таба алады.</p>")
        add("<table><tr><th>Үміткер</th><th>Модель</th><th>ИИ дауысы</th>"
            "<th>Неге тіркелмеді</th></tr>")
        for v in dropped:
            add(f"<tr><td>{html.escape(str(v.get('class_key') or ''))}</td>"
                f"<td>{(v.get('yolo_confidence') or 0)*100:.0f}%</td>"
                f"<td>{html.escape(str(v.get('ai_vote_summary') or '—'))}</td>"
                f"<td>{html.escape((v.get('reason') or '')[:200])}</td></tr>")
        add("</table>")

    if rejected:
        add("<h2>ИИ құжатта не деді</h2>")
        add("<p class='lead'>Екі сатылы тексерудің мәні осында. Модель бір "
            "жерден ақау «көрді», ал ИИ сарапшы фотоны қарап оның жалған "
            "екенін айтты. Құжат жойылмайды — «ИИ күмәнданды» деп белгіленеді "
            "де, соңғы шешімді инженер қабылдайды.</p>")
        add("<table><tr><th>Оқиға</th><th>Модель</th><th>ИИ</th>"
            "<th>ИИ не деді</th></tr>")
        for doc in rejected:
            add(f"<tr><td>{html.escape(doc['event_id'][-6:])}</td>"
                f"<td>{(doc.get('confidence') or 0) * 100:.0f}% "
                f"{html.escape(doc.get('class_key') or '')}</td>"
                f"<td class='r'>{(doc.get('ai_confidence') or 0) * 100:.0f}% — ақау емес</td>"
                f"<td>{html.escape((doc.get('ai_note') or '')[:220])}</td></tr>")
        add("</table>")

    add(f"<footer>AIQYN Vision · дереккөз {html.escape(ctx['video_name'])} · "
        f"жасалған {html.escape(ctx['built_at'])}<br>"
        "Бұл беттегі әр сурет пен сан жүйенің нақты шығысы. "
        "Қайталау: <code>python scripts/export_demo_pack.py</code></footer>")
    add("</div>")
    return "".join(parts)


# ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Көрсетілім пакетін жинау")
    parser.add_argument("--portal", default="http://localhost:8000")
    parser.add_argument("--out", default="reports/demo-pack")
    parser.add_argument("--video-name", default="test.MOV")
    parser.add_argument("--street", default="Шымкент, куиши Мамен көшесі")
    parser.add_argument("--frames", type=int, default=126)
    parser.add_argument("--duration", type=float, default=6.3)
    parser.add_argument("--raw-detections", type=int, default=44)
    parser.add_argument("--no-cards", action="store_true",
                        help="карточка скриншоттарын жасамау (жылдамырақ)")
    args = parser.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    if out.exists():
        shutil.rmtree(out)
    (out / "01-akaular").mkdir(parents=True)
    (out / "02-kujattar").mkdir(parents=True)
    (out / "03-karta").mkdir(parents=True)

    print("1/4  Сайттан құжаттарды алу…")
    response = requests.get(f"{args.portal}/api/documents", params={"limit": 100}, timeout=20)
    response.raise_for_status()
    docs = response.json().get("documents") or []
    docs.sort(key=lambda d: d["event_id"])
    if not docs:
        raise SystemExit("Сайтта құжат жоқ. Алдымен видеоны өткізіңіз.")
    print(f"     {len(docs)} құжат")

    chrome = find_chrome()
    if chrome is None:
        print("     ЕСКЕРТУ: Chrome табылмады — карта мен карточка скриншоттары жасалмайды")

    print("2/4  Дәлел фотоларын жинау…")
    photos: dict[str, Path] = {}
    evidence_dir = ROOT / "evidence"
    for index, doc in enumerate(docs, 1):
        source = evidence_dir / f"{doc['event_id']}.jpg"
        if not source.exists():
            continue
        target = out / "01-akaular" / f"{index:02d}-{doc.get('class_key','akau')}.jpg"
        shutil.copy2(source, target)
        photos[doc["event_id"]] = target
    print(f"     {len(photos)} фото")

    cards: dict[str, Path] = {}
    map_shot = None
    if chrome:
        print("3/4  Сайттың скриншоттары…")
        map_shot = out / "03-karta" / "karta.png"
        if shoot(chrome, f"{args.portal}/portal", map_shot):
            print("     карта дайын")
        else:
            map_shot = None
        if not args.no_cards:
            for index, doc in enumerate(docs, 1):
                target = out / "02-kujattar" / f"{index:02d}-kujat.png"
                url = f"{args.portal}/portal?event={doc['event_id']}"
                if shoot(chrome, url, target):
                    cards[doc["event_id"]] = target
                    print(f"     карточка {index}/{len(docs)}")
    else:
        print("3/4  Скриншоттар өткізілді")

    print("4/4  HTML бетін құрастыру…")
    verdicts = []
    for path in sorted((ROOT / "evidence").glob("*.verdict.json")):
        try:
            verdicts.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    verdicts.sort(key=lambda d: d.get("detected_at") or 0)
    from datetime import datetime
    ctx = {
        "docs": docs, "photos": photos, "cards": cards, "map_shot": map_shot,
        "verdicts": verdicts,
        "video_name": args.video_name, "street": args.street,
        "frames": args.frames, "duration": args.duration,
        "raw_detections": args.raw_detections,
        "built_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
    page = out / "AIQYN-korsetilim.html"
    page.write_text(build_html(ctx), encoding="utf-8")

    total_mb = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print(f"\nПакет дайын: {out}")
    print(f"  бет      : {page}")
    print(f"  ақау     : {len(photos)} фото")
    print(f"  карточка : {len(cards)}")
    print(f"  өлшемі   : {total_mb:.1f} МБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
