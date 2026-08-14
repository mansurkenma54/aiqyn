"""Фотосуреттің EXIF деректерінен GPS пен уақытты оқу.

Телефонмен түсірілген суретте координата көбіне суреттің өз ішінде
сақталады. Сондықтан бұрын түсірілген фотоларды талдағанда координатаны
қолмен енгізудің қажеті жоқ — оны суреттен аламыз.

Ескерту: WhatsApp/Telegram арқылы жіберілген суретте EXIF әдетте
ӨШІРІЛГЕН болады (мессенджер метадеректі тазалайды). Ондай жағдайда
--lat / --lon аргументтерімен қолмен көрсету керек.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ExifTags

log = logging.getLogger(__name__)

GPS_IFD_TAG = 34853          # GPSInfo
DATETIME_ORIGINAL = 36867


def _to_degrees(value) -> Optional[float]:
    """EXIF-тегі (градус, минут, секунд) үштігін ондық градусқа айналдыру."""
    try:
        degrees, minutes, seconds = (float(v) for v in value)
        return degrees + minutes / 60.0 + seconds / 3600.0
    except (TypeError, ValueError):
        return None


def gps_from_image(path: str | Path) -> Optional[dict]:
    """Суреттен координата мен түсірілген уақытты оқу.

    Қайтарады: {"lat", "lon", "timestamp", "altitude"} немесе None.
    """
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            if not exif:
                return None

            result: dict = {}

            # --- уақыт ---
            raw_time = exif.get(DATETIME_ORIGINAL)
            if raw_time:
                try:
                    moment = datetime.strptime(str(raw_time), "%Y:%m:%d %H:%M:%S")
                    result["timestamp"] = moment.timestamp()
                except ValueError:
                    pass

            # --- координата ---
            try:
                gps_info = exif.get_ifd(GPS_IFD_TAG)
            except Exception:
                gps_info = None

            if not gps_info:
                return result or None

            tags = {ExifTags.GPSTAGS.get(key, key): value for key, value in gps_info.items()}

            latitude = _to_degrees(tags.get("GPSLatitude"))
            longitude = _to_degrees(tags.get("GPSLongitude"))
            if latitude is None or longitude is None:
                return result or None

            if str(tags.get("GPSLatitudeRef", "N")).upper().startswith("S"):
                latitude = -latitude
            if str(tags.get("GPSLongitudeRef", "E")).upper().startswith("W"):
                longitude = -longitude

            result["lat"] = latitude
            result["lon"] = longitude

            altitude = tags.get("GPSAltitude")
            if altitude is not None:
                try:
                    result["altitude"] = float(altitude)
                except (TypeError, ValueError):
                    pass

            return result

    except Exception as exc:
        log.debug("EXIF оқылмады (%s): %s", path, exc)
        return None
