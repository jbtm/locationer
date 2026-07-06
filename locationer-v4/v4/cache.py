import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import GeoResult, NormalizedRecord


def _key(text: str) -> str:
    return hashlib.sha256(text.lower().strip().encode()).hexdigest()[:20]


class Cache:
    def __init__(self, path: str = "cache/locationer.sqlite"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self._init()

    def _init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS norm_cache (
                key                TEXT PRIMARY KEY,
                title              TEXT,
                description        TEXT,
                country            TEXT,
                region             TEXT,
                city               TEXT,
                location           TEXT,
                geocoding_queries  TEXT,
                created_at         TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS geo_cache (
                key            TEXT PRIMARY KEY,
                lat            REAL,
                lon            REAL,
                quality_score  INTEGER,
                fallback       INTEGER,
                source         TEXT,
                match_name     TEXT,
                ambiguous      INTEGER DEFAULT 0,
                location_hint  TEXT,
                city_hint      TEXT,
                country_hint   TEXT,
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS meta_cache (
                key        TEXT PRIMARY KEY,
                periode    TEXT,
                urheber    TEXT,
                technik    TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Migrate existing tables that predate new columns
        for stmt in (
            "ALTER TABLE norm_cache ADD COLUMN geocoding_queries TEXT",
            "ALTER TABLE geo_cache  ADD COLUMN ambiguous INTEGER DEFAULT 0",
            "ALTER TABLE geo_cache  ADD COLUMN location_hint TEXT",
            "ALTER TABLE geo_cache  ADD COLUMN city_hint TEXT",
            "ALTER TABLE geo_cache  ADD COLUMN country_hint TEXT",
        ):
            try:
                self.conn.execute(stmt)
            except Exception:
                pass  # column already exists
        self.conn.commit()

    def get_norm(self, raw_key: str) -> Optional[NormalizedRecord]:
        row = self.conn.execute(
            "SELECT title, description, country, city, location, region, geocoding_queries "
            "FROM norm_cache WHERE key=?",
            (_key(raw_key),),
        ).fetchone()
        if not row:
            return None
        gq = []
        if row[6]:
            try:
                gq = json.loads(row[6])
            except Exception:
                gq = []
        return NormalizedRecord(
            title=row[0], description=row[1], country=row[2],
            city=row[3], location=row[4], region=row[5] or "",
            geocoding_queries=gq,
        )

    def set_norm(self, raw_key: str, rec: NormalizedRecord):
        self.conn.execute(
            "INSERT OR REPLACE INTO norm_cache "
            "(key,title,description,country,region,city,location,geocoding_queries) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                _key(raw_key), rec.title, rec.description, rec.country,
                rec.region, rec.city, rec.location,
                json.dumps(rec.geocoding_queries) if rec.geocoding_queries else None,
            ),
        )
        self.conn.commit()

    def get_geo(self, query: str) -> Optional[GeoResult]:
        row = self.conn.execute(
            "SELECT lat,lon,quality_score,fallback,source,match_name,ambiguous "
            "FROM geo_cache WHERE key=?",
            (_key(query),),
        ).fetchone()
        if not row:
            return None
        return GeoResult(
            lat=row[0], lon=row[1], quality_score=row[2],
            fallback=bool(row[3]), source=row[4], match_name=row[5],
            ambiguous=bool(row[6]) if row[6] is not None else False,
        )

    def set_geo(self, query: str, result: GeoResult,
                location_hint: str = "", city_hint: str = "", country_hint: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO geo_cache "
            "(key,lat,lon,quality_score,fallback,source,match_name,ambiguous,"
            "location_hint,city_hint,country_hint) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                _key(query), result.lat, result.lon, result.quality_score,
                int(result.fallback), result.source, result.match_name,
                int(result.ambiguous),
                location_hint or None, city_hint or None, country_hint or None,
            ),
        )
        self.conn.commit()

    def get_meta(self, raw_key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT periode, urheber, technik FROM meta_cache WHERE key=?",
            (_key(raw_key),),
        ).fetchone()
        if row is None:
            return None
        return {"periode": row[0], "urheber": row[1], "technik": row[2]}

    def set_meta(self, raw_key: str, data: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta_cache (key, periode, urheber, technik) VALUES (?,?,?,?)",
            (_key(raw_key), data.get("periode"), data.get("urheber"), data.get("technik")),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
