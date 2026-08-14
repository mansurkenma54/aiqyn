"""AIQYN Vision — барлық баптаулар бір жерде.

Баптау реті (кейінгісі алдыңғысын басып жазады):
    1) осы файлдағы әдепкі мәндер
    2) config.json
    3) командалық жол аргументтері (--conf, --source, ...)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Optional

# aiqyn/vision/config.py -> aiqyn/
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    # ---------- Видео көзі ----------
    source: str = "phone"                 # phone | file | rtsp
    phone_host: str = "192.168.42.129"    # USB tethering кезіндегі әдепкі мекенжай
    phone_port: int = 8080
    video_path: str = ""                  # source=file болғанда
    rtsp_url: str = ""                    # source=rtsp (Sergek/CCTV/дрон — Phase 2)
    file_realtime: bool = True            # файлды нақты уақыт жылдамдығымен ойнату

    # ---------- Детекция ----------
    weights: str = "models/RDD_best.pt"   # YOLOv8, RDD2022-де үйретілген
    conf_threshold: float = 0.60
    frame_stride: int = 5                 # әр N-ші кадрды талдау (CPU үнемдеу)
    imgsz: int = 640
    device: str = "cpu"                   # "cpu" немесе "0" (CUDA)
    min_box_area_frac: float = 0.0015     # тым ұсақ bbox-тарды алып тастау

    # Кадрды қию: телефон көлікте тұрғанда кадрдың төменгі бөлігін көбіне
    # көліктің панелі (торпедо) алып қалады, ал жоғарғы бөлігі — аспан.
    # Модель RDD2022-де ЖОЛ көрінісіне үйретілген, сондықтан артық бөлікті
    # кесіп тастаған дұрыс: детекция да дәлдеу, жылдамдық та жоғары болады.
    # Мысалы: --crop-bottom 0.35 --crop-top 0.15
    crop_top_frac: float = 0.0
    crop_bottom_frac: float = 0.0

    enable_yolo: bool = True
    # Шұңқырды физикалық белгісі бойынша табатын екінші детектор.
    #
    # ӘДЕПКІДЕ ӨШІРУЛІ. Себебі өлшенді: симуляцияда ол recall-ды
    # 10%-дан 58%-ға көтереді, БІРАҚ нақты далалық бейнеде жаяу
    # жүргіншіні, ескі жамауды және көлеңкені шұңқыр деп танып,
    # шын детекцияны басып тастайды. Нақты жолда YOLO әлдеқайда
    # сенімді. Симулятормен тексергенде қосуға болады:
    #     --pothole-cv   немесе  config.json: "enable_pothole_cv": true
    enable_pothole_cv: bool = False
    enable_flood: bool = False            # су тасу (rule-based)
    # Көше жарығы детекторы ӘДЕЙІ ӨШІРУЛІ. Ол жалған сигнал көп берді
    # (өлшенген precision 21%), әрі жоба қазір БІР МІНДЕТКЕ — жол
    # ақауын дәл табуға — шоғырланған. Қажет болса қайта қосыңыз.
    enable_streetlight: bool = False      # көше жарығы (түнгі режим, rule-based)

    # ---------- Жол ақауын нақтылау ----------
    # Ақау ТЕК ЖОЛ БЕТІНЕН ізделеді: шөптегі дақ, тротуардағы жарық,
    # ғимарат — бәрі сүзіледі. Жол шекарасы roadedge.py арқылы табылады.
    road_only: bool = True
    road_margin_px: int = 12              # шекарадан сәл кеңірек рұқсат

    # Уақыт бойынша растау: нағыз шұңқыр көлік жақындаған сайын
    # БІРНЕШЕ кадрда қатарынан көрінеді. Бір кадрда жарқ ете қалған
    # нәрсе — шу. Бұл жалған детекцияны күрт азайтады.
    # ӘДЕПКІДЕ ӨШІРУЛІ (1 = растау жоқ). Себебі өлшенді: далалық
    # бейнеде YOLO бір шұңқырды бар болғаны 1-2 кадрда табады, ал
    # растау 2 рет көрінуді талап еткенде ШЫН шұңқыр да жоғалып кетті.
    # Детекция ТЫҒЫЗ болатын жағдайда (жақсы жарық, баяу жүріс) 2-ге
    # қойған пайдалы: симуляцияда ол шуды 1429-дан 115-ке түсірді.
    confirm_frames: int = 1               # осынша кадрда көрінуі керек
    confirm_window: int = 6               # соңғы осынша талданған кадр ішінде
    track_iou: float = 0.20               # кадрлар арасында бір нысан деп санау шегі
    night_brightness: float = 65.0        # кадрдың орташа жарықтығы < осы => түн

    # ---------- Дәлел (evidence) ----------
    evidence_dir: str = "evidence"
    pre_roll_sec: float = 3.0             # оқиғаға дейінгі
    post_roll_sec: float = 5.0            # оқиғадан кейінгі  => клип ~8 сек
    clip_fps: float = 15.0
    use_shot_jpg: bool = True             # ең анық кадрды /shot.jpg-тен жоғары сапада алу

    # ---------- GPS ----------
    gps_poll_hz: float = 2.0
    stream_latency_sec: float = 0.80      # МАҢЫЗДЫ: MJPEG ағынының кідірісі.
                                          # Кадрдың нақты уақыты = алынған уақыт - осы.
    gps_max_extrapolate_sec: float = 3.0  # одан әрі ескі фикс "сенімсіз" деп белгіленеді
    fallback_lat: float = 42.31560        # GPS жоқ кезде (демо/файл режимі) — Шымкент
    fallback_lon: float = 69.58690
    manual_lat: Optional[float] = None    # --lat: телефон GPS-і істемесе қолмен беру
    manual_lon: Optional[float] = None    # --lon
    gps_track: str = ""                   # --gps-track: жазылған трек (jsonl)

    # ---------- Жөндеу аймақтары ----------
    # Оператор сайттан белгілеген жөндеу/жабық аймақтардағы ақаулар бойынша
    # құжат құрылмайды — ол жерде жұмыс бұрыннан жүріп жатыр.
    skip_in_zones: bool = True
    zones_refresh_sec: float = 60.0

    # ---------- Дедупликация ----------
    dedup_radius_m: float = 25.0
    # 0 = сессия бойы есте сақтау: бір ақау бір сапарда БІР РЕТ қана
    # тіркеледі, тіпті сол жерден қайта өтсеңіз де қайталанбайды
    dedup_window_sec: float = 0.0

    # ---------- Мекенжай ----------
    geocode_enabled: bool = True
    geocode_provider: str = "auto"        # auto | nominatim | 2gis
    nominatim_url: str = "https://nominatim.openstreetmap.org/reverse"
    nominatim_email: str = ""             # Nominatim саясаты бойынша ұсынылады
    geocode_timeout: float = 6.0
    gis2_key: str = ""                    # .env: AIQYN_2GIS_KEY

    # ---------- Координатаны жолға түсіру ----------
    # GPS 5-15 метрге қателеседі, сондықтан белгі картада жолдың
    # үстінде емес, шөпте тұрып қалады. Бұл соны түзетеді.
    snap_to_road: bool = True
    snap_search_radius_m: float = 120.0   # осы радиустағы көшелер қаралады
    snap_max_distance_m: float = 25.0     # одан алыс болса — түзетілмейді

    # ---------- ИИ-тексеруші (екінші саты) ----------
    # Кілт .env файлынан алынады. Кілт болмаса — өшірулі күйде қалады,
    # жүйе бұрынғыдай жұмыс істейді.
    ai_verify: bool = True
    ai_provider: str = "gemini"           # gemini | openai
    ai_api_key: str = ""                  # .env: AIQYN_AI_API_KEY
    # Қосымша кілттер (.env: AIQYN_AI_API_KEY_2, _3 ...). Әр кілттің
    # тегін лимиті БӨЛЕК, сондықтан біреуі бітсе — келесісіне ауысамыз.
    ai_api_keys: tuple = ()
    ai_model: str = "gemini-3.1-flash-lite"   # .env: AIQYN_AI_MODEL
    # Тегін лимит біткенде автоматты ауысатын қосалқы модельдер.
    # Далалық тест ортасында ИИ үнсіз өшіп қалмауы үшін.
    ai_model_fallbacks: tuple = (
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-2.5-flash",
        "gemini-flash-latest",
    )
    ai_base_url: str = ""                 # openai үйлесімді қызмет үшін
    ai_timeout: float = 25.0
    ai_drop_rejected: bool = True         # ИИ жоққа шығарса — құжат жіберілмейді

    # ---------- AIQYN Portal (сайт) ----------
    portal_url: str = "http://localhost:8000"
    send_enabled: bool = True
    send_timeout: float = 20.0
    outbox_dir: str = "outbox"            # сайт өшік болса — осында кезекке тұрады

    # ---------- Интерфейс ----------
    show_preview: bool = True
    draw_lanes: bool = True               # мокаптағы көгілдір жол сызықтары
    preview_width: int = 960
    save_annotated: bool = False          # әр кадрды дискіге жазу (дебаг үшін)

    # ---------- Қызметтік ----------
    max_duration_sec: float = 0.0     # 0 = шексіз; тест/демо үшін шектеу
    log_level: str = "INFO"
    lang: str = "kk"                      # превьюдегі негізгі тіл

    # ================= көмекші =================

    @property
    def phone_base(self) -> str:
        return f"http://{self.phone_host}:{self.phone_port}"

    @property
    def video_url(self) -> str:
        return f"{self.phone_base}/video"

    @property
    def sensors_url(self) -> str:
        return f"{self.phone_base}/sensors.json"

    @property
    def shot_url(self) -> str:
        return f"{self.phone_base}/shot.jpg"

    def crop(self, image):
        """Кадрдың керексіз жоғарғы/төменгі бөлігін кесу."""
        if self.crop_top_frac <= 0 and self.crop_bottom_frac <= 0:
            return image
        height = image.shape[0]
        top = int(height * max(0.0, min(0.45, self.crop_top_frac)))
        bottom = height - int(height * max(0.0, min(0.60, self.crop_bottom_frac)))
        if bottom - top < 80:
            return image
        return image[top:bottom]

    def path(self, value: str) -> Path:
        """Салыстырмалы жолды жоба түбіріне қатысты абсолютті етеді."""
        p = Path(value)
        return p if p.is_absolute() else (ROOT / p)

    @property
    def evidence_path(self) -> Path:
        return self.path(self.evidence_dir)

    @property
    def outbox_path(self) -> Path:
        return self.path(self.outbox_dir)

    @property
    def weights_path(self) -> Path:
        return self.path(self.weights)

    # ================= жүктеу =================

    @classmethod
    def load(cls, config_file: str | Path | None = None, **overrides) -> "Config":
        cfg = cls()

        path = Path(config_file) if config_file else (ROOT / "config.json")
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            for key, value in data.items():
                if key.startswith("_"):
                    continue
                if key in known:
                    setattr(cfg, key, value)

        # Құпия кілттер тек .env файлынан алынады (config.json-ға жазылмайды)
        from .env import get as env_get, load_env
        load_env()
        for field_name, env_name in (
            ("ai_api_key", "AIQYN_AI_API_KEY"),
            ("ai_provider", "AIQYN_AI_PROVIDER"),
            ("ai_model", "AIQYN_AI_MODEL"),
            ("ai_base_url", "AIQYN_AI_BASE_URL"),
            ("gis2_key", "AIQYN_2GIS_KEY"),
            ("nominatim_email", "AIQYN_NOMINATIM_EMAIL"),
        ):
            value = env_get(env_name)
            if value:
                setattr(cfg, field_name, value)

        # Қосымша ИИ кілттері: AIQYN_AI_API_KEY_2, _3, _4 ...
        extra_keys = []
        if cfg.ai_api_key:
            extra_keys.append(cfg.ai_api_key)
        for index in range(2, 9):
            value = env_get(f"AIQYN_AI_API_KEY_{index}")
            if value and value not in extra_keys:
                extra_keys.append(value)
        cfg.ai_api_keys = tuple(extra_keys)

        for key, value in overrides.items():
            if value is None:
                continue
            if hasattr(cfg, key):
                setattr(cfg, key, value)

        return cfg

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def to_dict(self) -> dict:
        return asdict(self)
