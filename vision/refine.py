"""Рамканы ақаудың НАҚТЫ шекарасына дейін тарылту.

Мәселе: YOLO жол жабынының бүлінген АЙМАҒЫН қоршайды — бүкіл жамауды,
кейде жарты жолақты. Дәлел ретінде ол нашар: жюри де, жөндеуші де
«ақау қайда, әлде бүкіл жол ма?» деп сұрайды. Рамка ақаудың өзін
көрсетуі керек.

Шешім — детекторды өзгертпей, оның нәтижесін НАҚТЫЛАУ:

    1. рамканың ішіндегі жол бетінің қалыпты жарықтығы есептеледі
       (медиана — бір қараңғы дақ бүкіл есепті бұзбауы үшін);
    2. одан АЙТАРЛЫҚТАЙ қараңғы немесе құрылымы бұзылған нүктелер
       маска болып бөлінеді (шұңқыр — ойық, сондықтан көлеңкелі;
       жарық — сызық, сондықтан градиенті күшті);
    3. маска морфологиямен тазаланып, ЕҢ ІРІ байланысты аймақтың
       шекарасы алынады;
    4. сол шекара бастапқы рамканың ішінде қалады.

Ештеңе табылмаса — бастапқы рамка сол күйінде қалады. Жалған
дәлдік жасамаймыз.
"""

from __future__ import annotations

import cv2
import numpy as np

# Тарылтудың шегі: бастапқы ауданның осыдан аз бөлігі қалса, нәтиже
# күмәнді. 0.04 тым төмен болатын — ірі шұңқыр кішкентай шаршыға
# айналып, дәлел мәнін жоғалтатын.
MIN_AREA_RATIO = 0.20
# Тым үлкен қалса, тарылтудың мәні жоқ
MAX_AREA_RATIO = 0.92
PAD = 3                                   # шекараға қосылатын кішкене ауа


def _mask_dark(patch: np.ndarray) -> np.ndarray:
    """Айналасынан қараңғы жерлер — шұңқыр мен ойықтың белгісі."""
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 45, 45)
    base = float(np.median(gray))
    spread = float(np.percentile(gray, 75) - np.percentile(gray, 25)) or 8.0
    threshold = max(8.0, base - 0.85 * spread)
    return (gray < threshold).astype(np.uint8) * 255


def _mask_texture(patch: np.ndarray) -> np.ndarray:
    """Құрылымы бұзылған жерлер — жарық, үгітілу, қиыршық тас."""
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    # Лапласиан — жиектің күштілігі. Тегіс асфальтта ол дерлік нөл.
    energy = cv2.convertScaleAbs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))
    energy = cv2.GaussianBlur(energy, (0, 0), 3)
    level = float(np.percentile(energy, 82))
    return (energy > max(6.0, level)).astype(np.uint8) * 255


def refine_box(image: np.ndarray, bbox: tuple[int, int, int, int],
               class_key: str = "") -> tuple[int, int, int, int]:
    """Бір рамканы ақаудың нақты шекарасына дейін тарылту."""
    x1, y1, x2, y2 = (int(v) for v in bbox)
    height, width = image.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 - x1 < 12 or y2 - y1 < 12:
        return (x1, y1, x2, y2)

    patch = image[y1:y2, x1:x2]
    dark = _mask_dark(patch)
    texture = _mask_texture(patch)

    # Шұңқыр — бірінші кезекте ойық; жарық — құрылым бұзылуы.
    mask = cv2.bitwise_or(dark, texture) if "pothole" in class_key else texture
    if not mask.any():
        return (x1, y1, x2, y2)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return (x1, y1, x2, y2)

    # Ең ірі аймақ (0 — фон)
    index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    bx, by, bw, bh, area = stats[index]

    patch_area = float(patch.shape[0] * patch.shape[1])
    ratio = (bw * bh) / patch_area
    if not (MIN_AREA_RATIO <= ratio <= MAX_AREA_RATIO):
        return (x1, y1, x2, y2)

    nx1 = x1 + max(0, bx - PAD)
    ny1 = y1 + max(0, by - PAD)
    nx2 = x1 + min(patch.shape[1], bx + bw + PAD)
    ny2 = y1 + min(patch.shape[0], by + bh + PAD)

    if nx2 - nx1 < 10 or ny2 - ny1 < 10:
        return (x1, y1, x2, y2)
    return (nx1, ny1, nx2, ny2)


def grow_box(image: np.ndarray, bbox: tuple[int, int, int, int],
             max_scale: float = 1.9, max_growth: float = 2.6) -> tuple[int, int, int, int]:
    """Рамканы ойықтың ШЫН шекарасына дейін кеңейту.

    Детектор шұңқырдың бір бөлігін ғана көрсетуі мүмкін — әсіресе
    ойық ірі әрі көрерменге жақын болғанда. Ондай рамка дәлел емес:
    оператор да, жөндеуші де шұңқырдың нақты көлемін көре алмайды.

    Тәсіл: рамканың айналасынан кеңірек аймақ алынады, ондағы
    қараңғы (ойық) нүктелер маска болып бөлінеді де, рамка
    ортасындағы нүктемен БАЙЛАНЫСТЫ дақтың шекарасы алынады.
    Ойық — біртұтас нәрсе, сондықтан байланысты дақ дәл соның өзі.
    """
    x1, y1, x2, y2 = (int(v) for v in bbox)
    height, width = image.shape[:2]
    box_w, box_h = x2 - x1, y2 - y1
    if box_w < 6 or box_h < 6:
        return (x1, y1, x2, y2)

    # Іздеу аймағы — рамкадан кеңірек, бірақ кадрдан аспайды
    pad_x = int(box_w * (max_scale - 1) / 2)
    pad_y = int(box_h * (max_scale - 1) / 2)
    ax1, ay1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
    ax2, ay2 = min(width, x2 + pad_x), min(height, y2 + pad_y)
    area = image[ay1:ay2, ax1:ax2]
    if area.size == 0:
        return (x1, y1, x2, y2)

    gray = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 50, 50)
    base = float(np.median(gray))
    spread = float(np.percentile(gray, 75) - np.percentile(gray, 25)) or 8.0
    mask = (gray < max(6.0, base - 0.75 * spread)).astype(np.uint8) * 255

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return (x1, y1, x2, y2)

    # Рамканың ортасындағы нүкте қай дақта жатыр
    cx = min(labels.shape[1] - 1, max(0, (x1 + x2) // 2 - ax1))
    cy = min(labels.shape[0] - 1, max(0, (y1 + y2) // 2 - ay1))
    index = int(labels[cy, cx])
    if index == 0:
        # Дәл ортасы қараңғы емес — рамкамен ең көп қабаттасқанын аламыз
        best, best_overlap = 0, 0
        for i in range(1, count):
            bx, by, bw, bh, _ = stats[i]
            ox = max(0, min(bx + bw, x2 - ax1) - max(bx, x1 - ax1))
            oy = max(0, min(by + bh, y2 - ay1) - max(by, y1 - ay1))
            if ox * oy > best_overlap:
                best, best_overlap = i, ox * oy
        index = best
    if index == 0:
        return (x1, y1, x2, y2)

    bx, by, bw, bh, _ = stats[index]
    # Тым үлкен дақ — көлеңке немесе бүкіл тозған жолақ болуы мүмкін.
    # Ондай жағдайда өсіру ақауды емес, бүкіл кадрды қоршап кетеді.
    if bw * bh > 0.55 * (ax2 - ax1) * (ay2 - ay1):
        return (x1, y1, x2, y2)

    nx1, ny1 = ax1 + max(0, bx - PAD), ay1 + max(0, by - PAD)
    nx2, ny2 = min(width, ax1 + bx + bw + PAD), min(height, ay1 + by + bh + PAD)

    # Кеңейту ғана: бастапқы рамканы толық қамтуы керек
    gx1, gy1 = min(nx1, x1), min(ny1, y1)
    gx2, gy2 = max(nx2, x2), max(ny2, y2)

    # Өсу шектеулі: рамка бастапқысынан бірнеше есе үлкейсе, ол
    # ақаудың шекарасы емес, тұтас тозған беттің шекарасы
    if (gx2 - gx1) * (gy2 - gy1) > max_growth * box_w * box_h:
        return (x1, y1, x2, y2)
    # Кадрдың жартысынан асатын рамка дәлел бола алмайды
    if (gx2 - gx1) * (gy2 - gy1) > 0.30 * width * height:
        return (x1, y1, x2, y2)
    return (gx1, gy1, gx2, gy2)


# Физика бойынша жұмыс істейтін детектор рамканы ӨЗІ дәл береді:
# ол ойықтың шекарасын тауып тұр. Оны қайта тарылту — дұрыс нәтижені
# бұзу.
SKIP_DETECTORS = ("pothole_cv", "flood", "streetlight")


def refine_all(image: np.ndarray, detections: list) -> list:
    """Барлық рамканы нақтылау. Detection нысандары орнында өзгереді."""
    for detection in detections:
        try:
            if "pothole" in detection.class_key:
                # Шұңқыр — біртұтас ойық: рамка оның ТОЛЫҚ шекарасын
                # қамтуы керек, бір бұрышын емес
                detection.bbox = grow_box(image, detection.bbox)
            elif getattr(detection, "detector", "") not in SKIP_DETECTORS:
                # Жарық пен үгітілу — жайылған аймақ: рамканы керісінше
                # тарылтып, нақты бүлінген жерге түсіреміз
                detection.bbox = refine_box(image, detection.bbox, detection.class_key)
        except Exception:                          # noqa: BLE001
            pass                                   # нақтылау сәтсіз болса — бастапқысы қалады
    return detections
