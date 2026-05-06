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
import time
from typing import Optional

import requests

# Result place_types that are city/admin level (not precise)
_CITY_TYPES = {
    "city", "town", "village", "suburb", "county", "state", "country",
    "municipality", "administrative", "postcode",
}


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

        # Rate-limit: 1 req/s for public API
        elapsed = time.time() - self._last_call
        if elapsed < 1.0 and self.base_url.startswith("https://nominatim.openstreetmap.org"):
            time.sleep(1.0 - elapsed)

        self.call_count += 1
        self._last_call = time.time()

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

    def _make_result(self, lat, lon, display_name, place_type) -> dict:
        is_precise = place_type.lower() not in _CITY_TYPES
        return {
            "lat": lat,
            "lon": lon,
            "precise": is_precise,
            "name": display_name.split(",")[0].strip() if display_name else "",
            "place_type": place_type,
        }

