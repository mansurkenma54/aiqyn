"""AIQYN Vision — негізгі цикл.

Толық тізбек (бәрі осы программада өтеді):

    кадр -> детекция -> GPS сәйкестендіру -> дедупликация
         -> 5-10 сек дәлел (видео+фото) -> ресми санат
         -> мекенжай (reverse geocoding) -> карта сілтемесі
         -> қазақша/орысша ресми мәтін -> ДАЙЫН ҚҰЖАТ -> сайтқа POST

Сайт (AIQYN Portal) бұл процестің ешбір бөлігін қайталамайды.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import replace
from typing import Optional

import cv2
import numpy as np

from .aiverify import AIVerifier
from .config import Config
from .dedup import Deduplicator
from .detectors import Detection, DetectorBank, LaneDetector, LaneResult
from .detectors.confirm import _center_gap
from .document import build_document, new_event_id
from .evidence import EvidenceFiles, EvidenceRecorder
from .geocode import Geocoder
from .gps import GpsFix, create_gps
from .overlay import Hud, HudStats, fit_to_width
from .roadsnap import RoadSnapper
from .sender import PortalSender
from .sources import create_source
from .zones import ZoneRegistry

log = logging.getLogger(__name__)

WINDOW_NAME = "AIQYN Vision"

# Қозғалысты бағалау кішірейтілген сұр кадрда жүреді: 1080p-де optical flow
# 25 мс алады, ал 480px-де 4 мс. Дәлдігі bbox үшін жеткілікті.
FLOW_WIDTH = 480


class _DetectRequest:
    __slots__ = ("image", "gray", "ts")

    def __init__(self, image, gray, ts):
        self.image = image
        self.gray = gray
        self.ts = ts


class _DetectResult:
    __slots__ = ("detections", "confirmed", "image", "gray", "ts", "took_ms")

    def __init__(self, detections, confirmed, image, gray, ts, took_ms):
        self.detections = detections
        self.confirmed = confirmed
        self.image = image
        self.gray = gray
        self.ts = ts
        self.took_ms = took_ms


# Нысанның ортасы өз енінен осынша есе жылжыса да — сол нысан деп санаймыз
# (detectors/confirm.py ішіндегі бақылаушымен бірдей шама).
TRACK_CENTER_GAP = 2.2

# Нысанның ауданы бір қадамда осыншадан артық өссе — бұл сол нысан емес
AREA_JUMP_LIMIT = 4.0


def _evidence_score(confidence: float, area_frac: float) -> float:
    """«Бұл кадр дәлел фотосына қаншалықты жарайды» бағасы.

    Тек сенімділікпен таңдасақ, ақау АЛҒАШ көрінген — ең алыс, кадрда
    бірнеше пиксель болатын — кадр құжатқа түсіп қалады. Оператор да,
    жауапты орган да онда ештеңе көрмейді.

    Сондықтан ақаудың кадрдағы ӨЛШЕМІН де есепке аламыз: көлік жақындаған
    сайын ақау үлкейеді, ал үлкен әрі сенімді кадр — ең жақсы дәлел.
    Коэффициент шектеулі (ең көбі 2 есе), сондықтан анық емес, бірақ
    үлкен нысан жоғары сенімділікті ешқашан жеңіп кетпейді.
    """
    size_gain = 1.0 + min(1.0, max(0.0, area_frac) / 0.02)
    return float(confidence) * size_gain


class DetectWorker:
    """YOLO-ны БӨЛЕК АҒЫНДА жүргізетін қабат.

    Неге керек: CPU-да бір кадрды талдау ~350 мс алады. Егер оны негізгі
    циклде жасасақ, видео сол жылдамдықпен — секундына 3-7 кадр — ойналады,
    яғни көрініс «баяу түсірілім» сияқты болады.

    Бұл жерде негізгі цикл кадрды ОҚИДЫ және ЭКРАНҒА ШЫҒАРАДЫ (30 кадр/сек),
    ал талдау фонда өз қарқынымен жүреді. Талдаушы бос болмаса, кадр жай
    ғана өткізіп жіберіледі — кезекке жиналмайды, сондықтан көрініс ешқашан
    артта қалмайды.
    """

    def __init__(self, bank, min_interval_sec: float = 0.0):
        self._bank = bank
        self._min_interval = max(0.0, min_interval_sec)
        self._requests: queue.Queue = queue.Queue(maxsize=1)
        self._results: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._busy = threading.Event()
        self._last_submit = 0.0
        self._thread: Optional[threading.Thread] = None
        self.processed = 0
        self.last_took_ms = 0.0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="aiqyn-detect", daemon=True)
        self._thread.start()

    def submit(self, image: np.ndarray, gray: np.ndarray, ts: float,
               wait: bool = False, timeout: float = 10.0) -> bool:
        """Талдауға кадр беру. Талдаушы бос емес болса — False.

        wait=True — талдаушы босағанша КҮТЕМІЗ (кадр тасталмайды). Бұл тек
        видеофайл үшін: файлда «нақты уақыттан қалып қою» деген ұғым жоқ,
        сондықтан бір де бір кадрды жіберіп алудың қажеті жоқ.
        """
        if self._stop.is_set():
            return False
        if self._busy.is_set():
            if not wait:
                return False
            deadline = time.time() + max(0.0, timeout)
            while self._busy.is_set() and not self._stop.is_set():
                if time.time() > deadline:
                    return False
                time.sleep(0.002)
            if self._stop.is_set():
                return False
        now = time.time()
        if self._min_interval and now - self._last_submit < self._min_interval:
            if not wait:
                return False
            time.sleep(self._min_interval - (now - self._last_submit))
            now = time.time()
        try:
            self._requests.put_nowait(_DetectRequest(image, gray, ts))
        except queue.Full:
            return False
        self._busy.set()
        self._last_submit = now
        return True

    def poll(self) -> Optional[_DetectResult]:
        try:
            return self._results.get_nowait()
        except queue.Empty:
            return None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                request = self._requests.get(timeout=0.2)
            except queue.Empty:
                continue
            started = time.time()
            try:
                detections, _lane, context = self._bank.process(request.image)
                confirmed = context.get("confirmed", detections)
            except Exception as exc:                       # noqa: BLE001
                log.error("Талдау қатесі: %s", exc)
                detections, confirmed = [], []
            took = (time.time() - started) * 1000
            self.last_took_ms = took
            self.processed += 1
            self._results.put(
                _DetectResult(detections, confirmed, request.image,
                              request.gray, request.ts, took)
            )
            self._busy.clear()

    def drain(self, handler, timeout: float = 15.0) -> int:
        """Кезекте қалған нәтижелерді өңдеп бітіру.

        Видео аяқталғанда талдаушының қолында әлі 1-2 кадр болуы мүмкін.
        Оларды тастап кетсек, видеоның ЕҢ СОҢЫНДАҒЫ ақау есепке ілікпей
        қалады — қысқа демо-видеода бұл байқалады.
        """
        deadline = time.time() + max(0.0, timeout)
        handled = 0
        while time.time() < deadline:
            result = self.poll()
            if result is not None:
                try:
                    handler(result)
                    handled += 1
                except Exception as exc:                   # noqa: BLE001
                    log.error("Соңғы кадрды өңдеу қатесі: %s", exc)
                continue
            if not self._busy.is_set():
                break
            time.sleep(0.01)
        return handled

    def stop(self) -> None:
        self._stop.set()
        self._busy.clear()
        if self._thread is not None:
            self._thread.join(timeout=3.0)


class VisionPipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg

        self.source = create_source(cfg)
        self.gps = create_gps(cfg)
        self.detectors = DetectorBank(cfg)
        self.geocoder = Geocoder(cfg)
        self.dedup = Deduplicator(cfg)
        self.sender = PortalSender(cfg)
        self.zones = ZoneRegistry(cfg)
        self.verifier = AIVerifier(cfg)
        self.snapper = RoadSnapper(cfg)
        self.hud = Hud(cfg)
        self.recorder = EvidenceRecorder(
            cfg,
            on_ready=self._on_evidence_ready,
            annotator=self._annotate_evidence,
        )

        # Дәлелі әлі жиналып жатқан оқиғалар: {event_id: {class_key, bbox}}.
        # Солардың анығырақ кадрын тауып, құжаттағы фотоны жаңартамыз.
        self._open_events: dict[str, dict] = {}
        self._open_lock = threading.Lock()

        self._evidence_hud = Hud(cfg)
        self._evidence_hud.show_debug = False
        self._evidence_hud.show_captions = True

        # статистика
        self.frames_seen = 0
        self.frames_analysed = 0
        self.detections_total = 0
        self.documents_built = 0
        self.skipped_in_zone = 0
        self.ai_rejected = 0
        self.skipped_too_small = 0
        self._fps = 0.0
        self._last_frame_ts = 0.0
        self._portal_online = False
        self._portal_checked_at = 0.0
        self._running = False

        self._detect_fps = 0.0
        self._progress = 0.0
        self._paused = False

        # Графикалық қолданба үшін ілмектер (консольде қолданылмайды).
        # Кадр ШИКІ күйінде беріледі — қабатты қолданба өзі, кішірейтілген
        # кадрға салады (әлдеқайда жылдам).
        #   frame_callback(image, detections, stats) — әр кадр
        #   detect_callback(detections, image)       — жаңа талдау нәтижесі
        #   event_started_callback(event_id, det, thumb) — ақау тіркелді
        #   event_dropped_callback(event_id, reason) — құжат ҚҰРЫЛМАДЫ
        #   event_callback(document, files)          — құжат дайын
        self.frame_callback = None
        self.detect_callback = None
        self.event_started_callback = None
        self.event_dropped_callback = None
        self.event_callback = None

    def _notify_dropped(self, event_id: str, reason: str) -> None:
        """Оқиға құжатқа айналмағанын қолданбаға хабарлау.

        Мұнсыз тізімдегі жазба «дәлел жазылуда» күйінде мәңгі қалып,
        оператор неге құжат шықпағанын түсінбейді.
        """
        if self.event_dropped_callback is None:
            return
        try:
            self.event_dropped_callback(event_id, reason)
        except Exception as exc:
            log.debug("Оқиға жойылу callback қатесі: %s", exc)

    # --------------------------------------------------------- басқару ---

    def set_paused(self, value: bool) -> None:
        self._paused = bool(value)

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def progress(self) -> float:
        """Видеофайлдың қаралған үлесі (0..1). Тірі ағында әрқашан 0."""
        return self._progress

    def request_stop(self) -> None:
        """Циклді сырттан тоқтату (қолданбадағы «Тоқтату» түймесі)."""
        self._running = False

    # ============================================================
    #  Дәлел фотосын белгілеу
    # ============================================================

    def _annotate_evidence(self, image: np.ndarray, meta: dict) -> Optional[np.ndarray]:
        """Дәлел фотосына ақауды, жол шекараларын және деректерді салу.

        Ресми құжатқа түсетін фото «жай ғана жол суреті» болмауы керек:
        ақау қай жерде екені, координатасы мен уақыты дәл сол суретте
        көрініп тұруы тиіс.
        """
        bbox = (meta.get("extra") or {}).get("bbox")
        if not bbox:
            return None

        frame_shape = meta.get("frame_shape")
        height, width = image.shape[:2]

        # Негізгі pipeline дәл detection frame-ді береді. Масштабтау қорғанысы
        # annotator-ды басқа caller бөлек ажыратымдылықпен шақырса ғана керек.
        if frame_shape:
            source_h, source_w = frame_shape[:2]
            scale_x = width / float(max(1, source_w))
            scale_y = height / float(max(1, source_h))
        else:
            scale_x = scale_y = 1.0

        x1, y1, x2, y2 = bbox
        scaled = (
            max(0, int(x1 * scale_x)), max(0, int(y1 * scale_y)),
            min(width, int(x2 * scale_x)), min(height, int(y2 * scale_y)),
        )

        detection = Detection(
            class_key=meta["class_key"],
            confidence=meta["confidence"],
            bbox=scaled,
            detector=meta.get("detector", "yolo"),
        )

        try:
            # Әр evidence фотосы тәуелсіз және бірнеше writer thread қатар
            # жұмыс істей алады. Ортақ EMA state қолданбаймыз.
            lane = LaneDetector(max_missed_frames=0).detect(image)
        except Exception:
            lane = None

        return self._evidence_hud.render(
            image,
            [detection],
            lane,
            meta.get("fix"),
            HudStats(),
            captured_at=meta.get("detected_at"),
        )

    # ============================================================
    #  Шешім ізі (аудит)
    # ============================================================

    def _write_verdict(self, files: "EvidenceFiles", meta: dict,
                       verdict, decision: str, reason: str) -> None:
        """Әр ҮМІТКЕР бойынша толық шешім ізін жазу.

        Тек тіркелгендер емес, тіркелМЕГЕНдер де жазылады: оператор да,
        әзірлеуші де «неге бұл құжат ашылмады?» деген сұраққа жауап таба
        алуы керек. Көрсетілім видеосы да осы файлдардан құрылады —
        ойдан шығарылған сан жоқ.
        """
        try:
            import json as _json
            payload = {
                "event_id": files.event_id,
                "class_key": meta.get("class_key"),
                "yolo_confidence": meta.get("confidence"),
                "area_frac": meta.get("area_frac"),
                "bbox": (meta.get("extra") or {}).get("bbox"),
                "frame_shape": list(meta.get("frame_shape") or []),
                "min_area_frac": self.cfg.min_event_area_frac,
                "detected_at": meta.get("detected_at"),
                "decision": decision,                # registered | too_small | ai_rejected
                "reason": reason,
                "ai": verdict.as_dict() if verdict else None,
                "ai_votes": (verdict.raw or {}).get("votes") if verdict else None,
                "ai_vote_summary": (verdict.raw or {}).get("vote_summary") if verdict else None,
                "photo": files.photo.name,
            }
            files.photo.with_suffix(".verdict.json").write_text(
                _json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:                       # noqa: BLE001
            log.debug("Шешім ізі жазылмады: %s", exc)

    # ============================================================
    #  Дәлел фотосын жақсарту
    # ============================================================

    def _handle_result(self, result) -> None:
        """Талдау нәтижесін толық өңдеу: жаңа ақау + дәлел фотосын жақсарту."""
        self._handle_detections(result.confirmed, result.image, result.ts)
        self._improve_evidence(result.detections, result.image, result.ts)

    def _improve_evidence(self, detections: list[Detection], image: np.ndarray,
                          image_ts: float) -> None:
        """Жазылып жатқан оқиғаның анығырақ кадрын іздеу.

        Уақыт бойынша растау қабаты бір нысанды БІР-АҚ рет қайтарады —
        сондықтан оқиға ақау алғаш расталған кадрмен ашылады, ал ол кадрда
        ақау әлі алыс әрі кішкентай. Дәлел клипі жазылып жатқанда сол
        нысанның кейінгі (жақынырақ, анығырақ) кадрларын көріп отырамыз да,
        ең жақсысын құжаттың фотосы етіп қоямыз.
        """
        if not detections:
            return
        with self._open_lock:
            if not self._open_events:
                return
            open_events = list(self._open_events.items())

        now = None
        for event_id, state in open_events:
            # Дәлел клипі 5 секунд жазылады, ал ақау кадрда одан әлдеқайда
            # тез өтіп кетеді. Терезені шектемесек, сол клип жазылып жатқанда
            # көрінген БАСҚА ақау (сол класты) осы оқиғаға байланып қалады да,
            # құжатқа жат фото түседі. Өлшенді: ұсақ таңбаның құжатына 50
            # кадрдан кейінгі үлкен шұңқырдың фотосы түсіп кеткен.
            if now is None:
                now = image_ts
            if now - state.get("opened_at", now) > self.cfg.evidence_improve_sec:
                continue

            best = None
            best_score = -1.0
            for detection in detections:
                if detection.class_key != state["class_key"]:
                    continue
                # Сол нысан ба? Ортасының жылжуын өз өлшемімен салыстырамыз
                # (жақындаған сайын нысан жылдам жылжиды әрі үлкейеді,
                # сондықтан қабаттасу жарамайды).
                if _center_gap(state["bbox"], detection.bbox) > TRACK_CENTER_GAP:
                    continue
                # Нысан бір қадамда бірнеше есе үлкейіп кете алмайды —
                # ондай «секіру» басқа нысанға ауысып кеткенді білдіреді.
                if detection.area > state.get("area", 1) * AREA_JUMP_LIMIT:
                    continue
                score = _evidence_score(
                    detection.confidence, detection.area_frac(image.shape)
                )
                if score > best_score:
                    best, best_score = detection, score

            if best is None:
                continue

            updated = self.recorder.update_best(
                event_id, image.copy(), best_score,
                meta_patch={
                    "confidence": best.confidence,
                    "area_frac": best.area_frac(image.shape),
                    "frame_shape": image.shape,
                    "extra": {"bbox": list(best.bbox)},
                },
            )
            with self._open_lock:
                if event_id in self._open_events:
                    # Жақсарса да, жақсармаса да нысанның жаңа орны мен
                    # өлшемін есте сақтаймыз — әйтпесе ол кадрда жылжып
                    # кетіп, байланыс үзіледі.
                    self._open_events[event_id]["bbox"] = tuple(best.bbox)
                    self._open_events[event_id]["area"] = max(1, best.area)
            if updated:
                log.debug("Дәлел фотосы жаңарды: %s (%.0f%%)",
                          event_id, best.confidence * 100)

    # ============================================================
    #  Оқиға -> құжат  (дәлел дайын болғанда шақырылады)
    # ============================================================

    def _on_evidence_ready(self, files: EvidenceFiles, meta: dict) -> None:
        """Дәлел файлдары жазылып болғанда — толық құжат құрастырып, жіберу.

        Бұл БӨЛЕК АҒЫНДА орындалады, сондықтан мекенжайды анықтау
        (интернет сұрауы, ~1 сек) негізгі циклді тежемейді.
        """
        with self._open_lock:
            self._open_events.pop(files.event_id, None)

        try:
            fix: GpsFix = meta["fix"]

            # --- Координатаны жол осіне түсіру ---
            # GPS 5-15 м қателеседі, сондықтан белгі картада жолдың
            # үстінде емес, шөпте тұрып қалады. Мұнда (негізгі циклде емес)
            # жасалады — желі сұрауы кадр өңдеуді тежемейді.
            snap = self.snapper.snap(fix.lat, fix.lon)
            raw_lat, raw_lon = fix.lat, fix.lon

            if snap.snapped:
                fix = replace(fix, lat=snap.lat, lon=snap.lon)

                # Түзетілген нүкте жөндеу аймағына түсуі мүмкін — қайта тексереміз
                zone = self.zones.should_skip(fix.lat, fix.lon)
                if zone is not None:
                    self.skipped_in_zone += 1
                    log.info(
                        "Жолға түсіргеннен кейін «%s» аймағына түсті — құжат құрылмады: %s",
                        zone.name, files.event_id,
                    )
                    self._notify_dropped(
                        files.event_id, f"«{zone.name}» жөндеу аймағы")
                    return

            # Мекенжайды алдымен анықтаймыз — ИИ сарапшысына орынды да
            # беру керек, сонда талдауы нақтырақ болады
            address = self.geocoder.reverse(fix.lat, fix.lon)

            # --- ЕКІНШІ САТЫ: ИИ көру моделі талдап, қорытынды жазады ---
            # Мұнда шақырылатын себебі: дәлел фотосы дайын, әрі бұл бөлек
            # ағын — негізгі цикл тежелмейді.
            from datetime import datetime as _dt

            # ИИ-ге ӨҢДЕЛМЕГЕН фото беріледі. Белгіленген нұсқада ақаудың
            # үстінде жартылай мөлдір қызыл бояу бар — ИИ соны көріп,
            # нағыз шұңқырды «жол бетіндегі дақ» деп жоққа шығаратын.
            raw_photo = files.photo.with_name(f"{files.photo.stem}_raw.jpg")
            if not raw_photo.exists():
                raw_photo = files.photo

            verdict = self.verifier.verify(
                raw_photo,
                meta["class_key"],
                meta["confidence"],
                address=address.get("address_text", ""),
                when=_dt.fromtimestamp(meta["detected_at"]).strftime("%d.%m.%Y %H:%M"),
                location_hint=self._describe_location(
                    (meta.get("extra") or {}).get("bbox"), meta.get("frame_shape")
                ),
            )

            if verdict is not None and not verdict.is_real and self.cfg.ai_drop_rejected:
                self.ai_rejected += 1
                log.info(
                    "ИИ жоққа шығарды — құжат ҚҰРЫЛМАДЫ: %s (%s)",
                    files.event_id, verdict.reason[:100],
                )
                # Аудит үшін сақтаймыз: кейін модельді жақсарту материалы
                import json
                files.photo.with_suffix(".rejected.json").write_text(
                    json.dumps(
                        {
                            "event_id": files.event_id,
                            "class_key": meta["class_key"],
                            "yolo_confidence": meta["confidence"],
                            "ai": verdict.as_dict(),
                        },
                        ensure_ascii=False, indent=2,
                    ),
                    encoding="utf-8",
                )
                self._write_verdict(files, meta, verdict, "ai_rejected", verdict.reason)
                self._notify_dropped(files.event_id, verdict.reason[:120])
                return

            # --- ӨЛШЕМ СҮЗГІСІ (ИИ-ден КЕЙІН) ---
            # Екі сатыдан өткен ақау ресми жөндеу тапсырысын ашуға тұра ма?
            # Өлшем ақау ЕҢ ЖАҚЫН көрінген кадр бойынша алынады. Сүзгі ИИ-ден
            # кейін тұр: журналда әр үміткердің ИИ қорытындысы да қалады,
            # оператор неге тіркелмегенін көре алады. ИИ жоққа шығарғаны
            # АЛДЫМЕН есептеледі — әйтпесе қорытындыдағы «ИИ жоққа шығарды»
            # саны ұсақ әрі жалған ақауларды жоғалтып, 0 көрсететін.
            area_frac = float(meta.get("area_frac") or 0.0)
            if self.cfg.min_event_area_frac > 0 and area_frac < self.cfg.min_event_area_frac:
                self.skipped_too_small += 1
                log.info(
                    "Ұсақ ақау — құжат ашылмады: %s (%s, кадрдың %.2f%%-ы, шек %.2f%%)",
                    files.event_id, meta.get("class_key"),
                    area_frac * 100, self.cfg.min_event_area_frac * 100,
                )
                self._write_verdict(
                    files, meta, verdict, "too_small",
                    f"кадрдың {area_frac * 100:.2f}%-ы — тіркеу шегінен "
                    f"({self.cfg.min_event_area_frac * 100:.2f}%) төмен",
                )
                self._notify_dropped(files.event_id, "ұсақ ақау — тіркеу шегінен төмен")
                return

            # --- ЖӨНДЕУ УЧАСКЕСІ БОЙЫНША ТОПТАСТЫРУ ---
            # Осы жерде ақау барлық сүзгіден өтті: өлшемі де, ИИ де расталды.
            # Енді ғана «бұл жаңа жұмыс тапсырысы ма, әлде жақын маңдағы
            # тапсырысқа қосыла ма?» деген сұрақты шешеміз.
            if not self.dedup.check(meta["class_key"], fix.lat, fix.lon,
                                    trusted=bool(fix.trusted),
                                    ts=meta.get("detected_at")):
                log.info(
                    "Жақын маңдағы тапсырысқа қосылды: %s (%s, %.0f м радиус)",
                    files.event_id, meta["class_key"], self.cfg.dedup_radius_m,
                )
                self._write_verdict(
                    files, meta, verdict, "merged_nearby",
                    "жақын маңдағы жөндеу тапсырысына қосылды "
                    "(%.0f м радиус)" % self.cfg.dedup_radius_m,
                )
                self._notify_dropped(files.event_id, "жақын тапсырысқа қосылды")
                return
            self.dedup.remember(meta["class_key"], fix.lat, fix.lon, files.event_id,
                                ts=meta.get("detected_at"))

            document = build_document(
                cfg=self.cfg,
                event_id=files.event_id,
                class_key=meta["class_key"],
                confidence=meta["confidence"],
                area_frac=meta["area_frac"],
                fix=fix,
                address=address,
                detected_at=meta["detected_at"],
                detector_name=meta["detector"],
                video_seconds=files.duration_sec,
                has_video=files.video is not None,
                marked_on_road=meta.get("marked_on_road", False),
                extra={
                    **meta.get("extra", {}),
                    # Түпнұсқа GPS координатасы да сақталады — «түзетілген»
                    # дерек шынайы өлшемді жасырмауы керек
                    "gps_raw_lat": round(raw_lat, 6),
                    "gps_raw_lon": round(raw_lon, 6),
                    "road_snap": snap.as_dict(),
                },
                ai=verdict.as_dict() if verdict else None,
            )

            # Құжаттың көшірмесін дискіде де сақтаймыз (аудит/дебаг үшін)
            doc_path = files.photo.with_suffix(".json")
            import json
            doc_path.write_text(
                json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            self.documents_built += 1
            log.info(
                "ҚҰЖАТ ДАЙЫН: %s | %s | %s | сенімділік %.0f%%",
                document["event_id"],
                document["defect_type_official"],
                document["address_text"],
                document["confidence"] * 100,
            )

            self._write_verdict(
                files, meta, verdict, "registered",
                "барлық сатыдан өтті — ресми құжат ашылды",
            )

            self.sender.send(document, files.photo, files.video)

            if self.event_callback is not None:
                try:
                    self.event_callback(document, files)
                except Exception as exc:
                    log.debug("Оқиға callback қатесі: %s", exc)

        except Exception as exc:
            log.exception("Құжат құрастыру сәтсіз (%s): %s", files.event_id, exc)
            self._notify_dropped(files.event_id, f"қате: {exc}")

    # ============================================================
    #  Детекцияны оқиғаға айналдыру
    # ============================================================

    def _handle_detections(
        self, detections: list[Detection], image: np.ndarray, frame_ts: float
    ) -> None:
        if not detections:
            return

        # МАҢЫЗДЫ: кадрдың НАҚТЫ уақыты — ол ноутбукке келген уақыттан
        # ағын кідірісіне ерте. Осы түзетусіз координата 20-30 метрге жылжиды.
        latency = self.cfg.stream_latency_sec if self.source.is_live else 0.0
        real_ts = frame_ts - latency

        fix = self.gps.fix_at(real_ts)
        if fix is None:
            fix = GpsFix(
                lat=self.cfg.fallback_lat, lon=self.cfg.fallback_lon,
                ts=real_ts, source="fallback", trusted=False,
            )

        # Жөндеу аймағы: бүкіл кадр бойынша бір рет тексереміз —
        # координата барлық детекция үшін ортақ
        zone = self.zones.should_skip(fix.lat, fix.lon)
        if zone is not None:
            self.skipped_in_zone += len(detections)
            self.detections_total += len(detections)
            log.info(
                "Жөндеу аймағы «%s» — %d ақау есепке алынбады (%s)",
                zone.name, len(detections), zone.kind,
            )
            return

        for detection in detections:
            self.detections_total += 1

            # Орын бойынша топтастыру МҰНДА ЕМЕС, құжат сатысында жүреді.
            #
            # Неге: бұл жерде ақаудың нақты өлшемі әлі белгісіз (ол дәлел
            # жиналып біткенде ғана анықталады). Егер топтастыруды осында
            # жасасақ, ұсақ жалған детекция орынды «иеленіп» алады да,
            # сол жердегі ШЫН ақауға құжат ашылмай қалады.
            event_id = new_event_id()

            # Bbox дәл осы stream кадрында табылды. Кейін алынатын /shot.jpg
            # басқа уақыт/FOV болуы мүмкін, сондықтан ресми белгіленген фотоға
            # детекциямен пиксель-пиксель сәйкес кадрды ғана береміз.
            best_image = image.copy()

            area_frac = detection.area_frac(image.shape)
            self.recorder.start_event(
                event_id=event_id,
                best_image=best_image,
                score=_evidence_score(detection.confidence, area_frac),
                meta={
                    "class_key": detection.class_key,
                    "confidence": detection.confidence,
                    "area_frac": area_frac,
                    "fix": fix,
                    "detected_at": real_ts,
                    "detector": detection.detector,
                    "frame_shape": image.shape,   # bbox-ты фотоға масштабтау үшін
                    "marked_on_road": False,   # TODO(pilot): ЦС ГГ НИПД реестрімен салыстыру
                    "extra": {
                        "bbox": list(detection.bbox),
                        **detection.extra,
                    },
                },
            )

            # Дәлел жиналып жатқанда сол ақаудың анығырақ кадрын іздейміз
            with self._open_lock:
                self._open_events[event_id] = {
                    "class_key": detection.class_key,
                    "bbox": tuple(detection.bbox),
                    "area": max(1, detection.area),
                    "opened_at": real_ts,
                }

            log.info(
                "АҚАУ ТАБЫЛДЫ: %s (%.0f%%) @ %.5f, %.5f [%s] -> %s",
                detection.class_key, detection.confidence * 100,
                fix.lat, fix.lon, fix.source, event_id,
            )

            # Қолданбаға ДЕРЕУ хабарлаймыз. Толық құжат ~10-15 секундтан
            # кейін дайын болады (дәлел клипі + мекенжай + ИИ тексеруі),
            # ал оператор ақау табылғанын сол сәтте көруі керек.
            if self.event_started_callback is not None:
                try:
                    self.event_started_callback(
                        event_id, detection, self._thumbnail(image, detection.bbox)
                    )
                except Exception as exc:
                    log.debug("Оқиға басталу callback қатесі: %s", exc)

    @staticmethod
    def _describe_location(bbox, frame_shape) -> str:
        """Ақаудың кадрдағы орнын СӨЗБЕН сипаттау.

        ИИ-ге өңделмеген фото беріледі, сондықтан оған қай жерге қарау
        керегін айту қажет. Пиксель координатасы емес, адам түсінетін
        сипаттама беріледі — модель солай дәлірек жұмыс істейді.
        """
        if not bbox or not frame_shape:
            return "кадрдың жол бөлігінде"
        try:
            height, width = frame_shape[:2]
            x1, y1, x2, y2 = bbox
            cx = (x1 + x2) / 2.0 / max(1, width)
            cy = (y1 + y2) / 2.0 / max(1, height)
            horizontal = ("сол жақта" if cx < 0.38
                          else "оң жақта" if cx > 0.62 else "ортасында")
            vertical = ("жоғарғы бөлігінде (алыстау)" if cy < 0.55
                        else "төменгі бөлігінде (жақын)" if cy > 0.78
                        else "орта бөлігінде")
            share = (x2 - x1) * (y2 - y1) / float(max(1, width * height)) * 100
            return (f"кадрдың {horizontal}, {vertical}; "
                    f"кадр ауданының шамамен {share:.1f}%-ын алып тұр")
        except Exception:
            return "кадрдың жол бөлігінде"

    @staticmethod
    def _thumbnail(image: np.ndarray, bbox, width: int = 112) -> Optional[np.ndarray]:
        """Тізімде көрсетуге арналған кішкентай кесінді."""
        try:
            height, frame_w = image.shape[:2]
            x1, y1, x2, y2 = bbox
            pad_x = max(12, (x2 - x1) // 3)
            pad_y = max(12, (y2 - y1) // 3)
            x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
            x2, y2 = min(frame_w, x2 + pad_x), min(height, y2 + pad_y)
            if x2 - x1 < 8 or y2 - y1 < 8:
                return None
            crop = image[y1:y2, x1:x2]
            scale = width / float(crop.shape[1])
            return cv2.resize(
                crop, (width, max(1, int(crop.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
        except Exception:
            return None

    # ============================================================
    #  Көмекші
    # ============================================================

    def _update_fps(self, now: float) -> None:
        if self._last_frame_ts:
            delta = now - self._last_frame_ts
            if delta > 0:
                instant = 1.0 / delta
                self._fps = instant if self._fps == 0 else (self._fps * 0.9 + instant * 0.1)
        self._last_frame_ts = now

    def _check_portal(self, now: float) -> None:
        """Сайттың күйін ФОНДА тексеру.

        Бұрын ping() негізгі циклде шақырылатын. Сайт өшік тұрса,
        қосылуды күту 2 СЕКУНДҚА созылып, дәл сол уақытта видео қатып
        қалатын (өлшенді: 33 мс орнына 2067 мс). Енді тексеру бөлек
        ағында жүреді — цикл ешқашан тоқтамайды.
        """
        if now - self._portal_checked_at < 15.0:
            return
        self._portal_checked_at = now

        def probe():
            try:
                self._portal_online = self.sender.ping()
            except Exception:
                self._portal_online = False

        threading.Thread(target=probe, name="aiqyn-portal-ping", daemon=True).start()

    def _stats(self) -> HudStats:
        return HudStats(
            fps=self._fps,
            detect_fps=self._detect_fps,
            progress=self._progress,
            detections_total=self.detections_total,
            documents_sent=self.sender.sent_count,
            queued=self.sender.queued_count,
            active_recordings=self.recorder.active_count,
            is_night=self.detectors.is_night_now,
            timings_ms=self.detectors.timings_ms,
            portal_online=self._portal_online,
            gps_ok=self.gps.latest is not None,
        )

    @staticmethod
    def _propagate_detections(
        detections: list[Detection],
        previous_gray: Optional[np.ndarray],
        current_gray: Optional[np.ndarray],
        scale: float = 1.0,
    ) -> list[Detection]:
        """Талдау аралығындағы кадрларда bbox-ты optical flow арқылы жылжыту.

        Талдау ~350 мс алады, ал осы уақытта көлік жүріп кетеді. Түзетусіз
        белгі ақаудан артта қалып, жол бетінде «сырғанап» жүрер еді.

        `scale` — сұр кадрлардың түпнұсқаға қатысты өлшемі (0.25 деген —
        сұр кадр 4 есе кіші). bbox әрқашан ТҮПНҰСҚА координатасында келеді
        және сол күйінде қайтады.

        Feature жеткіліксіз болса ескі bbox-ты жорамалмен ұстамаймыз —
        оны бірден жасырған қауіпсіз.
        """
        if (
            not detections
            or previous_gray is None
            or current_gray is None
            or previous_gray.shape != current_gray.shape
        ):
            return []

        height, width = current_gray.shape[:2]
        inverse = 1.0 / max(1e-6, scale)
        propagated: list[Detection] = []

        for det in detections:
            x1, y1, x2, y2 = (int(v * scale) for v in det.bbox)
            x1, x2 = max(0, min(width - 1, x1)), max(0, min(width - 1, x2))
            y1, y2 = max(0, min(height - 1, y1)), max(0, min(height - 1, y2))
            if x2 - x1 < 6 or y2 - y1 < 6:
                continue

            # Қорапты сәл кеңейтеміз: жарық/жарықшақтың өзінде corner аз болса,
            # жанындағы асфальт текстурасы қозғалысты бағалауға көмектеседі.
            pad_x = max(4, int((x2 - x1) * 0.20))
            pad_y = max(4, int((y2 - y1) * 0.20))
            rx1, ry1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
            rx2, ry2 = min(width, x2 + pad_x), min(height, y2 + pad_y)
            roi = previous_gray[ry1:ry2, rx1:rx2]
            if roi.size == 0:
                continue

            points0 = cv2.goodFeaturesToTrack(
                roi,
                maxCorners=24,
                qualityLevel=0.015,
                minDistance=4,
                blockSize=5,
            )
            if points0 is None or len(points0) < 3:
                continue

            points0[:, 0, 0] += rx1
            points0[:, 0, 1] += ry1
            points1, status, error = cv2.calcOpticalFlowPyrLK(
                previous_gray,
                current_gray,
                points0,
                None,
                winSize=(21, 21),
                maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
            )
            if points1 is None or status is None:
                continue

            valid = status.reshape(-1).astype(bool)
            if error is not None:
                valid &= error.reshape(-1) < 40.0
            source_points = points0.reshape(-1, 2)[valid]
            target_points = points1.reshape(-1, 2)[valid]
            if len(source_points) < 3:
                continue

            matrix = None
            if len(source_points) >= 4:
                matrix, inliers = cv2.estimateAffinePartial2D(
                    source_points,
                    target_points,
                    method=cv2.RANSAC,
                    ransacReprojThreshold=3.0,
                )
                if matrix is not None and inliers is not None and int(inliers.sum()) < 3:
                    matrix = None

            if matrix is None:
                delta = np.median(target_points - source_points, axis=0)
                matrix = np.array(
                    [[1.0, 0.0, float(delta[0])], [0.0, 1.0, float(delta[1])]],
                    dtype=np.float64,
                )

            corners = np.array(
                [[x1, y1, 1.0], [x2, y1, 1.0], [x2, y2, 1.0], [x1, y2, 1.0]],
                dtype=np.float64,
            )
            moved = corners @ matrix.T
            if not np.isfinite(moved).all():
                continue

            nx1 = max(0, min(width - 1, int(round(float(moved[:, 0].min())))))
            ny1 = max(0, min(height - 1, int(round(float(moved[:, 1].min())))))
            nx2 = max(0, min(width - 1, int(round(float(moved[:, 0].max())))))
            ny2 = max(0, min(height - 1, int(round(float(moved[:, 1].max())))))
            new_area = max(0, nx2 - nx1) * max(0, ny2 - ny1)
            old_area = max(1, (x2 - x1) * (y2 - y1))
            area_ratio = new_area / float(old_area)
            if nx2 - nx1 < 5 or ny2 - ny1 < 5 or not (0.45 <= area_ratio <= 2.20):
                continue

            propagated.append(
                replace(
                    det,
                    # Түпнұсқа кадрдың координатасына қайтарамыз
                    bbox=(
                        int(round(nx1 * inverse)), int(round(ny1 * inverse)),
                        int(round(nx2 * inverse)), int(round(ny2 * inverse)),
                    ),
                    extra={**det.extra, "hud_tracked": True},
                )
            )

        return propagated

    @staticmethod
    def _flow_gray(image: np.ndarray) -> tuple[np.ndarray, float]:
        """Optical flow үшін кішірейтілген сұр кадр + оның масштабы."""
        height, width = image.shape[:2]
        scale = min(1.0, FLOW_WIDTH / float(max(1, width)))
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if scale < 1.0:
            gray = cv2.resize(
                gray, (int(width * scale), int(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        return gray, scale

    # ============================================================
    #  Негізгі цикл
    # ============================================================

    def run(self) -> None:
        log.info("=" * 68)
        log.info("AIQYN Vision іске қосылуда  |  көзі: %s", self.cfg.source)
        log.info("=" * 68)

        self.source.open()
        self.recorder.set_fps(self.source.fps)   # клип ұзақтығы көздің нақты FPS-іне байланысты
        # Телефон автоматты табылған болса, мекенжай өзгерген —
        # GPS те жаңа мекенжайдан оқуы керек
        if hasattr(self.gps, "refresh_urls"):
            self.gps.refresh_urls()
        self.gps.start()
        self.sender.start_retry_loop()
        self.zones.start(self.cfg.zones_refresh_sec)
        self.detectors.warmup()

        if self.zones.count:
            log.info("Белсенді жөндеу аймақтары: %d — олардың ішіндегі ақаулар еленбейді",
                     self.zones.count)

        if self.verifier.enabled:
            log.info("ИИ-тексеруші қосулы: %s / %s (екінші саты)",
                     self.cfg.ai_provider, self.cfg.ai_model)
        else:
            log.info("ИИ-тексеруші өшірулі — .env ішіне AIQYN_AI_API_KEY қойсаңыз, "
                     "жалған детекциялар автоматты сүзіледі")

        pending = self.sender.pending_count()
        if pending:
            log.info("Кезекте %d жіберілмеген құжат бар — қайта жіберіледі.", pending)

        if self.cfg.show_preview:
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(WINDOW_NAME, self.cfg.preview_width, int(self.cfg.preview_width * 0.62))
            log.info("Басқару:  [q] шығу   [d] панельді жасыру/көрсету   [space] кідірту")

        display_detections: list[Detection] = []
        last_lane: Optional[LaneResult] = None
        display_gray: Optional[np.ndarray] = None
        flow_scale = 1.0
        detect_times: list[float] = []
        paused = False
        self._running = True
        started_at = time.time()

        # Талдау бөлек ағында жүре ме? Тірі ағында да, файлда да — иә.
        # Сол арқылы экрандағы көрініс толық жылдамдықпен жүреді.
        worker: Optional[DetectWorker] = None
        if self.cfg.async_detect:
            worker = DetectWorker(
                self.detectors,
                min_interval_sec=(1.0 / self.cfg.detect_max_hz)
                if self.cfg.detect_max_hz > 0 else 0.0,
            )
            worker.start()
            log.info("Талдау бөлек ағында жүреді — видео толық жылдамдықпен ойналады.")

        # Видеофайл — тірі ағын емес: кадр тастаудың мәні жоқ, бәрін талдаймыз.
        full_scan = bool(
            worker is not None and not self.source.is_live and self.cfg.file_full_scan
        )
        if full_scan:
            log.info("Толық талдау режимі: видеофайлдың ӘР кадры талданады "
                     "(бір де бір ақау өткізіп жіберілмейді).")

        total_frames = getattr(self.source, "total_frames", 0) or 0

        try:
            while self._running:
                if self.cfg.max_duration_sec > 0 and \
                        time.time() - started_at >= self.cfg.max_duration_sec:
                    log.info("Белгіленген уақыт (%.0f сек) аяқталды.", self.cfg.max_duration_sec)
                    break

                if paused or self._paused:
                    if self.cfg.show_preview and self._wait_key() == "quit":
                        break
                    if not self.cfg.show_preview:
                        time.sleep(0.03)
                    continue

                frame = self.source.read()
                if frame is None:
                    log.info("Видео ағыны аяқталды.")
                    break

                # Қиюды ЕҢ БАСЫНДА жасаймыз: сол арқылы детекция да, дәлел
                # видеосы да, экрандағы көрініс те бір кадрмен жұмыс істейді
                frame.image = self.cfg.crop(frame.image)

                self.frames_seen += 1
                if total_frames:
                    self._progress = min(1.0, frame.index / float(total_frames))
                now = time.time()
                self._update_fps(now)
                self._check_portal(now)

                self.recorder.feed(frame.image)

                wants_canvas = self.cfg.show_preview or self.frame_callback is not None

                if worker is not None:
                    # ---------- АСИНХРОНДЫ РЕЖИМ ----------
                    current_gray, flow_scale = self._flow_gray(frame.image)

                    # Тірі ағында талдаушы бос болмаса кадр тасталады —
                    # көрініс артта қалмауы керек. ВИДЕОФАЙЛДА олай емес:
                    # қалып қоятын «нақты уақыт» жоқ, сондықтан әр кадрды
                    # талдауға береміз. Өлшенді: 41/126 -> 126/126 кадр,
                    # 3 -> 6 ақау. Нәтиже әр жүргізуде бірдей болады.
                    worker.submit(frame.image, current_gray, frame.ts,
                                  wait=full_scan)

                    result = worker.poll()
                    if result is not None:
                        self.frames_analysed += 1
                        detect_times.append(result.took_ms)
                        if len(detect_times) > 30:
                            detect_times.pop(0)
                        self._detect_fps = (
                            1000.0 / (sum(detect_times) / len(detect_times))
                            if detect_times else 0.0
                        )

                        self._handle_detections(
                            result.confirmed, result.image, result.ts
                        )
                        # Расталған ақаудың анығырақ кадрын іздейміз:
                        # растаушы бір нысанды бір рет қана қайтарады,
                        # ал ЕҢ ЖАҚСЫ кадр әдетте одан кейін келеді.
                        self._improve_evidence(result.detections, result.image, result.ts)

                        # Нәтиже ЕСКІ кадрға тиесілі — оны ағымдағы кадрға
                        # жылжытамыз, әйтпесе белгі ақаудан артта қалады
                        moved = self._propagate_detections(
                            result.detections, result.gray, current_gray, flow_scale
                        )
                        display_detections = moved or result.detections

                        if self.detect_callback is not None and result.detections:
                            try:
                                self.detect_callback(result.detections, result.image)
                            except Exception as exc:
                                log.debug("Детекция callback қатесі: %s", exc)
                    else:
                        display_detections = self._propagate_detections(
                            display_detections, display_gray, current_gray, flow_scale
                        )

                    display_gray = current_gray
                else:
                    # ---------- ЕСКІ СИНХРОНДЫ РЕЖИМ (тек --sync) ----------
                    current_gray, flow_scale = (
                        self._flow_gray(frame.image) if wants_canvas else (None, 1.0)
                    )
                    analyse_now = self.frames_seen % max(1, int(self.cfg.frame_stride)) == 0
                    if analyse_now:
                        self.frames_analysed += 1
                        detections, lane, context = self.detectors.process(frame.image)
                        display_detections, last_lane = detections, lane
                        self._handle_detections(
                            context.get("confirmed", detections), frame.image, frame.ts
                        )
                        self._improve_evidence(detections, frame.image, frame.ts)
                    elif wants_canvas:
                        display_detections = self._propagate_detections(
                            display_detections, display_gray, current_gray, flow_scale
                        )
                    display_gray = current_gray

                if wants_canvas:
                    # Қолданбаға ШИКІ кадр беріледі: қабат кішірейтілген
                    # көрініске салынады, сондықтан 1080p-ді бос жерге
                    # боямаймыз (кадрына ~10 мс үнемделеді).
                    if self.frame_callback is not None:
                        try:
                            self.frame_callback(
                                frame.image, display_detections, self._stats()
                            )
                        except Exception as exc:
                            log.debug("Кадр callback қатесі: %s", exc)

                    if self.cfg.show_preview:
                        latency = self.cfg.stream_latency_sec if self.source.is_live else 0.0
                        captured_at = frame.ts - latency
                        display_fix = self.gps.fix_at(captured_at) or self.gps.latest
                        canvas = self.hud.render(
                            frame.image,
                            display_detections,
                            last_lane,
                            display_fix,
                            self._stats(),
                            captured_at=captured_at,
                        )
                        cv2.imshow(WINDOW_NAME,
                                   fit_to_width(canvas, self.cfg.preview_width))

                        action = self._wait_key()
                        if action == "quit":
                            break
                        if action == "pause":
                            paused = not paused

        except KeyboardInterrupt:
            log.info("Ctrl+C — тоқтатылуда...")
        finally:
            # Соңғы кадрлар талдаушының кезегінде қалуы мүмкін — видеоның
            # ең соңындағы ақау жоғалып кетпеуі үшін оларды өңдеп аламыз.
            if worker is not None:
                worker.drain(self._handle_result)
                worker.stop()
            self.shutdown()

    def _wait_key(self) -> Optional[str]:
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            return "quit"
        if key == ord("d"):
            self.hud.show_debug = not self.hud.show_debug
        if key == ord(" "):
            return "pause"
        return None

    # ============================================================
    #  Тоқтату
    # ============================================================

    def shutdown(self) -> None:
        self._running = False
        log.info("Аяқталуда — жиналмаған дәлелдер сақталуда...")

        try:
            self.recorder.wait_all()
        except Exception as exc:
            log.warning("Дәлелдерді аяқтау қатесі: %s", exc)

        try:
            self.sender.flush_outbox()
        except Exception:
            pass

        self.gps.stop()
        self.sender.stop()
        self.zones.stop()
        self.source.close()

        if self.cfg.show_preview:
            cv2.destroyAllWindows()

        log.info("=" * 68)
        log.info("ҚОРЫТЫНДЫ")
        log.info("  Оқылған кадр           : %d", self.frames_seen)
        log.info("  Талданған кадр         : %d", self.frames_analysed)
        log.info("  Табылған ақау          : %d", self.detections_total)
        log.info("  Қайталанғаны еленбеді  : %d", self.dedup.suppressed_count)
        log.info("  Жөндеу аймағында       : %d (есепке алынбады)", self.skipped_in_zone)
        if self.detectors.dropped_off_road:
            log.info("  Жолдан тыс еленбеді    : %d (шөп, тротуар, ғимарат)",
                     self.detectors.dropped_off_road)
        if self.detectors.confirmer:
            stats = self.detectors.confirmer.stats()
            log.info("  Уақыт бойынша растау   : %d детекциядан %d расталды",
                     stats["seen"], stats["confirmed"])
        if self.verifier.enabled or self.verifier.checked:
            log.info("  ИИ тексерді            : %d", self.verifier.checked)
            log.info("  ИИ жоққа шығарды       : %d (жалған детекция)", self.ai_rejected)
        if self.skipped_too_small:
            log.info("  Ұсақ — тіркелмеді      : %d (өлшем шегінен төмен)",
                     self.skipped_too_small)
        if self.snapper.checked_count:
            log.info("  Жолға түсірілді        : %d / %d координата",
                     self.snapper.snapped_count, self.snapper.checked_count)
        log.info("  Құрастырылған құжат    : %d", self.documents_built)
        log.info("  Сайтқа жіберілді       : %d", self.sender.sent_count)
        log.info("  Кезекте қалды          : %d", self.sender.pending_count())
        log.info("=" * 68)
