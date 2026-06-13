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
    """Normalize to lowercase ASCII, stripping diacritics (NFD strip variant).
    Matches GeoNames entries like Göschenen→'goschenen', Bürglen→'burglen'.
    '/' is converted to space to match the GeoNames builder convention
    (e.g. 'Sils im Engadin/Segl' → 'sils im engadin segl')."""
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace(".", "").replace("/", " ")
    return " ".join(s.split())


def ascii_norm_de(s: str) -> str:
    """German umlaut expansion variant: ö→oe, ü→ue, ä→ae, ß→ss.
    Matches GeoNames entries like Zürich→'zuerich', Schlössli→'schloessli'.
    '/' is converted to space (see ascii_norm)."""
    s = s.lower()
    for src, tgt in (("ö", "oe"), ("ü", "ue"), ("ä", "ae"), ("ß", "ss")):
        s = s.replace(src, tgt)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace(".", "").replace("/", " ")
    return " ".join(s.split())


def _ascii_norm_variants(s: str) -> list[str]:
    """Return both normalization variants, deduplicated."""
    v1 = ascii_norm(s)
    v2 = ascii_norm_de(s)
    return [v1, v2] if v1 != v2 else [v1]


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

    def _count_alt_names(self, geoname_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as c FROM geo_alt_name WHERE geoname_id=?", (geoname_id,)
        ).fetchone()
        return row["c"] if row else 0

    def find_precise(
        self,
        name: str,
        country_code: Optional[str] = None,
        near: Optional[tuple[float, float]] = None,
        admin1_hint: Optional[str] = None,
        prefer_admin3: Optional[str] = None,
    ) -> Optional[sqlite3.Row]:
        """
        Find a non-city/admin feature by name.

        Collects candidates from both the country-filtered and world-wide search,
        then ranks by distance to `near` (city centre) when provided — this
        disambiguates names that exist in multiple places (e.g. "Piazza San Pietro").
        Falls back to preferring the country match when no `near` is given.
        """
        norms = _ascii_norm_variants(name)
        candidates: list[sqlite3.Row] = []
        seen_ids: set[int] = set()

        def _add(rows):
            for r in rows:
                if r["feature_class"] in PRECISE_CLASSES and r["geoname_id"] not in seen_ids:
                    seen_ids.add(r["geoname_id"])
                    candidates.append(r)

        scope = [country_code, None] if (country_code and near is not None) else [country_code] if country_code else [None]
        for cc in scope:
            cc_clause = " AND country_code=?" if cc else ""
            cc_params = (cc,) if cc else ()

            for norm in norms:
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

        # Hard admin1 filter — analogous to find_city. When the caller asserts
        # a region, candidates outside that country+admin1 are rejected, even
        # if proximity to `near` would otherwise favour them. Prevents picking
        # a wrong-canton match for ambiguous location names (e.g. "Flühmatt" in
        # canton SO when the photo is from canton OW).
        if admin1_hint and country_code:
            candidates = [
                c for c in candidates
                if c["country_code"] == country_code and c["admin1"] == admin1_hint
            ]

        if not candidates:
            return None

        if near:
            candidates.sort(key=lambda r: (
                0 if (prefer_admin3 and r["admin3"] == prefer_admin3) else 1,
                _dist_km(r["lat"], r["lon"], near[0], near[1]),
            ))
        elif country_code:
            # No proximity anchor: rank by same-country, then admin1 match, then
            # alt_name count (proxy for how well-known the place is).
            candidates.sort(key=lambda r: (
                0 if r["country_code"] == country_code else 1,
                0 if (admin1_hint and r["admin1"] == admin1_hint) else 1,
                -self._count_alt_names(r["geoname_id"]),
            ))

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

    def _strip_city_disambig(self, name: str) -> list[str]:
        """Return candidate simplifications of a city name.

        'Campo (Blenio)' → ['Campo']
        'Breil/Brigels'  → ['Breil', 'Brigels']   (try both parts)
        'Bergün/Bravuogn' → ['Bergün', 'Bravuogn']
        """
        import re
        name = re.sub(r'\s*\([^)]*\)', '', name).strip()
        parts = [p.strip() for p in name.split('/') if p.strip()]
        return parts if len(parts) > 1 else parts[:1]

    def find_city(
        self, name: str, country_code: Optional[str] = None,
        admin1_hint: Optional[str] = None,
    ) -> Optional[sqlite3.Row]:
        """Return the best populated-place matching name.

        Collects candidates from direct name and alt-name searches (with and without
        country filter), then picks the best by feature-code priority and population.
        When admin1_hint is provided, it acts as a hard filter on same-country
        candidates — analogous to the country BBox check. This prevents picking
        a same-named place in the wrong canton/state (e.g. Lugnez/JU when the
        caller asserts Graubünden).
        """
        norms = _ascii_norm_variants(name)
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

            for norm in norms:
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

        # Hard admin1 filter — analogous to country BBox. When the caller
        # asserts a region, candidates must live in that country+admin1; any
        # foreign-country match (e.g. Bern/KS/US when hint is CH/GR) is rejected.
        if admin1_hint and country_code:
            candidates = [
                c for c in candidates
                if c["country_code"] == country_code and c["admin1"] == admin1_hint
            ]

        # ADM3/ADM4 fallback
        same_country = [c for c in candidates if not country_code or c["country_code"] == country_code]
        if not same_country:
            cc_clause = " AND country_code=?" if country_code else ""
            cc_params = (country_code,) if country_code else ()
            for norm in norms:
                adm = self.conn.execute(
                    f"""SELECT * FROM geofeature
                        WHERE ascii_name_norm=?
                        AND feature_class='A'
                        AND feature_code IN ('ADM3','ADM4'){cc_clause}
                        LIMIT 5""",
                    (norm,) + cc_params,
                ).fetchall()
                if admin1_hint and country_code:
                    adm = [r for r in adm if r["admin1"] == admin1_hint]
                if adm:
                    adm.sort(key=lambda r: (
                        0 if (admin1_hint and r["admin1"] == admin1_hint) else 1,
                        0 if r["feature_code"] == "ADM3" else 1,
                    ))
                    return adm[0]

        # If still no candidates, retry with parenthetical/slash stripped.
        if not candidates and country_code:
            for stripped in self._strip_city_disambig(name):
                if stripped == name:
                    continue
                for norm_s in _ascii_norm_variants(stripped):
                    cc_rows = self.conn.execute(
                        "SELECT * FROM geofeature WHERE ascii_name_norm=? AND feature_class='P' AND country_code=? LIMIT 10",
                        (norm_s, country_code),
                    ).fetchall()
                    for r in cc_rows:
                        if r["geoname_id"] not in seen_ids:
                            if admin1_hint and r["admin1"] != admin1_hint:
                                continue
                            candidates.append(r)
                    if not candidates:
                        alt_rows = self.conn.execute(
                            """SELECT g.* FROM geo_alt_name a
                               JOIN geofeature g ON a.geoname_id = g.geoname_id
                               WHERE a.alt_name_norm=? AND g.feature_class='P' AND g.country_code=? LIMIT 10""",
                            (norm_s, country_code),
                        ).fetchall()
                        for r in alt_rows:
                            if r["geoname_id"] not in seen_ids:
                                if admin1_hint and r["admin1"] != admin1_hint:
                                    continue
                                candidates.append(r)
                    if not candidates:
                        adm_s = self.conn.execute(
                            "SELECT * FROM geofeature WHERE ascii_name_norm=? AND feature_class='A' AND feature_code IN ('ADM3','ADM4') AND country_code=? LIMIT 5",
                            (norm_s, country_code),
                        ).fetchall()
                        if admin1_hint:
                            adm_s = [r for r in adm_s if r["admin1"] == admin1_hint]
                        if adm_s:
                            return adm_s[0]
                if candidates:
                    break  # found via first matching part

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

    def find_admin1_centroid(self, country_code: str, admin1_code: str) -> "tuple[float, float] | None":
        """Return (lat, lon) of the ADM1 feature (canton/state/province) centroid."""
        row = self.conn.execute(
            "SELECT lat, lon FROM geofeature WHERE feature_code='ADM1' AND country_code=? AND admin1=? LIMIT 1",
            (country_code, admin1_code),
        ).fetchone()
        return (row["lat"], row["lon"]) if row else None

    def find_admin1_radius_km(self, country_code: str, admin1_code: str,
                              centroid: "tuple[float, float]") -> float:
        """Estimate admin1 spatial radius as max distance from centroid to any
        populated place in that region, plus a 20% buffer.

        Result is memoised per (country_code, admin1_code) so repeated calls
        within a pipeline run are free.
        """
        cache_key = (country_code, admin1_code)
        if not hasattr(self, "_admin1_radius_cache"):
            self._admin1_radius_cache: dict = {}
        if cache_key in self._admin1_radius_cache:
            return self._admin1_radius_cache[cache_key]

        rows = self.conn.execute(
            "SELECT lat, lon FROM geofeature "
            "WHERE feature_class='P' AND country_code=? AND admin1=?",
            (country_code, admin1_code),
        ).fetchall()

        if not rows:
            radius = 200.0  # conservative fallback for large/unknown regions
        else:
            clat, clon = centroid
            max_dist = max(
                math.sqrt(
                    (math.radians(r["lat"] - clat) * 6371) ** 2 +
                    (math.radians(r["lon"] - clon) * 6371 *
                     math.cos(math.radians(clat))) ** 2
                )
                for r in rows
            )
            radius = max(max_dist * 1.2, 20.0)

        self._admin1_radius_cache[cache_key] = radius
        return radius

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
