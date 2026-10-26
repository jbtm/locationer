import math
import sqlite3
import re
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
    # Englische Namen, die GeoNames anders schreibt (geo_country)
    "netherlands": "NL", "czech republic": "CZ", "vatican city": "VA",
    "holy see": "VA", "united states of america": "US",
    "palestinian territories": "PS", "state of palestine": "PS",
    # „Palestine" ist heute der Staat (Nablus, Bethlehem, Jericho).  Fuer
    # historische Angaben (vor 1948) setzt Haiku das heutige Land des Orts
    # (Jerusalem → Israel); landet trotzdem ein israelischer Ort hier, gibt
    # es keine Koordinate statt einer falschen.
    "palestine": "PS",
    "russian federation": "RU", "republic of korea": "KR",
    # Landesteile und historische Namen, die genau EINEM heutigen Land
    # entsprechen.  Mehrdeutige (Prussia, Ottoman Empire,
    # Austria-Hungary, Indochine) bewusst nicht: sie bleiben unbekannt, und
    # dann gibt es keine Koordinate (geostack.geocode).
    "scotland": "GB", "ecosse": "GB", "schottland": "GB", "scozia": "GB",
    "wales": "GB", "pays de galles": "GB",
    "northern ireland": "GB", "irlande du nord": "GB", "nordirland": "GB",
    "persia": "IR", "perse": "IR", "persien": "IR",
    "ceylon": "LK", "ceylan": "LK", "siam": "TH",
    "burma": "MM", "birmanie": "MM",
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


# Abkuerzungen am WORTANFANG.  GeoNames fuehrt „Saint-Jean", die Quelle schreibt
# „St-Jean" — ohne Erweiterung finden sich die beiden nie.
#
# Zwingend auf ganze Woerter angewandt, nicht auf Zeichenketten: eine
# Prefix-Ersetzung machte aus „Santis" ein „S.tis" und aus „Sanremo" ein
# „S.remo", weil beide zufaellig mit „san" beginnen.  Solche Kunstformen koennen
# in einer Datenbank mit 13 Mio. Zeilen durchaus etwas treffen — nur nichts
# Richtiges.
_ABK_PAARE = (("st", "saint"), ("ste", "sainte"))


def _ascii_norm_variants(s: str) -> list[str]:
    """Schreibvarianten eines Namens fuer die Suche.

    Liefert absichtlich MEHRERE Formen statt einer kanonischen: die
    GeoNames-Datenbank ist selbst uneinheitlich normalisiert.  Derselbe Name
    „Monte-Carlo" steht bei Monaco als `monte carlo`, bei Brasilien als
    `monte-carlo`.  Eine einzelne Abfrageform verfehlt darum systematisch die
    jeweils andere Schreibung — und traf im Fall Monte-Carlo ausgerechnet den
    brasilianischen Eintrag.

    Rein additiv: die bisherigen Formen bleiben an erster Stelle, es kommen nur
    weitere hinzu.  Die Suche kann dadurch mehr Kandidaten finden, nie weniger;
    welcher gewinnt, entscheidet unveraendert die Sortierung (gleiches Land
    zuerst) und danach die Laenderschranke im Geostack.
    """
    formen: list[str] = []

    def _dazu(x: str) -> None:
        x = " ".join(x.split())
        if x and x not in formen:
            formen.append(x)

    for basis in (ascii_norm(s), ascii_norm_de(s)):
        _dazu(basis)
        ohne_strich = basis.replace("-", " ")
        _dazu(ohne_strich)
        # Abkuerzung nur, wenn das erste WORT genau die Abkuerzung ist.
        # Der Bindestrich zaehlt dabei als Worttrenner („St-Jean").
        for form in (basis, ohne_strich):
            teile = form.replace("-", " ").split()
            if not teile:
                continue
            for kurz, lang in _ABK_PAARE:
                if teile[0] == kurz:
                    _dazu(" ".join([lang] + teile[1:]))
                elif teile[0] == lang:
                    _dazu(" ".join([kurz] + teile[1:]))
        _dazu(basis.replace("'", " ").replace("-", " "))     # „l'epalut" → `l epalut`
    # Die Alternativnamen (`geo_alt_name.alt_name_norm`) behalten die Akzente:
    # „castellón de la plana", „bâle".  Nur ASCII-Formen verfehlen sie — rund
    # 530000 von 3 Mio. Namen waren so unerreichbar („Espagne, Castellón de la
    # Plana" blieb ohne Koordinate, 2026-10-25).  Satzzeichen wie beim Aufbau
    # der Tabelle: zu Leerzeichen.
    _dazu(re.sub(r"[.\-/'():,]", " ", unicodedata.normalize("NFC", s.lower())))
    return formen


def _dist_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ, dλ = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# Laender, in denen GeoNames-ADM3 die Gemeinde ist (naechster_ort).
_GEMEINDE_IST_ADM3 = {"CH", "LI", "AT", "IT"}

# Auf welcher Verwaltungsebene GeoNames die Gemeinde fuehrt — fuer die
# Gemeinde-Pruefung (geostack._ausserhalb_gemeinde).  Geprueft 2026-10-25 an
# Anzahl ADM-Eintraegen und Beispielorten (Metz 57463, Nürnberg 09564000,
# Málaga 29067, Den Haag 0518).  Laender ohne Eintrag: nur Stadtradius.
GEMEINDE_EBENE = {"CH": 3, "LI": 3, "AT": 3, "IT": 3, "ES": 3,
                  "FR": 4, "DE": 4, "BE": 4, "NL": 2, "PT": 2}


def gemeinde_von(row) -> "tuple | None":
    """(Land, admin1 … adminN) bis zur Gemeindeebene — None, wenn unbekannt."""
    cc = row["country_code"]
    n = GEMEINDE_EBENE.get(cc)
    if not n:
        return None
    teile = tuple(row[f"admin{k}"] for k in range(1, n + 1))
    return (cc, *teile) if teile[-1] else None


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

    def wikidata_id(self, geoname_id) -> str:
        """Wikidata-Kennung aus GeoNames selbst (Alternativname mit Sprache „wkdt").

        Nur ein Teil der Eintraege hat eine (Chur nicht) — den Rest findet
        Wikidata.qid_fuer ueber die GeoNames-Nummer (P1566).
        """
        if not geoname_id:
            return ""
        row = self.conn.execute(
            "SELECT alt_name FROM geo_alt_name WHERE geoname_id=? AND language_code='wkdt' LIMIT 1",
            (geoname_id,)).fetchone()
        return row["alt_name"] if row else ""

    def gemeinde_id(self, row) -> "int | None":
        """GeoNames-Nummer der Gemeinde (ADMn nach GEMEINDE_EBENE), zu der ein Ort gehoert."""
        g = gemeinde_von(row)
        if g is None:
            return None
        n = len(g) - 1
        bed = " AND ".join(f"admin{k}=?" for k in range(1, n + 1))
        r = self.conn.execute(
            "SELECT geoname_id FROM geofeature INDEXED BY idx_geofeature_country_feature "
            f"WHERE country_code=? AND feature_class='A' AND feature_code=? AND {bed} LIMIT 1",
            (g[0], f"ADM{n}", *g[1:])).fetchone()
        return r["geoname_id"] if r else None

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

        Erst ADM1, dann ADM2 (→ dessen admin1).  Haiku nennt als Region oft
        ein Departement oder eine Provinz („Hautes-Pyrénées", „Pas-de-Calais",
        „Como").  Bis 2026-10-25 ergab das None — und damit fiel die harte
        Regionsgrenze stillschweigend weg: „Route de Gavarnie" mit Region
        Hautes-Pyrénées landete in der Dordogne.  Alle Schreibvarianten
        (_ascii_norm_variants), sonst blieb „Provence-Alpes-Côte d'Azur"
        unerkannt.
        """
        varianten = list(dict.fromkeys(_ascii_norm_variants(region) + [region.strip().lower()]))
        for code in ("ADM1", "ADM2"):
            for norm in varianten:
                row = self.conn.execute(
                    "SELECT admin1 FROM geofeature WHERE feature_code=? AND country_code=? "
                    "AND ascii_name_norm=? AND admin1 IS NOT NULL AND admin1 != '' LIMIT 1",
                    (code, country_code, norm),
                ).fetchone()
                if row:
                    return row["admin1"]
            # Alt-name match — stored as raw lowercase with diacritics intact
            for norm in varianten:
                row = self.conn.execute(
                    """SELECT g.admin1 FROM geo_alt_name a
                       JOIN geofeature g ON a.geoname_id = g.geoname_id
                       WHERE g.feature_code=? AND g.country_code=? AND a.alt_name_norm=?
                         AND g.admin1 IS NOT NULL AND g.admin1 != ''
                       LIMIT 1""",
                    (code, country_code, norm),
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
                    return adm[0], False

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
                            return adm_s[0], False
                if candidates:
                    break  # found via first matching part

        if not candidates:
            return None, False

        # Sort: region hint first, then feature-code priority, then population
        candidates.sort(key=lambda r: (
            0 if (not country_code or r["country_code"] == country_code) else 1,
            0 if (admin1_hint and r["admin1"] == admin1_hint) else 1,
            PPL_PRIORITY.get(r["feature_code"], 99),
            -(r["population"] or 0),
        ))

        # Mehrdeutig: ein weiterer bewohnter Ort gleichen Namens liegt weit weg.
        # Im selben Kanton zaehlen schon 5 km (Au ZH gibt es neunmal), sonst
        # 30 km.  Bis 2026-10-25 wurde nur der Zweitplatzierte verglichen und
        # nur ab 30 km — Au am Zuerichsee gegen Au bei Kloten (20 km) galt
        # als eindeutig.
        best = candidates[0]
        is_ambiguous = any(
            (c["population"] or 0) >= 100
            and _dist_km(best["lat"], best["lon"], c["lat"], c["lon"])
                > (5.0 if (c["country_code"], c["admin1"]) == (best["country_code"], best["admin1"]) else 30.0)
            for c in candidates[1:]
        )

        return candidates[0], is_ambiguous

    def lage(self, lat: float, lon: float, max_km: float = 20.0) -> "set | None":
        """{(Land, Kanton), …} der naechsten Orte — wo ein Punkt liegen kann.

        Fuer Treffer, deren Quelle Land und Kanton nicht mitliefert (Wikidata,
        Nominatim, TGN).  Rechteck und Kantonskreis sind dafuer zu grob: der
        Ortler (Italien) liegt in beiden — im CH-Rechteck und 72 km vom
        Buendner Mittelpunkt; seine 5 naechsten Orte sind alle italienisch.
        Eine MENGE, keine Mehrheit: beim Basodino (Grenzgipfel TI/IT) sind
        die meisten Nachbardoerfer italienisch, und der Vierwaldstaettersee
        gehoert mehreren Kantonen — beide sollen durchgehen.
        None, wenn im Umkreis kein Ort liegt.
        """
        key = (round(lat, 3), round(lon, 3))
        if not hasattr(self, "_lage_cache"):
            self._lage_cache: dict = {}
        if key in self._lage_cache:
            return self._lage_cache[key]
        d = max_km / 111.0
        dlon = d / max(math.cos(math.radians(lat)), 0.01)
        orte = []
        for la, lo, cc, a1 in self.conn.execute(
                "SELECT lat, lon, country_code, admin1 FROM geofeature "
                "WHERE feature_class='P' AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
                "AND feature_code IN ('PPL','PPLA','PPLA2','PPLA3','PPLA4','PPLC')",
                (lat - d, lat + d, lon - dlon, lon + dlon)):
            dist = math.hypot(la - lat, (lo - lon) * math.cos(math.radians(lat))) * 111.0
            if dist <= max_km:
                orte.append((dist, cc, a1))
        orte.sort()
        if not orte:
            self._lage_cache[key] = None
            return None
        # Die 5 naechsten UND alle bis zur doppelten Entfernung des naechsten:
        # mitten im Genfersee sind die 5 naechsten Orte franzoesisch, das
        # Schweizer Ufer liegt nur wenig weiter.  Der Ortler liegt 4 km neben
        # Sulden — doppelte Entfernung bleibt in Italien.
        grenze = 2 * orte[0][0]
        menge = {(o[1], o[2]) for k, o in enumerate(orte) if k < 5 or o[0] <= grenze}
        self._lage_cache[key] = menge
        return menge

    def gemeinden_bei(self, lat: float, lon: float, n: int = 5,
                      max_km: float = 10.0) -> "set[tuple]":
        """{(Land, Kanton, Gemeinde), …} der n naechsten Orte.

        Fuer die Frage „liegt dieses Gebaeude in der genannten Gemeinde?"
        (geostack._ausserhalb_gemeinde).  Mehrere Orte statt einem, weil der
        naechste Ort eines Gebaeudes am Gemeinderand schon im Nachbarort
        liegen kann — und weil GeoNames die Gemeindenummer nicht immer
        richtig fuehrt (Weiler bei Kloster Wettingen tragen die Nummer von
        Neuenhof).  Mit Stadtteilen (PPLX): sonst fand die Klinik Hirslanden
        nur Zollikon und galt als ausserhalb von Zuerich.
        """
        d = max_km / 111.0
        dlon = d / max(math.cos(math.radians(lat)), 0.01)
        orte = []
        for row in self.conn.execute(
                "SELECT lat, lon, country_code, admin1, admin2, admin3, admin4 FROM geofeature "
                "WHERE feature_class='P' AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
                "AND feature_code IN ('PPL','PPLA','PPLA2','PPLA3','PPLA4','PPLC','PPLL','PPLX')",
                (lat - d, lat + d, lon - dlon, lon + dlon)):
            g = gemeinde_von(row)
            if g:
                orte.append((math.hypot(row["lat"] - lat, (row["lon"] - lon) * math.cos(math.radians(lat))) * 111.0, g))
        orte.sort()
        return {g for _, g in orte[:n]}

    def ort_der_region_bei(self, lat: float, lon: float, country_code: str, admin1: str,
                           max_km: float = 25.0) -> bool:
        """Liegt ein bewohnter Ort dieser Region hoechstens max_km vom Punkt?

        Fuer Punkte, deren naechste Orte in einer anderen Region liegen
        (geostack._in_region): ein See oder Grenzberg hat immer einen Ufer-
        oder Talort der Region in der Naehe, ein Treffer in der falschen
        Region nicht.
        """
        d = max_km / 111.0
        dlon = d / max(math.cos(math.radians(lat)), 0.01)
        for la, lo in self.conn.execute(
                "SELECT lat, lon FROM geofeature WHERE feature_class='P' "
                "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND country_code=? AND admin1=?",
                (lat - d, lat + d, lon - dlon, lon + dlon, country_code, admin1)):
            if math.hypot(la - lat, (lo - lon) * math.cos(math.radians(lat))) * 111.0 <= max_km:
                return True
        return False

    def naechster_ort(self, lat: float, lon: float, country_code: "str | None" = None,
                      admin1: "str | None" = None, max_km: float = 10.0) -> "str | None":
        """Gemeinde des naechsten Orts — fuer eine fehlende Stadt.

        Der naechste Ort allein waere oft ein Weiler („Schloss Chillon" →
        Grandchamp, „Flüelastrasse" → Bünda).  Deshalb ueber dessen `admin3`
        die Gemeinde (ADM3: Veytaux, Davos); fehlt sie, der Ort selbst.
        Ueber den Klassen-/Koordinatenindex `idx_geofeature_class_latlon`.
        """
        d = max_km / 111.0
        dlon = d / max(math.cos(math.radians(lat)), 0.01)
        sql = ("SELECT name, lat, lon, country_code, admin1, admin3 FROM geofeature "
               "WHERE feature_class='P' AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
               "AND feature_code IN ('PPL','PPLA','PPLA2','PPLA3','PPLA4','PPLC')")
        args = [lat - d, lat + d, lon - dlon, lon + dlon]
        if country_code:
            sql += " AND country_code=?"; args.append(country_code)
        if admin1:
            sql += " AND admin1=?"; args.append(admin1)
        beste = None
        for name, la, lo, cc, a1, a3 in self.conn.execute(sql, args):
            dist = math.hypot(la - lat, (lo - lon) * math.cos(math.radians(lat))) * 111.0
            if dist <= max_km and (beste is None or dist < beste[0]):
                beste = (dist, name, cc, a1, a3)
        if not beste:
            return None
        _, name, cc, a1, a3 = beste
        # Die Gemeinde liegt nicht in jedem Land auf derselben Ebene: in
        # Frankreich ist ADM3 das Arrondissement, die Commune erst ADM4.
        # Wo die Ebene nicht sicher ist, bleibt es beim Ortsnamen.
        if a3 and cc in _GEMEINDE_IST_ADM3:
            g = self.conn.execute(
                "SELECT name FROM geofeature WHERE feature_code='ADM3' "
                "AND country_code=? AND admin1=? AND admin3=? LIMIT 1", (cc, a1, a3)).fetchone()
            if g:
                return g[0]
        return name

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
