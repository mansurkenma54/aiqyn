"""AIQYN Vision — командалық интерфейс.

Қолдану:
    python -m vision.main doctor                 # бәрін тексеру (алдымен осыны)
    python -m vision.main run                    # телефон камерасынан
    python -m vision.main run --source file --video test.mp4
    python -m vision.main run --source rtsp --rtsp-url rtsp://...
    python -m vision.main scan                   # телефонды желіден іздеу
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# --- Windows консолінде қазақ әріптері дұрыс шығуы үшін ---
# Әдепкі cp1251 кодтауы «қ», «ә», «ң» әріптерінде құлап қалады.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from .config import ROOT, Config  # noqa: E402

log = logging.getLogger("aiqyn")

OK = "[ OK ]"
FAIL = "[ ҚАТЕ ]"
WARN = "[ ЕСКЕРТУ ]"


# ============================================================
#  Журнал
# ============================================================

def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("ultralytics").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ============================================================
#  Телефонды желіден іздеу
# ============================================================

def _local_subnets() -> list[str]:
    """Ноутбуктің желілеріндегі мекенжайларды жинау."""
    hosts: list[str] = []
    try:
        import psutil
        for addresses in psutil.net_if_addrs().values():
            for addr in addresses:
                if addr.family != socket.AF_INET:
                    continue
                ip = addr.address
                if ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                parts = ip.split(".")
                if len(parts) == 4:
                    prefix = ".".join(parts[:3])
                    hosts.extend(f"{prefix}.{i}" for i in range(1, 255))
    except Exception:
        pass

    # USB tethering кезіндегі ең жиі кездесетін мекенжайлар
    hosts.extend([
        "192.168.42.129", "192.168.42.1",     # Android USB tethering
        "192.168.43.1", "192.168.43.129",     # Android Wi-Fi hotspot
        "192.168.1.100", "192.168.0.100",
    ])

    seen: set[str] = set()
    unique: list[str] = []
    for host in hosts:
        if host not in seen:
            seen.add(host)
            unique.append(host)
    return unique[:1024]


def scan_for_phone(ports: tuple[int, ...] = (8080, 8081, 4747)) -> list[str]:
    """IP Webcam ашық тұрған мекенжайларды табу."""
    targets = [(host, port) for host in _local_subnets() for port in ports]

    def probe(target) -> str | None:
        host, port = target
        sock = socket.socket()
        sock.settimeout(0.35)
        try:
            sock.connect((host, port))
            return f"{host}:{port}"
        except Exception:
            return None
        finally:
            sock.close()

    found: list[str] = []
    with ThreadPoolExecutor(max_workers=128) as pool:
        for result in pool.map(probe, targets):
            if result:
                found.append(result)
    return found


# ============================================================
#  doctor — толық тексеру
# ============================================================

def cmd_doctor(cfg: Config) -> int:
    print()
    print("=" * 68)
    print("  AIQYN Vision — жүйені тексеру")
    print("=" * 68)
    problems = 0

    # --- 1. Кітапханалар ---
    print("\n1) Кітапханалар")
    for module, label in (
        ("cv2", "OpenCV"), ("numpy", "NumPy"),
        ("requests", "requests"), ("PIL", "Pillow"),
    ):
        try:
            imported = __import__(module)
            version = getattr(imported, "__version__", "?")
            print(f"   {OK} {label:<12} {version}")
        except ImportError:
            print(f"   {FAIL} {label} орнатылмаған  ->  pip install -r requirements.txt")
            problems += 1

    try:
        import ultralytics
        import torch
        print(f"   {OK} {'ultralytics':<12} {ultralytics.__version__}")
        print(f"   {OK} {'torch':<12} {torch.__version__}")
    except ImportError:
        print(f"   {FAIL} ultralytics/torch орнатылмаған  ->  pip install ultralytics")
        problems += 1

    # --- 2. Модель салмағы ---
    print("\n2) AI моделі")
    weights = cfg.weights_path
    if weights.exists():
        size_mb = weights.stat().st_size / 1e6
        print(f"   {OK} RDD салмағы табылды: {weights.name} ({size_mb:.1f} МБ)")
    else:
        print(f"   {WARN} RDD салмағы жоқ: {weights}")
        print(f"          Жол ақауын анықтау ІСТЕМЕЙДІ.")
        print(f"          Түзету:  python scripts/fetch_weights.py")
        problems += 1

    # --- 3. Шрифт ---
    print("\n3) Интерфейс шрифті (қазақ әріптері үшін)")
    from .overlay import FONT_CANDIDATES
    font = next((p for p in FONT_CANDIDATES if Path(p).exists()), None)
    if font:
        print(f"   {OK} {Path(font).name}")
    else:
        print(f"   {WARN} TTF шрифт табылмады — жазулар дұрыс шықпауы мүмкін")

    # --- 4. Қалталар ---
    print("\n4) Қалталар")
    for label, path in (
        ("Дәлелдер", cfg.evidence_path),
        ("Кезек (outbox)", cfg.outbox_path),
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            print(f"   {OK} {label}: {path}")
        except Exception as exc:
            print(f"   {FAIL} {label} қолжетімсіз: {exc}")
            problems += 1

    # --- 5. Телефон ---
    print("\n5) Телефон (IP Webcam)")
    import requests
    phone_ok = False
    try:
        response = requests.get(cfg.phone_base, timeout=3.0)
        response.raise_for_status()
        print(f"   {OK} Телефон жауап берді: {cfg.phone_base}")
        phone_ok = True
    except Exception as exc:
        print(f"   {FAIL} {cfg.phone_base} жауап бермеді ({type(exc).__name__})")
        print("          Желіден іздеп көрейік...")
        found = scan_for_phone()
        if found:
            print(f"   {OK} Табылды: {', '.join(found)}")
            host = found[0].split(":")[0]
            print(f"          Іске қосу:  python -m vision.main run --phone-host {host}")
        else:
            print(f"   {FAIL} Желіде бірде-бір камера сервері табылмады.")
            print("          Тексеріңіз:")
            print("            1) Телефонда IP Webcam ашық па, 'Start server' басылған ба?")
            print("            2) USB tethering ҚОСУЛЫ ма?")
            print("               (Параметрлер -> Байланыстар -> Модем режимі -> USB модем)")
            print("            3) Қосымша экранның төменінде көрсеткен мекенжайды қараңыз")
            problems += 1

    # --- 6. GPS ---
    if phone_ok:
        print("\n6) GPS (телефон сенсоры)")
        try:
            from .gps import _extract_gps, _extract_gps_json

            parsed = None
            for url in (cfg.phone_base + "/gps.json", cfg.sensors_url):
                try:
                    response = requests.get(url, timeout=4.0)
                    response.raise_for_status()
                    payload = response.json()
                except Exception:
                    continue
                parsed = _extract_gps_json(payload) or _extract_gps(payload)
                if parsed:
                    break
            if parsed:
                lat, lon, accuracy, *_ = parsed
                print(f"   {OK} GPS жұмыс істеп тұр: {lat:.5f}, {lon:.5f} (дәлдік ±{accuracy:.0f} м)")
            else:
                print(f"   {WARN} sensors.json ішінде GPS жоқ")
                print("          Телефонда GPS-ті қосыңыз және IP Webcam-ға")
                print("          орналасқан жерге рұқсат беріңіз (ашық аспан астында тезірек табады).")
        except Exception as exc:
            print(f"   {WARN} sensors.json оқылмады: {exc}")

    # --- 7. Қосымша API кілттері ---
    print("\n7) Қосымша қызметтер (.env)")

    if cfg.ai_api_key:
        from .aiverify import AIVerifier
        verifier = AIVerifier(cfg)
        models = verifier.list_models()
        if models:
            if cfg.ai_model in models:
                print(f"   {OK} ИИ-тексеруші: {cfg.ai_provider} / {cfg.ai_model}")
                print(f"          Резерв: {len(verifier.keys)} кілт × "
                      f"{len(verifier.models)} модель "
                      f"({len(verifier.keys) * len(verifier.models)} комбинация)")
            else:
                print(f"   {WARN} «{cfg.ai_model}» моделі тізімде жоқ.")
                suggestions = [m for m in models if "flash" in m][:4] or models[:4]
                print(f"          Қолжетімді: {', '.join(suggestions)}")
                print(f"          .env ішінде AIQYN_AI_MODEL мәнін ауыстырыңыз.")
        elif cfg.ai_provider == "gemini":
            print(f"   {WARN} ИИ кілті қойылған, бірақ модельдер тізімі алынбады")
            print(f"          (кілт қате немесе интернет жоқ)")
        else:
            print(f"   {OK} ИИ-тексеруші: {cfg.ai_provider} / {cfg.ai_model}")
    else:
        print(f"   {WARN} ИИ-тексеруші өшірулі (AIQYN_AI_API_KEY жоқ)")
        print("          Тегін кілт: https://aistudio.google.com/apikey")
        print("          Не береді: жалған детекцияларды автоматты сүзеді")

    if cfg.gis2_key:
        print(f"   {OK} 2ГИС мекенжайы қосулы (Шымкент көшелерін жақсы біледі)")
    else:
        print(f"   {WARN} 2ГИС кілті жоқ — OpenStreetMap қолданылады (тегін, бірақ дәлдігі төмен)")
        print("          Тегін кілт: https://dev.2gis.com")

    try:
        import os
        if os.getenv("AIQYN_TELEGRAM_TOKEN") and os.getenv("AIQYN_TELEGRAM_CHAT_ID"):
            sys.path.insert(0, str(ROOT))
            from portal.notify import notifier
            ok_flag, detail = notifier.check()
            if ok_flag:
                print(f"   {OK} Telegram хабарламасы: {detail}")
            else:
                print(f"   {WARN} Telegram бапталмаған: {detail}")
        else:
            print(f"   {WARN} Telegram хабарламасы өшірулі (токен жоқ)")
            print("          Тегін: Telegram-да @BotFather -> /newbot")
    except Exception as exc:
        print(f"   {WARN} Telegram тексерілмеді: {exc}")

    # --- 8. Portal ---
    print("\n8) AIQYN Portal (сайт)")
    try:
        response = requests.get(cfg.portal_url.rstrip("/") + "/api/health", timeout=4.0)
        if response.status_code < 500:
            print(f"   {OK} Сайт жұмыс істеп тұр: {cfg.portal_url}")
        else:
            print(f"   {WARN} Сайт қатемен жауап берді: {response.status_code}")
    except Exception:
        print(f"   {WARN} Сайт әзірге қосылмаған: {cfg.portal_url}")
        print("          Бұл қалыпты — құжаттар outbox/ қалтасына жиналып,")
        print("          сайт қосылғанда автоматты жіберіледі.")

    # --- Қорытынды ---
    print("\n" + "=" * 68)
    if problems == 0:
        print("  Бәрі дайын. Іске қосу:  python -m vision.main run")
    else:
        print(f"  {problems} мәселе табылды (жоғарыда көрсетілген).")
        print("  Телефонсыз тексеру үшін:")
        print("     python -m vision.main run --source file --video <видео.mp4>")
    print("=" * 68 + "\n")
    return 0 if problems == 0 else 1


def cmd_scan(_: Config) -> int:
    print("\nЖелідегі камера серверлерін іздеу (біраз уақыт алады)...\n")
    found = scan_for_phone()
    if found:
        print("Табылды:")
        for address in found:
            print(f"   http://{address}")
        host = found[0].split(":")[0]
        print(f"\nІске қосу:\n   python -m vision.main run --phone-host {host}\n")
    else:
        print("Ештеңе табылмады. IP Webcam қосулы ма және USB tethering қосулы ма — тексеріңіз.\n")
    return 0


def cmd_images(cfg: Config, target: str, lat: float | None, lon: float | None) -> int:
    """Дайын фото(лар)ды талдап, құжат құрастыру."""
    from .images import ImageBatchProcessor

    if not Path(target).exists():
        print(f"{FAIL} Жол табылмады: {target}")
        return 2

    return ImageBatchProcessor(cfg, lat=lat, lon=lon).run(target)


def cmd_run(cfg: Config) -> int:
    from .pipeline import VisionPipeline

    if cfg.source == "file" and not cfg.video_path:
        print(f"{FAIL} --source file үшін --video <жол> көрсету керек")
        return 2

    try:
        VisionPipeline(cfg).run()
        return 0
    except ConnectionError as exc:
        print(f"\n{FAIL} {exc}\n")
        return 1
    except FileNotFoundError as exc:
        print(f"\n{FAIL} {exc}\n")
        return 1


# ============================================================
#  Аргументтер
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vision",
        description="AIQYN Vision — жол ақауын автоматты анықтау және ресми құжат құрастыру",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="жүйені толық тексеру")
    subparsers.add_parser("scan", help="телефонды желіден іздеу")

    run = subparsers.add_parser("run", help="анықтауды іске қосу")
    run.add_argument("--source", choices=["phone", "file", "rtsp"], help="видео көзі")
    run.add_argument("--video", dest="video_path", help="видеофайл жолы (--source file)")
    run.add_argument("--rtsp-url", dest="rtsp_url", help="RTSP сілтемесі (--source rtsp)")
    run.add_argument("--phone-host", dest="phone_host", help="телефонның IP мекенжайы")
    run.add_argument("--phone-port", dest="phone_port", type=int, help="порт (әдепкі 8080)")
    run.add_argument("--weights", help="YOLO салмағының жолы")
    run.add_argument("--conf", dest="conf_threshold", type=float, help="сенімділік шегі (0-1)")
    run.add_argument("--stride", dest="frame_stride", type=int, help="әр N-ші кадрды талдау")
    run.add_argument("--device", help="cpu немесе 0 (GPU)")
    run.add_argument("--crop-bottom", dest="crop_bottom_frac", type=float,
                     help="кадрдың төменгі бөлігін кесу (мыс. 0.35 — көлік панелі)")
    run.add_argument("--crop-top", dest="crop_top_frac", type=float,
                     help="кадрдың жоғарғы бөлігін кесу (мыс. 0.15 — аспан)")
    run.add_argument("--duration", dest="max_duration_sec", type=float,
                     help="белгіленген секундтан кейін автоматты тоқтау (тест үшін)")
    run.add_argument("--lat", dest="manual_lat", type=float,
                     help="координатаны қолмен беру (телефон GPS-і істемесе)")
    run.add_argument("--lon", dest="manual_lon", type=float,
                     help="координатаны қолмен беру (телефон GPS-і істемесе)")
    run.add_argument("--gps-track", dest="gps_track",
                     help="жазылған GPS трегі (jsonl) — видеомен қатар ойнатылады")
    run.add_argument("--no-ai", dest="ai_verify", action="store_false", default=None,
                     help="ИИ-тексерушіні өшіру (симуляциямен тексергенде керек)")
    run.add_argument("--pothole-cv", dest="enable_pothole_cv", action="store_true",
                     default=None,
                     help="физикалық белгі бойынша шұңқыр детекторын қосу "
                          "(симуляцияда пайдалы, нақты жолда жалған сигнал береді)")
    run.add_argument("--portal", dest="portal_url", help="сайттың мекенжайы")
    run.add_argument("--latency", dest="stream_latency_sec", type=float,
                     help="ағын кідірісі, сек (GPS түзетуі үшін)")
    run.add_argument("--no-send", dest="send_enabled", action="store_false", default=None,
                     help="сайтқа жібермеу (тек outbox-қа жинау)")
    run.add_argument("--no-preview", dest="show_preview", action="store_false", default=None,
                     help="терезесіз режим")
    run.add_argument("--fast", dest="file_realtime", action="store_false", default=None,
                     help="видеофайлды нақты уақыт жылдамдығын күтпей, барынша тез өңдеу")
    run.add_argument("--no-lanes", dest="draw_lanes", action="store_false", default=None,
                     help="жол сызықтарын салмау")
    run.add_argument("--no-flood", dest="enable_flood", action="store_false", default=None)
    run.add_argument("--no-lights", dest="enable_streetlight", action="store_false", default=None)
    run.add_argument("--log", dest="log_level", default=None,
                     choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    images = subparsers.add_parser(
        "images", help="дайын фото(лар)ды талдау — телефонсыз тексеру үшін"
    )
    images.add_argument("target", help="сурет файлы немесе қалта")
    images.add_argument("--lat", type=float, help="координата (EXIF жоқ болса)")
    images.add_argument("--lon", type=float, help="координата (EXIF жоқ болса)")
    images.add_argument("--weights", help="YOLO салмағының жолы")
    images.add_argument("--conf", dest="conf_threshold", type=float, help="сенімділік шегі")
    images.add_argument("--portal", dest="portal_url", help="сайттың мекенжайы")
    images.add_argument("--no-send", dest="send_enabled", action="store_false", default=None)
    images.add_argument("--no-lanes", dest="draw_lanes", action="store_false", default=None)
    images.add_argument("--crop-bottom", dest="crop_bottom_frac", type=float,
                        help="кадрдың төменгі бөлігін кесу (мыс. 0.35 — көлік панелі)")
    images.add_argument("--crop-top", dest="crop_top_frac", type=float,
                        help="кадрдың жоғарғы бөлігін кесу (мыс. 0.15 — аспан)")
    images.add_argument("--log", dest="log_level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    for sub in (run, images):
        sub.add_argument("--config", help="config.json жолы")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    skip = {"command", "config", "target", "lat", "lon"}
    options = {k: v for k, v in vars(args).items() if k not in skip and v is not None}

    cfg = Config.load(getattr(args, "config", None), **options)
    setup_logging(cfg.log_level)

    if args.command == "images":
        return cmd_images(cfg, args.target, args.lat, args.lon)

    handlers = {"doctor": cmd_doctor, "scan": cmd_scan, "run": cmd_run}
    return handlers[args.command](cfg)


if __name__ == "__main__":
    sys.exit(main())
