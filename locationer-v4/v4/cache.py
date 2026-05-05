import hashlib
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
                key       TEXT PRIMARY KEY,
                title     TEXT,
                description TEXT,
                country   TEXT,
                region    TEXT,
                city      TEXT,
                location  TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS geo_cache (
                key           TEXT PRIMARY KEY,
                lat           REAL,
                lon           REAL,
                quality_score INTEGER,
                fallback      INTEGER,
                source        TEXT,
                match_name    TEXT,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS meta_cache (
                key        TEXT PRIMARY KEY,
                periode    TEXT,
                urheber    TEXT,
                technik    TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.conn.commit()

    def get_norm(self, raw_key: str) -> Optional[NormalizedRecord]:
        row = self.conn.execute(
            "SELECT title, description, country, city, location, region FROM norm_cache WHERE key=?",
            (_key(raw_key),),
        ).fetchone()
        if not row:
            return None
        return NormalizedRecord(
            title=row[0], description=row[1], country=row[2],
            city=row[3], location=row[4], region=row[5] or "",
        )

    def set_norm(self, raw_key: str, rec: NormalizedRecord):
        self.conn.execute(
            "INSERT OR REPLACE INTO norm_cache (key,title,description,country,region,city,location) VALUES (?,?,?,?,?,?,?)",
            (_key(raw_key), rec.title, rec.description, rec.country, rec.region, rec.city, rec.location),
        )
        self.conn.commit()

    def get_geo(self, query: str) -> Optional[GeoResult]:
        row = self.conn.execute(
            "SELECT lat,lon,quality_score,fallback,source,match_name FROM geo_cache WHERE key=?",
            (_key(query),),
        ).fetchone()
        if not row:
            return None
        return GeoResult(
            lat=row[0], lon=row[1], quality_score=row[2],
            fallback=bool(row[3]), source=row[4], match_name=row[5],
        )

    def set_geo(self, query: str, result: GeoResult):
        self.conn.execute(
            "INSERT OR REPLACE INTO geo_cache (key,lat,lon,quality_score,fallback,source,match_name) VALUES (?,?,?,?,?,?,?)",
            (_key(query), result.lat, result.lon, result.quality_score,
             int(result.fallback), result.source, result.match_name),
        )
        self.conn.commit()

    def get_meta(self, raw_key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT periode, urheber, technik FROM meta_cache WHERE key=?",
            (_key(raw_key),),
        ).fetchone()
        # A stored row with all-None fields is still a valid cache entry (extraction ran, found nothing)
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
