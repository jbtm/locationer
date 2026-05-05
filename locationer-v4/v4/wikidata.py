"""
Wikidata geocoder via SPARQL.

Searches for named entities with coordinates (P625) matching a location string.
Well-suited for cultural heritage, historic buildings, mountains, lakes — exactly
the subject matter of historical photo archives.

CC0 licence — persistent caching allowed, commercial use allowed.
No rate limit documented, but fair-use applies; caching minimises calls.
"""

import hashlib
import re
from typing import Optional

import requests

SPARQL_URL = "https://query.wikidata.org/sparql"
SPARQL_HEADERS = {
    "User-Agent": "locationer/4.0 (geocoding historical photo archives)",
    "Accept": "application/sparql-results+json",
}

# Wikidata instance-of types considered "precise" (not just a city/admin area)
_PRECISE_INSTANCES = {
    "Q570116",   # tourist attraction
    "Q2065736",  # cultural property
    "Q23413",    # castle
    "Q16560",    # palace
    "Q16970",    # church building
    "Q34627",    # mosque
    "Q1402592",  # chapel
    "Q839954",   # archaeological site
    "Q33506",    # museum
    "Q3914",     # school
    "Q4663385",  # historic railway station
    "Q55488",    # railway station
    "Q12280",    # bridge
    "Q23442",    # island
    "Q8502",     # mountain
    "Q23397",    # lake
    "Q4022",     # river
    "Q1248784",  # airport
    "Q1076486",  # sports venue
    "Q207694",   # art museum
    "Q44613",    # monastery
    "Q751876",   # lighthouse
    "Q2247863",  # ski resort
}

_SPARQL_TEMPLATE = """\
SELECT ?item ?itemLabel ?lat ?lon WHERE {{
  ?item wdt:P625 ?coords ;
        rdfs:label ?itemLabel .
  FILTER(LANG(?itemLabel) IN ("de","fr","it","en","rm"))
  FILTER(CONTAINS(LCASE(?itemLabel), LCASE("{name}")))
  BIND(geof:latitude(?coords)  AS ?lat)
  BIND(geof:longitude(?coords) AS ?lon)
}}
ORDER BY STRLEN(?itemLabel)
LIMIT 10
"""


def _key(text: str) -> str:
    return hashlib.sha256(f"wikidata|{text.lower().strip()}".encode()).hexdigest()[:20]


class Wikidata:
    def __init__(self, cache_conn, debug: bool = False):
        self.cache_conn = cache_conn
        self.debug = debug
        self.call_count = 0
        self._last_error: str = ""
        self._init_cache()

    def _init_cache(self):
        self.cache_conn.execute("""
            CREATE TABLE IF NOT EXISTS wikidata_cache (
                key          TEXT PRIMARY KEY,
                lat          REAL,
                lon          REAL,
                label        TEXT,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.cache_conn.commit()

    def search(
        self,
        location: str,
        near: Optional[tuple[float, float]] = None,
        max_dist_km: float = 50.0,
    ) -> Optional[dict]:
        """
        Search Wikidata for `location`. If `near` is given, filter to results
        within `max_dist_km` of those coordinates.
        Returns dict with lat/lon/name or None.
        """
        if not location or len(location) < 3:
            return None

        k = _key(location)

        # Cache lookup
        row = self.cache_conn.execute(
            "SELECT lat, lon, label FROM wikidata_cache WHERE key=?", (k,)
        ).fetchone()
        if row is not None:
            if row[0] is None:
                return None
            return {"lat": row[0], "lon": row[1], "name": row[2] or location}

        self.call_count += 1

        query = _SPARQL_TEMPLATE.format(name=location.replace('"', ""))
        try:
            resp = requests.get(
                SPARQL_URL,
                params={"query": query},
                headers=SPARQL_HEADERS,
                timeout=10,
            )
            data = resp.json()
        except Exception as e:
            self._last_error = str(e)
            self.call_count -= 1
            return None

        bindings = data.get("results", {}).get("bindings", [])
        if not bindings:
            self._cache_miss(k)
            return None

        # Pick best candidate — if near provided, filter by proximity
        best = None
        best_dist = float("inf")
        for b in bindings:
            try:
                lat = float(b["lat"]["value"])
                lon = float(b["lon"]["value"])
                label = b["itemLabel"]["value"]
            except (KeyError, ValueError):
                continue
            if near:
                dist = _haversine(lat, lon, near[0], near[1])
                if dist > max_dist_km:
                    continue
                if dist < best_dist:
                    best_dist = dist
                    best = (lat, lon, label)
            else:
                best = (lat, lon, label)
                break

        if best is None:
            self._cache_miss(k)
            return None

        lat, lon, label = best
        self.cache_conn.execute(
            "INSERT OR REPLACE INTO wikidata_cache (key,lat,lon,label) VALUES (?,?,?,?)",
            (k, lat, lon, label),
        )
        self.cache_conn.commit()
        return {"lat": lat, "lon": lon, "name": label}

    def _cache_miss(self, key: str):
        self.cache_conn.execute(
            "INSERT OR IGNORE INTO wikidata_cache (key,lat,lon,label) VALUES (?,NULL,NULL,NULL)",
            (key,),
        )
        self.cache_conn.commit()


def _haversine(lat1, lon1, lat2, lon2) -> float:
    import math
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2-lat1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
