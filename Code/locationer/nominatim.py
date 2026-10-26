"""
Nominatim (OpenStreetMap) geocoder.

Public API: https://nominatim.openstreetmap.org  — 1 req/s rate limit, User-Agent required.
Self-hosted: set NOMINATIM_URL=http://localhost:7070 for unlimited throughput.

Response types considered precise (not city/admin level):
  anything that is NOT amenity=city/town/village, boundary=administrative,
  or place=city/town/village/county/state/country.

ODbL licence — persistent caching allowed, commercial use allowed.
"""

import hashlib
import json
import math
import threading
import time
from typing import Optional

import requests

# Result place_types that are city/admin level (not precise)
_CITY_TYPES = {
    "city", "town", "village", "suburb", "county", "state", "country",
    "municipality", "administrative", "postcode",
}


# 1 Anfrage pro Sekunde gilt fuer den ganzen Prozess, nicht pro Objekt: bei
# parallelen Paketen hat jedes seine eigene Nominatim-Instanz (eigene
# Datenbankverbindung), der Takt muss trotzdem gemeinsam sein.
_TAKT = threading.Lock()
_LETZTER_AUFRUF = [0.0]


def _norm_key(text: str) -> str:
    return hashlib.sha256(f"nominatim|{text.lower().strip()}".encode()).hexdigest()[:20]


class Nominatim:
    def __init__(
        self,
        cache_conn,               # sqlite3 connection with nominatim_cache table
        base_url: str = "https://nominatim.openstreetmap.org",
        user_agent: str = "locationer/4.0",
        debug: bool = False,
    ):
        self.cache_conn = cache_conn
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.debug = debug
        self.call_count = 0
        self._last_call = 0.0
        self._last_error: str = ""
        self._init_cache()

    def _warte_takt(self) -> None:
        """Public API: hoechstens 1 Anfrage pro Sekunde, ueber alle Threads."""
        with _TAKT:
            if self.base_url.startswith("https://nominatim.openstreetmap.org"):
                warten = 1.0 - (time.time() - _LETZTER_AUFRUF[0])
                if warten > 0:
                    time.sleep(warten)
            _LETZTER_AUFRUF[0] = time.time()
            self._last_call = _LETZTER_AUFRUF[0]

    def _init_cache(self):
        self.cache_conn.execute("""
            CREATE TABLE IF NOT EXISTS nominatim_cache (
                key          TEXT PRIMARY KEY,
                lat          REAL,
                lon          REAL,
                display_name TEXT,
                place_type   TEXT,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.cache_conn.execute("""
            CREATE TABLE IF NOT EXISTS nominatim_alle (
                key          TEXT PRIMARY KEY,
                liste        TEXT NOT NULL,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.cache_conn.commit()

    def search(self, query: str) -> Optional[dict]:
        """
        Search for a location. Returns dict with lat/lon/precise/name or None.
        Results are cached permanently.
        """
        key = _norm_key(query)

        # Cache lookup
        row = self.cache_conn.execute(
            "SELECT lat, lon, display_name, place_type FROM nominatim_cache WHERE key=?",
            (key,),
        ).fetchone()
        if row is not None:
            if row[0] is None:
                return None  # cached miss
            return self._make_result(row[0], row[1], row[2], row[3])

        self._warte_takt()
        self.call_count += 1

        try:
            resp = requests.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "limit": 1, "addressdetails": 1},
                headers={"User-Agent": self.user_agent},
                timeout=8,
            )
            data = resp.json()
        except Exception as e:
            self._last_error = str(e)
            self.call_count -= 1
            return None

        # Nominatim antwortet bei Fehlern und Drosselung mit einem OBJEKT
        # ({"error": …}) statt einer Liste.  `not data` ist dann falsch, und das
        # folgende data[0] sucht den Schluessel 0 in einem dict — KeyError.
        # Am 2026-09-06 nach 47 Aufrufen im ZIN-Lauf passiert; der ganze Lauf
        # brach nach 1.371 von 21.684 Zeilen ab.
        if isinstance(data, dict):
            self._last_error = f"Nominatim-Fehlerantwort: {str(data)[:120]}"
            return None
        if not data:
            return None  # don't cache misses — allows retry on next run

        r = data[0]
        lat = float(r["lat"])
        lon = float(r["lon"])
        display_name = r.get("display_name", query)
        place_type = r.get("type", r.get("class", ""))

        self.cache_conn.execute(
            "INSERT OR REPLACE INTO nominatim_cache (key,lat,lon,display_name,place_type) VALUES (?,?,?,?,?)",
            (key, lat, lon, display_name, place_type),
        )
        self.cache_conn.commit()
        return self._make_result(lat, lon, display_name, place_type)

    def search_bounded(self, query: str, lat: float, lon: float, radius_km: float) -> Optional[dict]:
        """Search constrained to a viewbox around (lat, lon) with given radius.

        Uses Nominatim's bounded=1 so results outside the viewbox are suppressed.
        Cache key includes coordinates and radius so bounded and unbounded results
        are stored independently.
        """
        key = _norm_key(f"bounded|{lat:.3f},{lon:.3f},{radius_km:.1f}|{query}")

        row = self.cache_conn.execute(
            "SELECT lat, lon, display_name, place_type FROM nominatim_cache WHERE key=?",
            (key,),
        ).fetchone()
        if row is not None:
            if row[0] is None:
                return None
            return self._make_result(row[0], row[1], row[2], row[3])

        self._warte_takt()
        self.call_count += 1

        delta_lat = radius_km / 111.0
        delta_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
        viewbox = f"{lon - delta_lon},{lat + delta_lat},{lon + delta_lon},{lat - delta_lat}"

        try:
            resp = requests.get(
                f"{self.base_url}/search",
                params={
                    "q": query, "format": "json", "limit": 1, "addressdetails": 1,
                    "viewbox": viewbox, "bounded": 1,
                },
                headers={"User-Agent": self.user_agent},
                timeout=8,
            )
            data = resp.json()
        except Exception as e:
            self._last_error = str(e)
            self.call_count -= 1
            return None

        # Nominatim antwortet bei Fehlern und Drosselung mit einem OBJEKT
        # ({"error": …}) statt einer Liste.  `not data` ist dann falsch, und das
        # folgende data[0] sucht den Schluessel 0 in einem dict — KeyError.
        # Am 2026-09-06 nach 47 Aufrufen im ZIN-Lauf passiert; der ganze Lauf
        # brach nach 1.371 von 21.684 Zeilen ab.
        if isinstance(data, dict):
            self._last_error = f"Nominatim-Fehlerantwort: {str(data)[:120]}"
            return None
        if not data:
            return None  # don't cache misses — allows retry on next run

        r = data[0]
        r_lat = float(r["lat"])
        r_lon = float(r["lon"])
        display_name = r.get("display_name", query)
        place_type = r.get("type", r.get("class", ""))

        self.cache_conn.execute(
            "INSERT OR REPLACE INTO nominatim_cache (key,lat,lon,display_name,place_type) VALUES (?,?,?,?,?)",
            (key, r_lat, r_lon, display_name, place_type),
        )
        self.cache_conn.commit()
        return self._make_result(r_lat, r_lon, display_name, place_type)

    def alle_bounded(self, query: str, lat: float, lon: float, radius_km: float,
                     limit: int = 10) -> "list[dict] | None":
        """Wie search_bounded, aber bis `limit` Treffer — fuer Gattungswoerter.

        „Kirche Almens" hat zwei Kirchen, „Kirche Zürich" Dutzende; der erste
        Treffer ist dann Zufall (geostack._gattung_eindeutig).  Gleiche Anzahl
        Anfragen wie search_bounded: es ersetzt sie fuer solche Objekte.
        → Liste (leer = nichts gefunden) oder None bei einem Fehler (nicht gemerkt).
        """
        key = _norm_key(f"alle2|{limit}|{lat:.3f},{lon:.3f},{radius_km:.1f}|{query}")
        row = self.cache_conn.execute(
            "SELECT liste FROM nominatim_alle WHERE key=?", (key,)).fetchone()
        if row is not None:
            return json.loads(row[0])

        self._warte_takt()
        self.call_count += 1
        delta_lat = radius_km / 111.0
        delta_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
        viewbox = f"{lon - delta_lon},{lat + delta_lat},{lon + delta_lon},{lat - delta_lat}"
        try:
            resp = requests.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "limit": limit, "addressdetails": 1,
                        "viewbox": viewbox, "bounded": 1},
                headers={"User-Agent": self.user_agent},
                timeout=8,
            )
            data = resp.json()
        except Exception as e:
            self._last_error = str(e)
            self.call_count -= 1
            return None
        if isinstance(data, dict):          # Fehlerantwort, siehe search()
            self._last_error = f"Nominatim-Fehlerantwort: {str(data)[:120]}"
            return None
        liste = [{**self._make_result(float(r["lat"]), float(r["lon"]), r.get("display_name", query),
                                      r.get("type", r.get("class", ""))),
                  "klasse": r.get("class", ""),
                  "importance": float(r.get("importance") or 0.0)}
                 for r in (data or [])]
        self.cache_conn.execute("INSERT OR REPLACE INTO nominatim_alle (key, liste) VALUES (?, ?)",
                                (key, json.dumps(liste, ensure_ascii=False)))
        self.cache_conn.commit()
        return liste

    def _make_result(self, lat, lon, display_name, place_type) -> dict:
        is_precise = place_type.lower() not in _CITY_TYPES
        return {
            "lat": lat,
            "lon": lon,
            "precise": is_precise,
            "name": display_name.split(",")[0].strip() if display_name else "",
            "place_type": place_type,
        }

