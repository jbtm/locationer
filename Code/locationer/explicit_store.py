"""
Persistent store for manual location overrides (explicit corrections).

Stored in explicit_list/explicit.sqlite — separate from the auto-generated
cache so this file can be version-controlled while cache/locationer.sqlite is not.

Pattern syntax:  location|city|country   (* = wildcard for any field)
"""

import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Optional

from .models import GeoResult


def _fold(s: str) -> str:
    """Klein, ohne Akzente."""
    s = unicodedata.normalize("NFKD", (s or "").casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def _woerter(s: str) -> set:
    return {w for w in re.split(r"[^a-z0-9]+", _fold(s)) if len(w) >= 3}


class ExplicitStore:
    def __init__(self, path: str = "explicit_list/explicit.sqlite"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS overrides (
                pattern       TEXT PRIMARY KEY,
                lat           REAL NOT NULL,
                lon           REAL NOT NULL,
                quality_score INTEGER DEFAULT 3,
                match_name    TEXT,
                note          TEXT,
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS norm_overrides (
                pattern    TEXT PRIMARY KEY,  -- case-insensitive substring match on raw row text
                city       TEXT,              -- set city to this value (null = don't touch)
                country    TEXT,              -- set country to this value (null = don't touch)
                location   TEXT,              -- set location to this value (null = don't touch)
                note       TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.conn.commit()

    def match(self, location: str, city: str, country: str) -> Optional[GeoResult]:
        """Return override if any pattern matches location|city|country."""
        for pat, lat, lon, score, name in self.conn.execute(
            "SELECT pattern, lat, lon, quality_score, match_name FROM overrides"
        ).fetchall():
            parts = (pat + "||").split("|")[:3]
            p_loc, p_city, p_country = parts[0], parts[1], parts[2]
            if (
                (p_loc == "*" or p_loc.lower() == location.lower())
                and (p_city == "*" or p_city.lower() == city.lower())
                and (p_country == "*" or p_country.lower() == country.lower())
            ):
                return GeoResult(
                    lat=lat, lon=lon, quality_score=score,
                    fallback=False, source="override", match_name=name or pat,
                )
        return None

    def add(self, pattern: str, lat: float, lon: float,
            quality_score: int = 3, match_name: str = "", note: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO overrides "
            "(pattern,lat,lon,quality_score,match_name,note) VALUES (?,?,?,?,?,?)",
            (pattern, lat, lon, quality_score, match_name, note),
        )
        self.conn.commit()

    def list_all(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT pattern, lat, lon, quality_score, match_name, note "
            "FROM overrides ORDER BY created_at"
        ).fetchall()

    def remove(self, pattern: str) -> bool:
        n = self.conn.execute(
            "DELETE FROM overrides WHERE pattern=?", (pattern,)
        ).rowcount
        self.conn.commit()
        return n > 0

    # ── norm_overrides ────────────────────────────────────────────────────────

    def match_norm(self, raw_text: str, current_city: str = "") -> Optional[dict]:
        """Return {city, country, location} corrections if a pattern is found in raw_text.

        Longer patterns are checked first so specific entries (e.g. 'Lenzerheide')
        win over shorter ones (e.g. 'Lenz').

        ZWEI REGELN (2026-10-17), beide gegen dieselbe Fehlerklasse:

        **Nur ganze Woerter.**  Frueher reichte ein Teilstring: „Münster" traf
        Grossmünster, Fraumünster, Beromünster; „Lenz" traf Koblenz; „Uors"
        traf Trais FlUors; „Mühlen" traf Mühlengebäude.  Akzente zaehlen nicht
        („San Nicla" trifft „San Niclà").

        **Eine Stadt-Korrektur ersetzt keine andere Stadt.**  Steht in der
        Eingabe schon eine Stadt (`current_city`: Adresse/City-Spalte, sonst
        Haikus Stadt), greift ein Override, der die Stadt setzt, nur, wenn das
        MUSTER mit ihr ein Wort teilt — „Sils im Engadin" → „Sils im
        Engadin/Segl" ja, „Zürich" → „Müstair" nein.  Nicht die Zielstadt
        mitzaehlen: sonst machte „Münstertal" → „Val Müstair" aus der Adresse
        „Santa Maria Val Müstair" das ungenauere „Val Müstair".  Am ZIN-Bestand: der alte Münster-Override
        legte 101 Zürcher, 41 Berner, 22 Basler und 27 Walliser (Münster VS)
        Bilder nach Müstair; Sur, Tiefencastel, Mon wurden umgestellt, weil in
        der Beschreibung ein Nachbarort stand.  Die neue Regel aenderte 26
        Treffergruppen — alle bisherige Fehlgriffe.
        """
        text = _fold(raw_text)
        rows = sorted(
            self.conn.execute(
                "SELECT pattern, city, country, location, note FROM norm_overrides"
            ).fetchall(),
            key=lambda r: -len(r[0]),
        )
        for pat, city, country, location, _ in rows:
            if not re.search(r"(?<![a-z0-9])" + re.escape(_fold(pat)) + r"(?![a-z0-9])", text):
                continue
            if city and current_city and not (_woerter(current_city) & _woerter(pat)):
                return None   # wuerde eine andere Stadt ersetzen — und kein kuerzeres Muster probieren
            result = {}
            if city:
                result["city"] = city
            if country:
                result["country"] = country
            if location:
                result["location"] = location
            return result if result else None
        return None

    def add_norm(self, pattern: str, city: str = "", country: str = "",
                 location: str = "", note: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO norm_overrides "
            "(pattern,city,country,location,note) VALUES (?,?,?,?,?)",
            (pattern, city or None, country or None, location or None, note),
        )
        self.conn.commit()

    def list_norm(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT pattern, city, country, location, note FROM norm_overrides ORDER BY created_at"
        ).fetchall()

    def remove_norm(self, pattern: str) -> bool:
        n = self.conn.execute(
            "DELETE FROM norm_overrides WHERE pattern=?", (pattern,)
        ).rowcount
        self.conn.commit()
        return n > 0

    def close(self):
        self.conn.close()
