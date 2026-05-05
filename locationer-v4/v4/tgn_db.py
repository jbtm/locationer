"""
Getty Thesaurus of Geographic Names (TGN) — local SQLite interface.

The full TGN RDF (~1 GB) is downloaded once and imported into SQLite via
import_tgn.py.  All lookups are then purely local — no network needed.

TGN is strong for:  cultural heritage sites, historic cities, monuments,
                    mountains with art-historical significance.
TGN is weak for:    small hamlets, current addresses, non-cultural geography
                    → GeoNames covers those.

ODC-By licence — persistent caching and commercial use allowed.
"""

import sqlite3
import unicodedata
from typing import Optional


def ascii_norm(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace(".", "")


# TGN place types that represent a precise named location (not just a city/admin area)
PRECISE_TYPES = {
    "castles", "palaces", "churches", "cathedrals", "temples", "mosques",
    "monasteries", "abbeys", "chapels", "ruins", "archaeological sites",
    "mountains", "peaks", "mountain ranges", "passes", "glaciers",
    "lakes", "rivers", "waterfalls", "valleys", "gorges",
    "bridges", "towers", "monuments", "memorials", "fountains",
    "museums", "galleries", "theatres", "amphitheaters",
    "squares", "plazas", "parks", "gardens",
    "railway stations", "airports",
}


class TgnDatabase:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def find(
        self,
        name: str,
        country_code: Optional[str] = None,
        near: Optional[tuple[float, float]] = None,
        max_dist_km: float = 50.0,
    ) -> Optional[sqlite3.Row]:
        """
        Find the best TGN match for `name`.
        Prefers precise place types over inhabited places.
        When `near` is provided, restricts to candidates within max_dist_km.
        """
        norm = ascii_norm(name)
        if not norm or len(norm) < 3:
            return None

        # Search preferred name + alt names
        candidates = []
        seen = set()

        def _add(rows):
            for r in rows:
                if r["tgn_id"] not in seen:
                    seen.add(r["tgn_id"])
                    candidates.append(r)

        if country_code:
            _add(self.conn.execute(
                "SELECT * FROM tgn_place WHERE name_norm=? AND country_code=? AND lat IS NOT NULL LIMIT 20",
                (norm, country_code),
            ).fetchall())
            _add(self.conn.execute(
                """SELECT p.* FROM tgn_name n
                   JOIN tgn_place p ON n.tgn_id = p.tgn_id
                   WHERE n.name_norm=? AND p.country_code=? AND p.lat IS NOT NULL LIMIT 20""",
                (norm, country_code),
            ).fetchall())

        # Widen to world
        _add(self.conn.execute(
            "SELECT * FROM tgn_place WHERE name_norm=? AND lat IS NOT NULL LIMIT 20",
            (norm,),
        ).fetchall())
        _add(self.conn.execute(
            """SELECT p.* FROM tgn_name n
               JOIN tgn_place p ON n.tgn_id = p.tgn_id
               WHERE n.name_norm=? AND p.lat IS NOT NULL LIMIT 20""",
            (norm,),
        ).fetchall())

        if not candidates:
            return None

        # Filter by proximity if near is given
        if near:
            import math
            def dist(r):
                R = 6371.0
                p1, p2 = math.radians(r["lat"]), math.radians(near[0])
                a = math.sin(math.radians(near[0]-r["lat"])/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(near[1]-r["lon"])/2)**2
                return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            candidates = [c for c in candidates if dist(c) <= max_dist_km]
            if candidates:
                candidates.sort(key=dist)

        if not candidates:
            return None

        # Prefer precise types over inhabited places
        # TGN stores singular ("mountain") but PRECISE_TYPES uses plural ("mountains")
        def _is_precise(row) -> bool:
            pt = (row["place_type"] or "").lower()
            return pt in PRECISE_TYPES or (pt + "s") in PRECISE_TYPES

        precise = [c for c in candidates if _is_precise(c)]
        return precise[0] if precise else candidates[0]

    def get_by_id(self, tgn_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM tgn_place WHERE tgn_id=?", (tgn_id,)
        ).fetchone()

    def close(self):
        self.conn.close()
