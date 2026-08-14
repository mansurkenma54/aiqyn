"""Координатаны толық мәтіндік мекенжайға айналдыру (reverse geocoding).

Nominatim (OpenStreetMap) — тегін, кілт қажет емес.
Шектеу: секундына 1 сұраудан аспау керек (қызметтің ережесі), сондықтан
жылдамдық шектегіш пен кэш қойылған.

Интернет жоқ болса немесе сұрау сәтсіз болса — программа тоқтамайды,
құжатқа координата бойынша сипаттама жазылады.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests

from .config import Config

log = logging.getLogger(__name__)

USER_AGENT = "AIQYN-Vision/1.0 (SmartCity Hackathon 2026, Shymkent)"


class Geocoder:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._cache: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._failures = 0

    @staticmethod
    def _cache_key(lat: float, lon: float) -> tuple:
        # ~11 метрлік торға дөңгелектейміз — жақын нүктелер бір сұрауды бөліседі
        return (round(lat, 4), round(lon, 4))

    def _rate_limit(self) -> None:
        with self._lock:
            wait = 1.05 - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.time()

    # ---------- негізгі ----------

    def reverse(self, lat: float, lon: float) -> dict:
        """Қайтарады: {address_text, road, house_number, city, raw, ok}

        Дереккөз реті:
            1) 2ГИС — кілт болса (Шымкент мекенжайларын әлдеқайда жақсы біледі)
            2) Nominatim (OpenStreetMap) — тегін, кілт қажет емес
            3) Координата бойынша мәтін — интернет жоқ болса
        """
        key = self._cache_key(lat, lon)
        if key in self._cache:
            return self._cache[key]

        result = self._fallback(lat, lon)

        # --- 1) 2ГИС ---
        if self.cfg.geocode_enabled and self.cfg.gis2_key and \
                self.cfg.geocode_provider in ("auto", "2gis"):
            from_2gis = self._reverse_2gis(lat, lon)
            if from_2gis is not None:
                self._cache[key] = from_2gis
                return from_2gis

        # --- 2) Nominatim ---
        if self.cfg.geocode_provider == "2gis":
            self._cache[key] = result
            return result

        if self.cfg.geocode_enabled and self._failures < 5:
            try:
                self._rate_limit()
                params = {
                    "lat": f"{lat:.6f}",
                    "lon": f"{lon:.6f}",
                    "format": "jsonv2",
                    "zoom": "18",
                    "addressdetails": "1",
                    "accept-language": "kk,ru,en",
                }
                if self.cfg.nominatim_email:
                    params["email"] = self.cfg.nominatim_email

                response = self._session.get(
                    self.cfg.nominatim_url, params=params, timeout=self.cfg.geocode_timeout
                )
                response.raise_for_status()
                result = self._parse(response.json(), lat, lon)
                self._failures = 0
            except Exception as exc:
                self._failures += 1
                if self._failures <= 2:
                    log.warning("Мекенжай анықталмады (%.5f, %.5f): %s", lat, lon, exc)
                if self._failures == 5:
                    log.warning("Nominatim бірнеше рет жауап бермеді — өшіріледі.")

        self._cache[key] = result
        return result

    def _reverse_2gis(self, lat: float, lon: float) -> Optional[dict]:
        """2ГИС Catalog API арқылы мекенжай (Қазақстан бойынша дәлдеу).

        Кілт: https://dev.2gis.com — тегін тариф бар.
        """
        try:
            response = self._session.get(
                "https://catalog.api.2gis.com/3.0/items/geocode",
                params={
                    "lat": f"{lat:.6f}",
                    "lon": f"{lon:.6f}",
                    "fields": "items.address,items.adm_div,items.full_address_name",
                    "radius": "250",
                    "locale": "kk_KZ",
                    "key": self.cfg.gis2_key,
                },
                timeout=self.cfg.geocode_timeout,
            )
            response.raise_for_status()
            payload = response.json()

            items = (payload.get("result") or {}).get("items") or []
            if not items:
                return None

            # 2ГИС нәтижені араластырып қайтарады: ғимарат/көше де,
            # әкімшілік бірлік те (аудан, қала) бір тізімде. Бізге НАҚТЫ
            # мекенжай керек, "Аль-Фараби ауданы" емес — сондықтан тек
            # ғимарат/көше түріндегі жазбаны аламыз.
            precise_types = ("building", "street", "branch", "road")
            item = None
            for candidate in items:
                if candidate.get("type") in precise_types and (
                    candidate.get("full_address_name") or candidate.get("address_name")
                ):
                    item = candidate
                    break

            # Нақты мекенжай табылмаса — None қайтарамыз, сонда Nominatim
            # сыналады (ол кейде көше атауын біледі)
            if item is None:
                log.debug("2ГИС нақты мекенжай таппады (%.5f, %.5f)", lat, lon)
                return None

            text = (
                item.get("full_address_name")
                or item.get("address_name")
                or item.get("name")
                or ""
            )
            if not text:
                return None

            city = ""
            for division in item.get("adm_div", []) or []:
                if division.get("type") == "city":
                    city = division.get("name", "")
                    break

            if city and city.lower() not in text.lower():
                text = f"{city}, {text}"

            return {
                "address_text": text,
                "road": item.get("address_name", ""),
                "house_number": (item.get("address") or {}).get("building_name", ""),
                "neighbourhood": "",
                "city": city or "Шымкент",
                "raw": item.get("full_name", ""),
                "ok": True,
                "provider": "2gis",
            }

        except (requests.RequestException, ValueError, KeyError) as exc:
            log.debug("2ГИС мекенжайы алынбады: %s", exc)
            return None

    def _parse(self, payload: dict, lat: float, lon: float) -> dict:
        address = payload.get("address", {}) or {}

        road = (
            address.get("road")
            or address.get("pedestrian")
            or address.get("residential")
            or address.get("highway")
            or ""
        )
        house = address.get("house_number") or ""
        neighbourhood = (
            address.get("neighbourhood")
            or address.get("suburb")
            or address.get("city_district")
            or address.get("quarter")
            or ""
        )
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("state")
            or "Шымкент"
        )

        parts: list[str] = [city]
        if neighbourhood and neighbourhood != city:
            parts.append(neighbourhood)
        if road:
            parts.append(f"{road} көшесі" if "көше" not in road.lower() else road)
        if house:
            parts.append(f"№{house}")

        if len(parts) <= 1:
            display = payload.get("display_name") or ""
            text = display or self._fallback(lat, lon)["address_text"]
        else:
            text = ", ".join(parts)

        return {
            "address_text": text,
            "road": road,
            "house_number": house,
            "neighbourhood": neighbourhood,
            "city": city,
            "raw": payload.get("display_name", ""),
            "ok": True,
        }

    @staticmethod
    def _fallback(lat: float, lon: float) -> dict:
        return {
            "address_text": f"Шымкент қаласы, координата бойынша: {lat:.5f}, {lon:.5f}",
            "road": "",
            "house_number": "",
            "neighbourhood": "",
            "city": "Шымкент",
            "raw": "",
            "ok": False,
        }
