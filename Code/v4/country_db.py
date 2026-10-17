"""
Country-specific geocoding database — pluggable layer in the GeoStack pipeline.

Each country DB is a normalized SQLite built from official national sources
(swisstopo, BKG, ISTAT, INSEE, BEV, Kartverket, …).

Schema: one row per name variant — precise lookups are single index scans.

Pipeline position:
  Step 2.7  country_db precise  (after TGN, before Nominatim)  → Score 5
  Step 4.5  country_db city     (after GEO DB city fallback)    → Score 3

Auto-loaded via .env:
  COUNTRY_DB_CH=/path/to/ch_country.sqlite
  COUNTRY_DB_DE=/path/to/de_country.sqlite
  ...
"""

import math
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional


# ── Normalisation (same logic as geo_db.py ascii_norm) ───────────────────────
def _norm(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace(".", "").replace("/", " ").replace("-", " ")
    return " ".join(s.split())


def _dist_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


# ── Schema ────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS places (
    rowid         INTEGER PRIMARY KEY,
    place_id      TEXT    NOT NULL,   -- source ID (same for all name variants of one place)
    canonical_name TEXT   NOT NULL,   -- primary display name
    name_norm     TEXT    NOT NULL,   -- this variant normalised (indexed)
    lat           REAL    NOT NULL,
    lon           REAL    NOT NULL,
    feature_type  TEXT    NOT NULL,   -- 'precise' or 'city'
    admin1        TEXT,               -- state/canton/county code
    population    INTEGER DEFAULT 0,
    country_code  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cdb_name    ON places (name_norm, country_code);
CREATE INDEX IF NOT EXISTS idx_cdb_type    ON places (feature_type, country_code);
CREATE INDEX IF NOT EXISTS idx_cdb_admin1  ON places (admin1, country_code);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class CountryDB:
    """Query interface for one country-specific place-name database."""

    def __init__(self, db_path: str):
        self.path = db_path
        self._con = sqlite3.connect(db_path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row

    # ── Public API ────────────────────────────────────────────────────────────
    def find_precise(
        self,
        name: str,
        country_code: str,
        near: "tuple[float,float] | None" = None,
        radius_km: float = 50.0,
        admin1_hint: "str | None" = None,
    ) -> "dict | None":
        """Named place (landmark, geo-feature, etc.) — maps to Score 5."""
        norm = _norm(name)
        if admin1_hint:
            rows = self._con.execute("""
                SELECT * FROM places
                WHERE name_norm = ? AND country_code = ?
                  AND feature_type = 'precise' AND admin1 = ?
            """, (norm, country_code, admin1_hint)).fetchall()
            if not rows:  # fall through without admin1 constraint
                rows = self._con.execute("""
                    SELECT * FROM places
                    WHERE name_norm = ? AND country_code = ? AND feature_type = 'precise'
                """, (norm, country_code)).fetchall()
        else:
            rows = self._con.execute("""
                SELECT * FROM places
                WHERE name_norm = ? AND country_code = ? AND feature_type = 'precise'
            """, (norm, country_code)).fetchall()

        return self._best(rows, near, radius_km)

    def find_city(
        self,
        name: str,
        country_code: str,
        admin1_hint: "str | None" = None,
    ) -> "dict | None":
        """Populated place / municipality — maps to Score 3."""
        norm = _norm(name)
        if admin1_hint:
            rows = self._con.execute("""
                SELECT * FROM places
                WHERE name_norm = ? AND country_code = ?
                  AND feature_type = 'city' AND admin1 = ?
                ORDER BY population DESC
            """, (norm, country_code, admin1_hint)).fetchall()
            if not rows:
                rows = self._con.execute("""
                    SELECT * FROM places
                    WHERE name_norm = ? AND country_code = ? AND feature_type = 'city'
                    ORDER BY population DESC
                """, (norm, country_code)).fetchall()
        else:
            rows = self._con.execute("""
                SELECT * FROM places
                WHERE name_norm = ? AND country_code = ? AND feature_type = 'city'
                ORDER BY population DESC
            """, (norm, country_code)).fetchall()

        return self._best(rows, None, None)

    def count(self) -> int:
        return self._con.execute("SELECT COUNT(DISTINCT place_id) FROM places").fetchone()[0]

    def info(self) -> dict:
        rows = self._con.execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def close(self):
        self._con.close()

    # ── Internal ──────────────────────────────────────────────────────────────
    def _best(self, rows, near, radius_km) -> "dict | None":
        if not rows:
            return None
        if near and radius_km:
            rows = [r for r in rows
                    if _dist_km(r["lat"], r["lon"], near[0], near[1]) <= radius_km]
        if not rows:
            return None
        if near:
            return dict(min(rows, key=lambda r: _dist_km(r["lat"], r["lon"], near[0], near[1])))
        # No proximity filter: prefer highest population, then first row
        return dict(max(rows, key=lambda r: r["population"] or 0))


# ── Registry loader ───────────────────────────────────────────────────────────
SUPPORTED = {"CH", "DE", "FR", "IT", "AT", "NO"}


def load_country_dbs(env: dict) -> "dict[str, CountryDB]":
    """
    Read COUNTRY_DB_XX env vars and return a {country_code: CountryDB} registry.
    Missing / non-existent files are silently skipped.
    """
    dbs: dict[str, CountryDB] = {}
    for cc in SUPPORTED:
        key = f"COUNTRY_DB_{cc}"
        path = env.get(key, "").strip()
        if path and Path(path).exists():
            try:
                dbs[cc] = CountryDB(path)
            except Exception as e:
                print(f"[country_db] Warning: could not open {path}: {e}")
    return dbs
