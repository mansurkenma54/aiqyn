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
import time
from dataclasses import replace
from typing import Optional

import cv2
import numpy as np

from dataclasses import replace

from .aiverify import AIVerifier
from .config import Config
from .dedup import Deduplicator
from .detectors import Detection, DetectorBank, LaneDetector, LaneResult
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
        self._fps = 0.0
        self._last_frame_ts = 0.0
        self._portal_online = False
        self._portal_checked_at = 0.0
        self._running = False

        # Графикалық қолданба үшін ілмектер (консольде қолданылмайды):
        #   frame_callback(canvas, stats) — әр кадрды сыртқа береді
        #   event_callback(document)      — құжат дайын болғанда шақырылады
        self.frame_callback = None
        self.event_callback = None

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
    #  Оқиға -> құжат  (дәлел дайын болғанда шақырылады)
    # ============================================================

    def _on_evidence_ready(self, files: EvidenceFiles, meta: dict) -> None:
        """Дәлел файлдары жазылып болғанда — толық құжат құрастырып, жіберу.

        Бұл БӨЛЕК АҒЫНДА орындалады, сондықтан мекенжайды анықтау
        (интернет сұрауы, ~1 сек) негізгі циклді тежемейді.
        """
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
                    return

            # Мекенжайды алдымен анықтаймыз — ИИ сарапшысына орынды да
            # беру керек, сонда талдауы нақтырақ болады
            address = self.geocoder.reverse(fix.lat, fix.lon)

            # --- ЕКІНШІ САТЫ: ИИ көру моделі талдап, қорытынды жазады ---
            # Мұнда шақырылатын себебі: дәлел фотосы дайын, әрі бұл бөлек
            # ағын — негізгі цикл тежелмейді.
            from datetime import datetime as _dt
            verdict = self.verifier.verify(
                files.photo,
                meta["class_key"],
                meta["confidence"],
                address=address.get("address_text", ""),
                when=_dt.fromtimestamp(meta["detected_at"]).strftime("%d.%m.%Y %H:%M"),
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
                return

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

            self.sender.send(document, files.photo, files.video)

            if self.event_callback is not None:
                try:
                    self.event_callback(document, files)
                except Exception as exc:
                    log.debug("Оқиға callback қатесі: %s", exc)

        except Exception as exc:
            log.exception("Құжат құрастыру сәтсіз (%s): %s", files.event_id, exc)

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

            if not self.dedup.check(detection.class_key, fix.lat, fix.lon):
                continue

            event_id = new_event_id()
            self.dedup.remember(detection.class_key, fix.lat, fix.lon, event_id)

            # Bbox дәл осы stream кадрында табылды. Кейін алынатын /shot.jpg
            # басқа уақыт/FOV болуы мүмкін, сондықтан ресми белгіленген фотоға
            # детекциямен пиксель-пиксель сәйкес кадрды ғана береміз.
            best_image = image.copy()

            self.recorder.start_event(
                event_id=event_id,
                best_image=best_image,
                confidence=detection.confidence,
                meta={
                    "class_key": detection.class_key,
                    "confidence": detection.confidence,
                    "area_frac": detection.area_frac(image.shape),
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

            log.info(
                "АҚАУ ТАБЫЛДЫ: %s (%.0f%%) @ %.5f, %.5f [%s] -> %s",
                detection.class_key, detection.confidence * 100,
                fix.lat, fix.lon, fix.source, event_id,
            )

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
        if now - self._portal_checked_at < 15.0:
            return
        self._portal_checked_at = now
        self._portal_online = self.sender.ping()

    def _stats(self) -> HudStats:
        return HudStats(
            fps=self._fps,
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
    ) -> list[Detection]:
        """YOLO аралық кадрларда bbox-ты sparse optical flow арқылы жылжыту.

        Бұрын әр N-ші кадрдағы bbox келесі N-1 қозғалған кадрға сол күйі
        салынып, метка ақаудан сырғып кететін. Feature жеткіліксіз болса ескі
        bbox-ты жорамалмен ұстамаймыз — оны бірден жасыру қауіпсіз.
        """
        if (
            not detections
            or previous_gray is None
            or current_gray is None
            or previous_gray.shape != current_gray.shape
        ):
            return []

        height, width = current_gray.shape[:2]
        propagated: list[Detection] = []

        for det in detections:
            x1, y1, x2, y2 = det.bbox
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
                    bbox=(nx1, ny1, nx2, ny2),
                    extra={**det.extra, "hud_tracked": True},
                )
            )

        return propagated

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
        previous_gray: Optional[np.ndarray] = None
        paused = False
        self._running = True
        started_at = time.time()

        try:
            while self._running:
                if self.cfg.max_duration_sec > 0 and \
                        time.time() - started_at >= self.cfg.max_duration_sec:
                    log.info("Белгіленген уақыт (%.0f сек) аяқталды.", self.cfg.max_duration_sec)
                    break

                if paused:
                    if self._wait_key() == "quit":
                        break
                    continue

                frame = self.source.read()
                if frame is None:
                    log.info("Видео ағыны аяқталды.")
                    break

                # Қиюды ЕҢ БАСЫНДА жасаймыз: сол арқылы детекция да, дәлел
                # видеосы да, экрандағы көрініс те бір кадрмен жұмыс істейді
                frame.image = self.cfg.crop(frame.image)

                self.frames_seen += 1
                now = time.time()
                self._update_fps(now)
                self._check_portal(now)

                self.recorder.feed(frame.image)

                # Кадрды сыртқа беретін болсақ (графикалық қолданба), превью
                # логикасының бәрі керек — терезе ашылмаса да
                wants_canvas = self.cfg.show_preview or self.frame_callback is not None

                current_gray = (
                    cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
                    if wants_canvas else None
                )

                # YOLO-ны әр N-ші кадрда жүргіземіз. Аралық кадрда bbox optical
                # flow-мен қозғалады, ал жеңіл lane detector әр кадрда жаңарады.
                analyse_now = self.frames_seen % max(1, int(self.cfg.frame_stride)) == 0
                if analyse_now:
                    self.frames_analysed += 1
                    detections, lane, context = self.detectors.process(frame.image)
                    display_detections, last_lane = detections, lane
                    # Экранда БАРЛЫҚ детекция көрінеді, ал құжат тек
                    # бірнеше кадрда РАСТАЛҒАНЫ бойынша құрылады
                    self._handle_detections(
                        context.get("confirmed", detections), frame.image, frame.ts
                    )
                elif wants_canvas:
                    display_detections = self._propagate_detections(
                        display_detections, previous_gray, current_gray
                    )
                    lane_detector = self.detectors.lane_detector
                    if lane_detector is not None:
                        try:
                            last_lane = lane_detector.detect(frame.image)
                        except Exception as exc:
                            log.debug("Аралық кадрдағы lane қатесі: %s", exc)

                previous_gray = current_gray

                if wants_canvas:
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

                    # Графикалық қолданбаға кадрды береміз
                    if self.frame_callback is not None:
                        try:
                            self.frame_callback(canvas, self._stats())
                        except Exception as exc:
                            log.debug("Кадр callback қатесі: %s", exc)

                    if self.cfg.show_preview:
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
        if self.snapper.checked_count:
            log.info("  Жолға түсірілді        : %d / %d координата",
                     self.snapper.snapped_count, self.snapper.checked_count)
        log.info("  Құрастырылған құжат    : %d", self.documents_built)
        log.info("  Сайтқа жіберілді       : %d", self.sender.sent_count)
        log.info("  Кезекте қалды          : %d", self.sender.pending_count())
        log.info("=" * 68)
