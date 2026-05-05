import math
import sqlite3
import unicodedata
from typing import Optional

# Non-English country name → ISO-2 code
COUNTRY_ALIASES: dict[str, str] = {
    # German
    "schweiz": "CH", "osterreich": "AT", "deutschland": "DE", "frankreich": "FR",
    "italien": "IT", "spanien": "ES", "norwegen": "NO", "schweden": "SE",
    "danemark": "DK", "finnland": "FI", "niederlande": "NL", "belgien": "BE",
    "ungarn": "HU", "tschechien": "CZ", "kroatien": "HR", "slowenien": "SI",
    "rumanien": "RO", "bulgarien": "BG", "griechenland": "GR", "turkei": "TR",
    "furstentum liechtenstein": "LI",
    # French
    "suisse": "CH", "autriche": "AT", "allemagne": "DE",
    "italie": "IT", "espagne": "ES", "norvege": "NO", "suede": "SE",
    "inde": "IN", "chine": "CN", "japon": "JP",
    "etats-unis": "US", "royaume-uni": "GB", "angleterre": "GB",
    # Italian
    "italia": "IT", "svizzera": "CH", "austria": "AT", "germania": "DE",
    "spagna": "ES", "norvegia": "NO", "inghilterra": "GB",
    # Others
    "india": "IN", "england": "GB", "uk": "GB", "usa": "US",
    "liechtenstein": "LI",
}

# Feature classes that represent a specific named location (not admin or city)
PRECISE_CLASSES = {"S", "T", "H", "U", "V", "L"}

# Priority order for populated-place feature codes (lower = better)
PPL_PRIORITY = {"PPLC": 0, "PPLA": 1, "PPLA2": 2, "PPLA3": 3, "PPLA4": 4, "PPL": 5, "PPLX": 6}


def ascii_norm(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace(".", "")  # match DB normalization: "St." → "st", "U.S.A." → "usa"
    return s


def _dist_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ, dλ = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class GeoDatabase:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def country_to_code(self, country: str) -> Optional[str]:
        if not country:
            return None
        norm = ascii_norm(country.strip())
        if norm in COUNTRY_ALIASES:
            return COUNTRY_ALIASES[norm]
        row = self.conn.execute(
            "SELECT country_code FROM geo_country WHERE country_name_norm=?", (norm,)
        ).fetchone()
        return row["country_code"] if row else None

    def find_precise(
        self,
        name: str,
        country_code: Optional[str] = None,
        near: Optional[tuple[float, float]] = None,
    ) -> Optional[sqlite3.Row]:
        """
        Find a non-city/admin feature by name.

        Collects candidates from both the country-filtered and world-wide search,
        then ranks by distance to `near` (city centre) when provided — this
        disambiguates names that exist in multiple places (e.g. "Piazza San Pietro").
        Falls back to preferring the country match when no `near` is given.
        """
        norm = ascii_norm(name)
        candidates: list[sqlite3.Row] = []
        seen_ids: set[int] = set()

        def _add(rows):
            for r in rows:
                if r["feature_class"] in PRECISE_CLASSES and r["geoname_id"] not in seen_ids:
                    seen_ids.add(r["geoname_id"])
                    candidates.append(r)

        # Widening to world-wide is only safe when we have a proximity anchor (`near`)
        # to validate the expanded candidates.  Without it, a global search risks
        # matching a homonym in a distant country (e.g. Italian city "Todi" for
        # Swiss mountain "Tödi").  With `near` the proximity ranking picks the
        # geographically correct result (e.g. Piazza di San Pietro in Vatican vs.
        # other "Piazza San Pietro" entries in Italy).
        scope = [country_code, None] if (country_code and near is not None) else [country_code] if country_code else [None]
        for cc in scope:
            cc_clause = " AND country_code=?" if cc else ""
            cc_params = (cc,) if cc else ()

            _add(self.conn.execute(
                f"SELECT * FROM geofeature WHERE ascii_name_norm=?{cc_clause} LIMIT 20",
                (norm,) + cc_params,
            ).fetchall())

            _add(self.conn.execute(
                f"""SELECT g.* FROM geo_alt_name a
                    JOIN geofeature g ON a.geoname_id = g.geoname_id
                    WHERE a.alt_name_norm=?{cc_clause} LIMIT 20""",
                (norm,) + cc_params,
            ).fetchall())

        if not candidates:
            return None

        if near:
            # Pick the candidate closest to the known city centre
            candidates.sort(key=lambda r: _dist_km(r["lat"], r["lon"], near[0], near[1]))
        elif country_code:
            # Prefer same-country result when no proximity hint
            candidates.sort(key=lambda r: 0 if r["country_code"] == country_code else 1)

        return candidates[0]

    def region_to_admin1(self, country_code: str, region: str) -> Optional[str]:
        """Resolve a region/canton/state name to its admin1 code.

        Tries ascii_norm (for geofeature.ascii_name_norm) and raw lowercase
        (for geo_alt_name.alt_name_norm which keeps diacritics).
        """
        norm = ascii_norm(region)
        raw  = region.strip().lower()

        # Primary name match (ascii-normalized, e.g. "kanton graubuenden")
        row = self.conn.execute(
            "SELECT admin1 FROM geofeature WHERE feature_code='ADM1' AND country_code=? AND ascii_name_norm=? LIMIT 1",
            (country_code, norm),
        ).fetchone()
        if row:
            return row["admin1"]

        # Alt-name match — stored as raw lowercase with diacritics intact
        for lookup in (raw, norm):
            row = self.conn.execute(
                """SELECT g.admin1 FROM geo_alt_name a
                   JOIN geofeature g ON a.geoname_id = g.geoname_id
                   WHERE g.feature_code='ADM1' AND g.country_code=? AND a.alt_name_norm=?
                   LIMIT 1""",
                (country_code, lookup),
            ).fetchone()
            if row:
                return row["admin1"]

        return None

    def find_city(
        self, name: str, country_code: Optional[str] = None,
        admin1_hint: Optional[str] = None,
    ) -> Optional[sqlite3.Row]:
        """Return the best populated-place matching name.

        Collects candidates from direct name and alt-name searches (with and without
        country filter), then picks the best by feature-code priority and population.
        When admin1_hint is provided (canton/state code), it is used as a tiebreaker
        to disambiguate same-named places in different regions.
        """
        norm = ascii_norm(name)
        candidates: list[sqlite3.Row] = []
        seen_ids: set[int] = set()

        def _add(rows):
            for r in rows:
                if r["feature_class"] == "P" and r["geoname_id"] not in seen_ids:
                    seen_ids.add(r["geoname_id"])
                    candidates.append(r)

        for cc in ([country_code, None] if country_code else [None]):
            cc_clause = " AND country_code=?" if cc else ""
            cc_params = (cc,) if cc else ()

            _add(self.conn.execute(
                f"SELECT * FROM geofeature WHERE ascii_name_norm=? AND feature_class='P'{cc_clause} LIMIT 10",
                (norm,) + cc_params,
            ).fetchall())

            _add(self.conn.execute(
                f"""SELECT g.* FROM geo_alt_name a
                    JOIN geofeature g ON a.geoname_id = g.geoname_id
                    WHERE a.alt_name_norm=? AND g.feature_class='P'{cc_clause} LIMIT 10""",
                (norm,) + cc_params,
            ).fetchall())

        # ADM3/ADM4 fallback: municipalities stored as administrative (not populated)
        # areas — e.g. small Swiss Gemeinden like Avers.
        # Trigger when: no candidates at all, OR all candidates are from other countries.
        same_country = [c for c in candidates if not country_code or c["country_code"] == country_code]
        if not same_country:
            cc_clause = " AND country_code=?" if country_code else ""
            cc_params = (country_code,) if country_code else ()
            adm = self.conn.execute(
                f"""SELECT * FROM geofeature
                    WHERE ascii_name_norm=?
                    AND feature_class='A'
                    AND feature_code IN ('ADM3','ADM4'){cc_clause}
                    LIMIT 5""",
                (norm,) + cc_params,
            ).fetchall()
            if adm:
                adm.sort(key=lambda r: (
                    0 if (admin1_hint and r["admin1"] == admin1_hint) else 1,
                    0 if r["feature_code"] == "ADM3" else 1,
                ))
                return adm[0]

        if not candidates:
            return None

        # Sort: region hint first, then feature-code priority, then population
        candidates.sort(key=lambda r: (
            0 if (not country_code or r["country_code"] == country_code) else 1,
            0 if (admin1_hint and r["admin1"] == admin1_hint) else 1,
            PPL_PRIORITY.get(r["feature_code"], 99),
            -(r["population"] or 0),
        ))

        return candidates[0]

    def find_region_name(self, country_code: str, admin1_code: str) -> str:
        """Return the ADM1 (state/province/canton) name for a country + admin1 code."""
        if not country_code or not admin1_code:
            return ""
        row = self.conn.execute(
            "SELECT name FROM geofeature WHERE feature_code='ADM1' AND country_code=? AND admin1=? LIMIT 1",
            (country_code, admin1_code),
        ).fetchone()
        return row["name"] if row else ""

    def close(self):
        self.conn.close()
