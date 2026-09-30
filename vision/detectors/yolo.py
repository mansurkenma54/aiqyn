"""YOLOv8 — жол жабынының ақауын анықтау (RDD2022).

Салмақтар: github.com/oracl4/RoadDamageDetection — RDD2022 датасетінде
(47 420 сурет, 55 000+ белгіленген ақау) үйретілген.

Салмақты жүктеу:
    python scripts/fetch_weights.py

Егер RDD салмағы табылмаса, программа тоқтап қалмайды: жалпы COCO
моделіне ауысып, тек "жол бөгеті" (obstruction) режимінде жұмыс істейді
және журналға анық ескерту жазады.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

from ..config import Config
from .base import Detection, Detector

log = logging.getLogger(__name__)


# Модель класының атауы -> AIQYN ішкі кілті.
# Әртүрлі репозиторийде атау әрқалай жазылған, сондықтан кілт сөз бойынша іздейміз.
NAME_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("pothole", "potholes", "d40", "yama"), "pothole"),
    (("alligator", "aligator", "d20", "block crack", "fatigue"), "crack_alligator"),
    (("longitudinal", "d00", "wheel mark"), "crack_longitudinal"),
    (("transverse", "d10", "lateral"), "crack_transverse"),
    (("manhole", "utility hole", "d43", "cover"), "manhole_open"),
    (("flood", "water", "puddle"), "flood"),
    (("debris", "obstruction", "obstacle"), "obstruction"),
]

# COCO-моделіне түсіп қалған жағдайда: жолдағы бөгет болуы мүмкін нысандар
COCO_OBSTRUCTION = {
    "car", "truck", "bus", "motorcycle", "bicycle",
    "bench", "chair", "suitcase", "backpack", "potted plant",
    "cow", "horse", "sheep", "dog", "elephant",
}


def map_class_name(raw: str) -> Optional[str]:
    """Модель класының атауын AIQYN санатына айналдыру."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    for patterns, key in NAME_PATTERNS:
        for pattern in patterns:
            if pattern in text:
                return key
    return None


class RoadDamageDetector(Detector):
    name = "yolo"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.model = None
        self.class_map: dict[int, str] = {}
        self.is_coco_fallback = False
        self.device = cfg.device
        self.device_label = "процессор"
        self._load()

    # ---------- құрылғыны таңдау ----------

    @staticmethod
    def _openvino_gpu_available() -> bool:
        """Intel-дің кірістірілген графикасы OpenVINO арқылы қолжетімді ме."""
        try:
            import openvino
            return "GPU" in openvino.Core().available_devices
        except Exception:
            return False

    def _openvino_model_dir(self) -> Path:
        weights = self.cfg.weights_path
        return weights.with_name(f"{weights.stem}_openvino_model")

    def _prepare_openvino(self) -> Optional[str]:
        """OpenVINO нұсқасын дайындау. Дайын болса — оның жолын қайтарады.

        Модель бір рет қана түрлендіріледі (~2 сек) және дискіде қалады,
        келесі іске қосуларда бірден жүктеледі.
        """
        target = self._openvino_model_dir()
        if target.exists() and any(target.glob("*.xml")):
            return str(target)

        try:
            from ultralytics import YOLO
            log.info("Intel графикасы табылды — модель бір рет түрлендірілуде…")
            YOLO(str(self.cfg.weights_path)).export(
                format="openvino", imgsz=self.cfg.imgsz, half=True, dynamic=False
            )
        except Exception as exc:
            log.warning("OpenVINO түрлендіруі сәтсіз (процессормен жалғастырамыз): %s", exc)
            return None

        return str(target) if target.exists() else None

    def _resolve_device(self) -> tuple[Optional[str], str, str]:
        """(модель жолы, device, адам оқитын атау) қайтарады."""
        requested = (self.cfg.device or "auto").strip().lower()

        if requested not in ("auto", "intel:gpu", "gpu"):
            return None, self.cfg.device, "процессор" if requested == "cpu" else requested

        if not self.cfg.weights_path.exists():
            return None, "cpu", "процессор"

        if not self._openvino_gpu_available():
            if requested != "auto":
                log.warning("Intel графикасы табылмады — процессорға ауысамыз.")
            return None, "cpu", "процессор"

        path = self._prepare_openvino()
        if path is None:
            return None, "cpu", "процессор"
        return path, "intel:gpu", "Intel графикасы (3,6 есе жылдам)"

    # ---------- жүктеу ----------

    def _resolve_weights(self) -> tuple[str, bool]:
        """Салмақ файлын табу. (жол, coco_fallback_па) қайтарады."""
        path: Path = self.cfg.weights_path
        if path.exists():
            return str(path), False

        log.warning("=" * 68)
        log.warning("RDD салмағы табылмады: %s", path)
        log.warning("Жол ақауын анықтау ӨШІРУЛІ болады — тек бөгет (obstruction).")
        log.warning("Түзету үшін:  python scripts/fetch_weights.py")
        log.warning("=" * 68)
        return "yolov8n.pt", True

    def _load(self) -> None:
        from ultralytics import YOLO  # ауыр импорт — тек керек кезде

        weights, is_fallback = self._resolve_weights()
        self.is_coco_fallback = is_fallback

        # Ең жылдам қолжетімді құрылғыны табамыз (Intel графикасы / процессор)
        if not is_fallback:
            ov_path, device, label = self._resolve_device()
            self.device, self.device_label = device, label
            if ov_path:
                weights = ov_path
        else:
            self.device, self.device_label = "cpu", "процессор"

        log.info("YOLO жүктелуде: %s", weights)
        log.info("Есептеу құрылғысы: %s", self.device_label)
        try:
            self.model = YOLO(weights, task="detect")
        except Exception as exc:
            if self.device == "intel:gpu":
                log.warning("OpenVINO жүктелмеді (%s) — процессорға ауысамыз.", exc)
                self.device, self.device_label = "cpu", "процессор"
                self.model = YOLO(str(self.cfg.weights_path), task="detect")
            else:
                raise

        names = self.model.names or {}
        if isinstance(names, (list, tuple)):
            names = dict(enumerate(names))

        unmapped: list[str] = []
        for idx, raw in names.items():
            if is_fallback:
                if str(raw).lower() in COCO_OBSTRUCTION:
                    self.class_map[int(idx)] = "obstruction"
                continue
            key = map_class_name(str(raw))
            if key:
                self.class_map[int(idx)] = key
            else:
                unmapped.append(str(raw))

        log.info(
            "Модель кластары: %s",
            ", ".join(f"{names[i]} -> {k}" for i, k in sorted(self.class_map.items())) or "жоқ",
        )
        if unmapped:
            log.warning("Сәйкестендірілмеген кластар (еленбейді): %s", ", ".join(unmapped))

    def warmup(self) -> None:
        if self.model is None:
            return
        blank = np.zeros((self.cfg.imgsz, self.cfg.imgsz, 3), dtype=np.uint8)
        try:
            self.model.predict(
                blank, imgsz=self.cfg.imgsz, device=self.device, verbose=False
            )
            log.info("YOLO дайын (warmup орындалды).")
        except Exception as exc:
            log.warning("Warmup сәтсіз: %s", exc)

    # ---------- детекция ----------

    def _predict(
        self, image: np.ndarray, frame_area: float,
        offset_x: int = 0, offset_y: int = 0, tile: str = "",
    ) -> list[Detection]:
        """Бір суретті модельге беріп, нәтижені Detection тізіміне айналдыру.

        offset_x/offset_y — сурет үлкен кадрдың кесіндісі болса, bbox-ты
        ТҮПНҰСҚА кадрдың координатасына қайтару үшін.
        """
        try:
            results = self.model.predict(
                image,
                imgsz=self.cfg.imgsz,
                conf=max(0.15, self.cfg.conf_threshold - 0.15),  # сүзгіні кейін өзіміз саламыз
                device=self.device,
                verbose=False,
            )
        except Exception as exc:
            log.error("YOLO детекциясы сәтсіз: %s", exc)
            return []

        detections: list[Detection] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                cls_id = int(box.cls[0])
                key = self.class_map.get(cls_id)
                if key is None:
                    continue

                confidence = float(box.conf[0])
                if confidence < self.cfg.conf_threshold:
                    continue

                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                x1, x2 = x1 + offset_x, x2 + offset_x
                y1, y2 = y1 + offset_y, y2 + offset_y
                if (x2 - x1) * (y2 - y1) / frame_area < self.cfg.min_box_area_frac:
                    continue

                extra = {"model_class": cls_id}
                if tile:
                    extra["tile"] = tile

                detections.append(
                    Detection(
                        class_key=key,
                        confidence=confidence,
                        bbox=(x1, y1, x2, y2),
                        detector="yolo",
                        extra=extra,
                    )
                )

        return detections

    def detect(self, image: np.ndarray, context: Optional[dict] = None) -> list[Detection]:
        if self.model is None:
            return []

        frame_area = float(image.shape[0] * image.shape[1])
        detections = self._predict(image, frame_area)

        if not self.cfg.tile_detect:
            return detections

        # --- ДӘЛДЕУ РЕЖИМІ: кадрдың жол бөлігін бөлек талдау ---
        #
        # Модель RDD2022-де ~600 px суреттерге үйретілген. 1920 px кадр
        # 640-қа сығылғанда алыстағы немесе жіңішке ақау бірнеше пиксельге
        # айналып, мүлдем жоғалады. Кадрдың төменгі (жол көрінетін) бөлігін
        # ЕКІ кесіндіге бөліп, әрқайсысын өз ажыратымдылығында берсек, сол
        # ақаулар қайта табылады.
        #
        # Үш видеода өлшенді: 3 детекция -> 11 детекция (қосымшалары
        # көзбен тексерілді — шын жарықтар). Бағасы: талдау ~3 есе ұзақ,
        # бірақ талдау бөлек ағында жүреді, сондықтан видео тежелмейді.
        height, width = image.shape[:2]
        top = int(height * max(0.0, min(0.8, self.cfg.tile_top_frac)))
        tile_w = int(width * 0.58)          # кесінділер ортада қабаттасады
        if height - top < 80 or tile_w < 80:
            return detections

        for index in range(2):
            left = int(index * width * 0.42)
            right = min(width, left + tile_w)
            tile = image[top:height, left:right]
            if tile.size == 0:
                continue
            detections.extend(
                self._predict(tile, frame_area, left, top, tile=f"t{index}")
            )

        return detections
