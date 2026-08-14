"""Модель класын РЕСМИ санатқа айналдыру.

Модель "pothole" деп шығарады — бірақ жауапты органға баратын құжатта
"Жол жабынының ақауы — шұңқыр / Дефект дорожного покрытия — яма" деп
тұруы керек. Осы файл сол аударманы, ауырлық деңгейін және қай мекемеге
баратынын анықтайды.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str            # ішкі кілт
    kk: str             # ресми атауы (қазақша)
    ru: str             # ресми атауы (орысша)
    org: str            # жауапты ұйым кілті (ORGANISATIONS ішінен)
    base_severity: str  # low | medium | high
    color: tuple        # BGR — превью мен bbox түсі
    short: str          # превьюдегі қысқа белгі (тек ASCII — cv2 үшін)


# --- Ауырлық деңгейлері ---
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}
SEVERITY_KK = {"low": "Төмен", "medium": "Орташа", "high": "Жоғары"}
SEVERITY_RU = {"low": "Низкая", "medium": "Средняя", "high": "Высокая"}

# Мокаптағы түстер (BGR).
# Дизайн мокабында барлық ақау БІР қанық қызыл түспен көрсетілген,
# тек бөгет қана сары — сондықтан бұл жерде де солай.
ORANGE = (31, 59, 255)     # #FF3B1F — жол ақауы (мокаптағы негізгі түс)
RED = (31, 59, 255)        # #FF3B1F — жоғары қауіп / жарық (сол түс)
YELLOW = (0, 208, 255)     # #FFD000 — бөгет
BLUE = (255, 229, 0)       # #00E5FF — су тасу (жол сызығымен бір тілде)


CATEGORIES: dict[str, Category] = {
    "pothole": Category(
        key="pothole",
        kk="Жол жабынының ақауы — шұңқыр",
        ru="Дефект дорожного покрытия — яма",
        org="roads",
        base_severity="high",
        color=ORANGE,
        short="POTHOLE",
    ),
    "crack_alligator": Category(
        key="crack_alligator",
        kk="Жол жабынының ақауы — торлы жарық",
        ru="Дефект дорожного покрытия — сетка трещин",
        org="roads",
        base_severity="medium",
        color=ORANGE,
        short="ALLIGATOR CRACK",
    ),
    "crack_longitudinal": Category(
        key="crack_longitudinal",
        kk="Жол жабынының ақауы — бойлық жарық",
        ru="Дефект дорожного покрытия — продольная трещина",
        org="roads",
        base_severity="low",
        color=ORANGE,
        short="LONG. CRACK",
    ),
    "crack_transverse": Category(
        key="crack_transverse",
        kk="Жол жабынының ақауы — көлденең жарық",
        ru="Дефект дорожного покрытия — поперечная трещина",
        org="roads",
        base_severity="low",
        color=ORANGE,
        short="TRANSV. CRACK",
    ),
    "flood": Category(
        key="flood",
        kk="Жолда су тасуы",
        ru="Подтопление проезжей части",
        org="water",
        base_severity="high",
        color=BLUE,
        short="FLOODING",
    ),
    "obstruction": Category(
        key="obstruction",
        kk="Жол бөгеті",
        ru="Препятствие на проезжей части",
        org="roads",
        base_severity="high",
        color=YELLOW,
        short="OBSTRUCTION",
    ),
    "streetlight_out": Category(
        key="streetlight_out",
        kk="Көше жарығының істен шығуы",
        ru="Неисправность уличного освещения",
        org="lighting",
        base_severity="medium",
        color=RED,
        short="LIGHT OUT",
    ),
    "manhole_open": Category(
        key="manhole_open",
        kk="Ашық люк / құдық қақпағының болмауы",
        ru="Открытый люк / отсутствие крышки колодца",
        org="water",
        base_severity="high",
        color=RED,
        short="OPEN MANHOLE",
    ),
}

UNKNOWN = Category(
    key="unknown",
    kk="Анықталмаған инфрақұрылым ақауы",
    ru="Неопределённый дефект инфраструктуры",
    org="roads",
    base_severity="low",
    color=(200, 200, 200),
    short="UNKNOWN",
)


# --- Жауапты ұйымдар ---
# ЕСКЕРТУ: бұл хакатон демосы үшін. Нақты мемлекеттік мекеменің поштасы
# ҚОЙЫЛМАУЫ керек — растамай тұрып ресми өтінім жіберу жалған шағым болып
# саналады. Пилот кезеңінде әкімдікпен келісілген нақты адрестер қойылады.
ORGANISATIONS: dict[str, dict] = {
    "roads": {
        "kk": "«Шымкент қаласының жолаушылар көлігі және автомобиль жолдары басқармасы» КММ",
        "ru": "КГУ «Управление пассажирского транспорта и автомобильных дорог города Шымкент»",
        "email": "demo+roads@aiqyn.local",
    },
    "lighting": {
        "kk": "Тұрғын үй-коммуналдық шаруашылық бөлімі (көше жарығы)",
        "ru": "Отдел ЖКХ (уличное освещение)",
        "email": "demo+lighting@aiqyn.local",
    },
    "water": {
        "kk": "«Water Resources — Marketing» су шаруашылығы қызметі",
        "ru": "Служба водного хозяйства",
        "email": "demo+water@aiqyn.local",
    },
}


def get(key: str) -> Category:
    return CATEGORIES.get(key, UNKNOWN)


def org_for(key: str) -> dict:
    return ORGANISATIONS.get(get(key).org, ORGANISATIONS["roads"])


def severity_for(key: str, confidence: float, area_frac: float) -> str:
    """Инженер тексергенге дейінгі санаттық бастапқы қауіп деңгейі.

    Модель сенімділігі — анықтаудың ықтималдығы ғана; ол ақаудың қауіптілігін
    білдірмейді. Bbox ауданы да камера қашықтығына тәуелді, сондықтан физикалық
    өлшем ретінде қолданылмайды. Параметрлер backward compatibility үшін қалды.
    """
    _ = (confidence, area_frac)
    return get(key).base_severity
