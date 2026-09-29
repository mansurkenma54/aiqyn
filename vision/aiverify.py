"""ИИ-тексеруші — екінші саты (жалған детекцияны сүзу).

НЕ ҮШІН КЕРЕК
-------------
YOLO жылдам, бірақ ол тек «пішінді» таниды: көлеңкені, ескі жамауды,
құрғақ шалшықтың ізін шұңқыр деп қателесуі мүмкін. Ал жалған өтінім —
жобаның ең үлкен тәуекелі (зерттеу құжатындағы 1-тәуекел).

Сондықтан екі сатылы схема жасалды:

    1-саты  YOLOv8 (жергілікті, жылдам)   -> «мұнда бірдеңе бар»
    2-саты  Көру моделі (ИИ)              -> «бұл шынымен шұңқыр ма?»

Екінші саты тек ақау табылғанда, дәлел фотосы дайын болғанда бір рет
шақырылады — секундына емес, оқиғаға бір рет. Сондықтан тегін лимит
әбден жетеді.

МАҢЫЗДЫ: кілт болмаса немесе қызмет жауап бермесе, жүйе БҰРЫНҒЫДАЙ
жұмыс істей береді — тек бұл қосымша сүзгі болмайды.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests

from . import categories
from .config import Config

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_IMAGE_SIDE = 1024          # сурет осыдан үлкен болса кішірейтеміз


PROMPT = """Сен — жол шаруашылығының тәжірибелі инженер-сарапшысысың.
Міндетің: суреттегі жол жабынын кәсіби бағалап, ресми құжатқа
кіретін қорытынды жазу.

Автоматты жүйе бұл жерде «{defect_kk}» ({defect_ru}) деп тапты.
Жүйенің сенімділігі: {confidence:.0%}.
Ақаудың кадрдағы орны: {location_hint}
Түсірілген орны: {address}
Түсірілген уақыты: {when}

Сурет ӨҢДЕЛМЕГЕН — үстіне ешқандай сызық та, бояу да салынбаған.
Сондықтан «қызыл дақ», «түсі өзгерген аймақ» деген белгілерді
іздеме: көрсетілген орындағы АСФАЛЬТТЫҢ НАҚТЫ ЖАҒДАЙЫН бағала.

1-МІНДЕТ — ТЕКСЕРУ.
Мыналар АҚАУ болып саналАДЫ:
- шұңқыр, ойық, жабынның үгітілуі
- асфальт беті мүлдем жоқ немесе қирап, астынан қиыршық тас/топырақ
  шығып қалған аймақ (тегіс емес, түйіршікті бет) — бұл АУЫР ақау
- торлы, бойлық немесе көлденең жарықтар
- жиегі бұзылған, шөгіп кеткен жамау
Көлеңкеде тұрған ақау да — АҚАУ. Жарық аз болғаны себеп емес.

Мыналар ақау болып саналМАЙДЫ:
- көлік, баған, ағаш көлеңкесінің ӨЗІ (астында бүтін асфальт болса)
- жиегі бүтін, БЕТІ ТЕГІС жамау
- жол таңбалауы, люк қақпағы, құрғақ шаң немесе су ізі
- жол емес бет (тротуар, шөп, ғимарат)

Күмәнданған жағдайда `is_real: true` қой да, `confidence` мәнін
төмен (0.3-0.5) бер. Соңғы шешімді инженер қабылдайды — сенің
міндетің шын ақауды жоғалтып алмау.
{strict_block}

2-МІНДЕТ — ТАЛДАУ (тек ақау расталса).
Мыналарды бағала:
- ақаудың түрі мен шамамен өлшемі (кадрдағы пропорция бойынша)
- қозғалысқа қауіптілігі (доңғалақ зақымы, рөлден айырылу қаупі, су жиналуы)
- қай жолақта/жолдың қай бөлігінде орналасқаны
- жедел шара қажет пе, әлде жоспарлы жөндеуге қалдыруға бола ма

3-МІНДЕТ — РЕСМИ МӘТІН.
«description_kk» өрісіне ресми іс қағаз стилінде 2-4 сөйлем жаз.
Эмоция, артық сын есім болмасын — тек факт. Мысал стилі:
«Жүру бөлігінің оң жақ жолағында диаметрі шамамен 60 см, тереңдігі
10 см-ге жуық шұңқыр анықталды. Ақау доңғалақ пен аспаны зақымдау
қаупін тудырады, жедел жөндеуді талап етеді.»
«description_ru» — сол мәтіннің орысша нұсқасы.

Жауапты ТЕК JSON түрінде бер, басқа мәтінсіз:
{{
  "is_real": true немесе false,
  "confidence": 0.0-1.0 аралығындағы сан,
  "defect_type": "pothole" | "crack" | "flood" | "streetlight_out" | "obstruction" | "none",
  "severity": "low" | "medium" | "high",
  "size_estimate": "шамамен өлшемі, мыс. 'диаметрі ~60 см, тереңдігі ~10 см'",
  "location_detail": "жолдың қай бөлігі, мыс. 'оң жақ жолақ, жиекке жақын'",
  "danger": "қозғалысқа қандай қауіп тудырады",
  "recommended_action": "ұсынылатын шара, мыс. 'жедел жамау (24 сағат ішінде)'",
  "urgency_days": жөндеу мерзімі күнмен (сан, мыс. 1, 7, 30),
  "description_kk": "ресми стильдегі 2-4 сөйлем (қазақша)",
  "description_ru": "сол мәтіннің орысша нұсқасы",
  "reason": "неге раста(ма)дың — қысқа негіздеме"
}}"""


# Қатаң режим. Бірінші саты (модель + уақыт бойынша растау) ТОЛЫҚТЫҚҚА
# бапталған: күмәнділердің бәрін жібереді. Екінші сатының міндеті — оның
# кері жағы: ДӘЛДІК. Сондықтан мұнда сұрақ «мұнда бір зақым бар ма?» емес,
# «бұл ресми жөндеу тапсырысын ашуға тұрарлық ақау ма?» болады.
STRICT_BLOCK = """
ҚАТАҢ ӨЛШЕМ (МІНДЕТТІ).
Жоғарыдағы «күмәнданса — раста» ережесі БҰЛ ЖОЛЫ ҚОЛДАНЫЛМАЙДЫ.
Сен екінші сатыдағы сарапшысың: бірінші саты күмәндінің бәрін жіберді,
ал сенің міндетің — солардың ішінен ресми ЖӨНДЕУ ТАПСЫРЫСЫН ашуға
тұрарлықтарын ғана қалдыру.

`is_real: true` деу үшін ЕКІ шарттың ЕКЕУІ де орындалуы керек.

1-ШАРТ. АСФАЛЬТТА, ДОҢҒАЛАҚ БАСАТЫН ЖЕРДЕ.
   Ақау асфальт төселген бетте тұруы керек.
   МАҢЫЗДЫ: «кадрдың оң жағында» немесе «сол жағында» деген сөз
   ақаудың жол сыртында тұрғанын БІЛДІРМЕЙДІ. Таңбалауы жоқ тар
   көшеде асфальттың БҮКІЛ ені — көлік жүретін бет, оның оң және сол
   шеті де доңғалақ басатын жер. Ондағы ақау бұл шартқа СӘЙКЕС КЕЛЕДІ.
   Тек мыналар сәйкес КЕЛМЕЙДІ:
     - асфальт төселмеген жиек: топырақ, қиыршық тас, шөп
     - бордюр тасының өзі, науа (арық), тротуар
     - ғимарат, аула, жол емес кез келген бет

2-ШАРТ. ЖАБЫННЫҢ НАҚТЫ БҰЗЫЛУЫ, ЕЛЕУЛІ ӨЛШЕМДЕ.
   Асфальттың тұтастығы жоғалған әрі ол доңғалаққа қауіп төндіретіндей:
     - шұңқыр, ойық, қопарылған аймақ
     - асфальт үгітіліп, астыңғы қиыршық тас/топырақ көрініп тұр
     - шөгіп кеткен немесе үгітіліп жатқан жамау — бұл ДА бұзылу
     - тығыз торлы жарық (асфальт бөлшектеніп жатыр)
   Өлшемі шамамен доңғалақ енінен кем болмауы керек (~30 см-ден үлкен).

   Мыналар бұл шартқа сай КЕЛМЕЙДІ:
     - беті ТЕГІС, жиегі бүтін жамау (жөндеу қажет емес)
     - тек түс өзгерісі, дақ, шаң, су ізі, жеке жіңішке жарық
     - ұсақ қажалу, беткі кедір-бұдыр

Екі шарттың бірі күмәнді болса — `is_real: false` қой да, `reason`
өрісінде ҚАЙСЫ шарт орындалмағанын нақты жаз.
"""


@dataclass
class Verdict:
    """ИИ сарапшысының қорытындысы."""

    is_real: bool
    confidence: float = 0.0
    defect_type: str = ""
    severity: str = ""
    size_estimate: str = ""
    location_detail: str = ""
    danger: str = ""
    recommended_action: str = ""
    urgency_days: int = 0
    description_kk: str = ""
    description_ru: str = ""
    reason: str = ""
    model: str = ""
    elapsed_ms: int = 0
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "is_real": self.is_real,
            "confidence": round(self.confidence, 3),
            "defect_type": self.defect_type,
            "severity": self.severity,
            "size_estimate": self.size_estimate,
            "location_detail": self.location_detail,
            "danger": self.danger,
            "recommended_action": self.recommended_action,
            "urgency_days": self.urgency_days,
            "description_kk": self.description_kk,
            "description_ru": self.description_ru,
            "reason": self.reason,
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
        }


def _encode_image(image_path: Path) -> Optional[str]:
    """Суретті base64-ке айналдыру (алдын ала кішірейтіп)."""
    image = cv2.imread(str(image_path))
    if image is None:
        return None

    height, width = image.shape[:2]
    longest = max(height, width)
    if longest > MAX_IMAGE_SIDE:
        scale = MAX_IMAGE_SIDE / float(longest)
        image = cv2.resize(
            image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA
        )

    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        return None
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _extract_json(text: str) -> Optional[dict]:
    """Модель жауабынан JSON-ды шығару (айналасында артық мәтін болуы мүмкін)."""
    text = (text or "").strip()
    if not text:
        return None

    # ```json ... ``` қоршауын алып тастау
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


class QuotaExceeded(Exception):
    """Модельдің тегін лимиті бітті (HTTP 429)."""


class AIVerifier:
    """Көру моделі арқылы детекцияны растау."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.provider = (cfg.ai_provider or "gemini").lower()
        self.base_url = cfg.ai_base_url
        self._session = requests.Session()
        self._failures = 0

        # Кілттер кезегі. Әр кілттің тегін лимиті БӨЛЕК, сондықтан
        # біреуі 429 берсе — келесісіне ауысамыз. Модельді ауыстырудан
        # бұрын осыны істейміз: кілт ауыстыру арзанырақ (сол модель қалады).
        self.keys: list[str] = [k for k in (cfg.ai_api_keys or ()) if k]
        if not self.keys and cfg.ai_api_key:
            self.keys = [cfg.ai_api_key]
        self._key_index = 0

        # Тегін лимит біткенде тоқтап қалмау үшін модельдер кезегі.
        # Біреуі 429 берсе, келесісіне автоматты ауысады — далалық тест
        # ортасында жүйе үнсіз өшіп қалмауы керек.
        self.models: list[str] = []
        for name in [cfg.ai_model, *(cfg.ai_model_fallbacks or [])]:
            if name and name not in self.models:
                self.models.append(name)
        self._model_index = 0

        self.checked = 0
        self.rejected = 0

    @property
    def model(self) -> str:
        if self._model_index < len(self.models):
            return self.models[self._model_index]
        return self.models[-1] if self.models else ""

    @property
    def api_key(self) -> str:
        if self._key_index < len(self.keys):
            return self.keys[self._key_index]
        return self.keys[-1] if self.keys else ""

    @property
    def exhausted(self) -> bool:
        """Барлық кілт пен модельдің лимиті бітті ме."""
        return self._model_index >= len(self.models)

    def _on_quota(self) -> bool:
        """429 келгенде: алдымен келесі КІЛТ, ол бітсе — келесі МОДЕЛЬ.

        False қайтарса — ауысатын ештеңе қалмады.
        """
        if self._key_index + 1 < len(self.keys):
            self._key_index += 1
            log.warning(
                "Лимит бітті — %d-кілтке ауысамыз (модель: %s)",
                self._key_index + 1, self.model,
            )
            return True

        # Барлық кілт бітті — модельді ауыстырып, кілттерді басынан бастаймыз
        self._key_index = 0
        self._model_index += 1
        if self.exhausted:
            log.warning(
                "Барлық кілт пен модельдің тәуліктік лимиті бітті — "
                "екінші саты уақытша өшірілді. Жүйе жұмысын ЖАЛҒАСТЫРАДЫ, "
                "тек жалған детекцияны сүзу болмайды. Лимит тәулік сайын жаңарады."
            )
            return False
        log.warning("Барлық кілт бітті — келесі модельге ауысамыз: %s", self.model)
        return True

    @property
    def enabled(self) -> bool:
        return bool(
            self.cfg.ai_verify
            and self.keys
            and self.models
            and not self.exhausted
            and self._failures < 5
        )

    # ---------- сұрау ----------

    def _ask_gemini(self, prompt: str, image_b64: str) -> Optional[str]:
        url = GEMINI_URL.format(model=self.model)

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                ]
            }],
            "generationConfig": {
                "temperature": 0.1,
                # Gemini 2.5 модельдерінде «ойлану» (thinking) токендері де осы
                # шектен алынады. Шек тар болса, модель бүкіл бюджетті ойлануға
                # жұмсап, жауапты жарты жолда үзіп жібереді — сондықтан кең аламыз.
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
                # Ойлануды өшіреміз: бұл тапсырма үшін қажет емес, әрі
                # жауап екі есе жылдам келеді.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }

        response = self._session.post(
            url, params={"key": self.api_key}, json=payload, timeout=self.cfg.ai_timeout
        )
        if response.status_code == 429:
            raise QuotaExceeded(self.model)
        if response.status_code >= 400:
            log.warning("ИИ қатесі (%s): %s", response.status_code, response.text[:200])
            return None

        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            log.warning("ИИ жауабының пішімі күтпеген: %s", str(data)[:200])
            return None

    def _ask_openai_compatible(self, prompt: str, image_b64: str) -> Optional[str]:
        base = (self.base_url or "https://openrouter.ai/api/v1").rstrip("/")
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 500,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            }],
        }

        response = self._session.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=self.cfg.ai_timeout,
        )
        if response.status_code >= 400:
            log.warning("ИИ қатесі (%s): %s", response.status_code, response.text[:200])
            return None

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            log.warning("ИИ жауабының пішімі күтпеген: %s", str(data)[:200])
            return None

    # ---------- негізгі ----------

    def _verify_once(
        self,
        image_path: Path,
        class_key: str,
        confidence: float,
        address: str = "",
        when: str = "",
        location_hint: str = "",
    ) -> Optional[Verdict]:
        """Дәлел фотосын ИИ-ге тексертіп, шешім қайтару.

        МАҢЫЗДЫ: мұнда ӨҢДЕЛМЕГЕН (белгіленбеген) фото берілуі керек.
        Бұрын белгіленген фото берілетін, ал ондағы ақаудың үстінде
        жартылай мөлдір ҚЫЗЫЛ бояу тұратын. Нәтижесінде ИИ нағыз
        шұңқырды «жол бетіндегі дақ немесе түс өзгерісі» деп жоққа
        шығаратын — ол шын мәнінде БІЗДІҢ бояуымызды сипаттайтын.
        Ақаудың орны енді сөзбен (`location_hint`) беріледі.

        None қайтарса — ИИ пікір білдірмеді (кілт жоқ / қызмет жауап бермеді).
        Ол жағдайда шешім YOLO-да қалады.
        """
        if not self.enabled:
            return None

        image_b64 = _encode_image(image_path)
        if image_b64 is None:
            return None

        category = categories.get(class_key)
        prompt = PROMPT.format(
            defect_kk=category.kk,
            defect_ru=category.ru,
            confidence=confidence,
            address=address or "нақтыланбаған",
            when=when or "белгісіз",
            location_hint=location_hint or "кадрдың жол бөлігінде",
            strict_block=STRICT_BLOCK if self.cfg.ai_strict else "",
        )

        started = time.time()
        text: Optional[str] = None

        # Лимит біткен кілт/модельдерді айналып өтеміз
        for _ in range(len(self.models) * max(1, len(self.keys))):
            try:
                if self.provider == "gemini":
                    text = self._ask_gemini(prompt, image_b64)
                else:
                    text = self._ask_openai_compatible(prompt, image_b64)
                break
            except QuotaExceeded:
                if not self._on_quota():
                    return None
                continue
            except requests.RequestException as exc:
                self._failures += 1
                log.warning("ИИ-ге қосылу сәтсіз (%d-рет): %s", self._failures, exc)
                if self._failures >= 5:
                    log.warning("ИИ-тексеру уақытша өшірілді.")
                return None

        if not text:
            self._failures += 1
            return None

        parsed = _extract_json(text)
        if not parsed:
            log.warning("ИИ жауабы JSON емес (%d таңба): %s", len(text), text[:400])
            return None

        self._failures = 0
        self.checked += 1

        try:
            urgency = int(float(parsed.get("urgency_days") or 0))
        except (TypeError, ValueError):
            urgency = 0

        verdict = Verdict(
            is_real=bool(parsed.get("is_real", True)),
            confidence=float(parsed.get("confidence", 0.0) or 0.0),
            defect_type=str(parsed.get("defect_type", "")),
            severity=str(parsed.get("severity", "")),
            size_estimate=str(parsed.get("size_estimate", "")),
            location_detail=str(parsed.get("location_detail", "")),
            danger=str(parsed.get("danger", "")),
            recommended_action=str(parsed.get("recommended_action", "")),
            urgency_days=urgency,
            description_kk=str(parsed.get("description_kk", "")),
            description_ru=str(parsed.get("description_ru", "")),
            reason=str(parsed.get("reason", "")),
            model=self.model,
            elapsed_ms=int((time.time() - started) * 1000),
            raw=parsed,
        )

        if not verdict.is_real:
            self.rejected += 1

        log.info(
            "ИИ тексерді (%d мс): %s — %s (%.0f%%) · %s",
            verdict.elapsed_ms,
            "РАСТАДЫ" if verdict.is_real else "ЖОҚҚА ШЫҒАРДЫ",
            verdict.defect_type or "?",
            verdict.confidence * 100,
            verdict.reason[:80],
        )
        return verdict

    # ---------- бірнеше рет тексеру (дауыс беру) ----------

    def verify(
        self,
        image_path: Path,
        class_key: str,
        confidence: float,
        address: str = "",
        when: str = "",
        location_hint: str = "",
    ) -> Optional[Verdict]:
        """Фотоны БІРНЕШЕ РЕТ тексеріп, көпшілік дауыспен шешу.

        Неге керек: тілдік модельдің жауабы бір шақыруда тұрақсыз —
        шекарадағы жағдайда бір жүргізуде «ақау», екіншісінде «ақау емес»
        деуі мүмкін. Бір сұрақты бірнеше рет қойып, көпшілік дауысты
        алсақ, шешім әлдеқайда тұрақты болады (self-consistency).

        Бір ғана дауыс болса (`ai_votes = 1`) — мінез-құлық бұрынғыдай.
        """
        votes = max(1, int(self.cfg.ai_votes))
        results: list[Verdict] = []

        for index in range(votes):
            verdict = self._verify_once(
                image_path, class_key, confidence,
                address=address, when=when, location_hint=location_hint,
            )
            if verdict is None:
                break
            results.append(verdict)
            if votes > 1:
                log.info(
                    "  ИИ дауыс %d/%d: %s (%.0f%%)",
                    index + 1, votes,
                    "АҚИҚАТ" if verdict.is_real else "ЖАЛҒАН",
                    verdict.confidence * 100,
                )

        if not results:
            return None
        if len(results) == 1:
            return results[0]

        real = [v for v in results if v.is_real]
        fake = [v for v in results if not v.is_real]
        winner_pool = real if len(real) > len(fake) else fake
        is_real = len(real) > len(fake)

        # Мазмұнды сипаттаманы жеңген жақтың ЕҢ СЕНІМДІ дауысынан аламыз
        best = max(winner_pool, key=lambda v: v.confidence)
        merged = replace(
            best,
            is_real=is_real,
            confidence=sum(v.confidence for v in winner_pool) / len(winner_pool),
            elapsed_ms=sum(v.elapsed_ms for v in results),
        )
        merged.raw = {
            **best.raw,
            "votes": [
                {"is_real": v.is_real, "confidence": round(v.confidence, 3),
                 "reason": v.reason}
                for v in results
            ],
            "vote_summary": f"{len(real)} АҚИҚАТ / {len(fake)} ЖАЛҒАН",
        }
        log.info(
            "ИИ ҚОРЫТЫНДЫ ДАУЫС (%d рет): %s — %d АҚИҚАТ / %d ЖАЛҒАН (%.0f%%)",
            len(results), "РАСТАЛДЫ" if is_real else "ЖОҚҚА ШЫҒАРЫЛДЫ",
            len(real), len(fake), merged.confidence * 100,
        )
        return merged

    # ---------- диагностика ----------

    def list_models(self) -> list[str]:
        """Қолжетімді модельдер тізімі (doctor үшін)."""
        if self.provider != "gemini" or not self.api_key:
            return []
        try:
            response = self._session.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": self.api_key}, timeout=10,
            )
            response.raise_for_status()
            models = response.json().get("models", [])
            return [
                m["name"].split("/")[-1] for m in models
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
        except requests.RequestException:
            return []
