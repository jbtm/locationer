import math
import re
from typing import Optional

from .cache import Cache
from .country_db import CountryDB
from .explicit_store import ExplicitStore
from .geo_db import GeoDatabase
from .models import GeoResult, NormalizedRecord
from .nominatim import Nominatim

# (min_lat, max_lat, min_lon, max_lon)
# Tolerance: 0.2° for small/medium countries (~22 km), 0.5° for large ones.
# Role: secondary sanity check — the country-code field of GeoNames results is
# the primary guard; this bbox catches Nominatim results and TGN which don't
# carry a reliable country_code.
_COUNTRY_BBOX: dict[str, tuple[float, float, float, float]] = {
    "CH": (45.62, 48.01,  5.76, 10.69),  # actual 45.82–47.81 / 5.96–10.49 + 0.2°
    "LI": (46.85, 47.47,  9.28,  9.84),  # actual 47.05–47.27 / 9.48–9.64 + 0.2°
    "AT": (46.18, 49.22,  9.33, 17.36),  # actual 46.38–49.02 / 9.53–17.16 + 0.2°
    "DE": (46.87, 55.26,  5.67, 15.23),  # actual 47.27–55.06 / 5.87–15.03 + 0.2°
    "FR": (41.13, 51.30, -5.25,  9.76),  # actual 41.33–51.10 / -5.05–9.56 + 0.2°
    "IT": (35.29, 47.12,  6.45, 18.72),  # actual 35.49–46.92 / 6.65–18.52 + 0.2°
    "ES": (27.43, 43.99,-18.39,  4.58),  # + 0.2°
    "PT": (32.43, 42.19,-31.46, -6.19),  # + 0.2°
    "GB": (49.67, 60.86, -8.82,  1.97),  # + 0.2°
    "IE": (51.22, 55.73,-10.67, -5.73),  # + 0.2°
    "NL": (50.55, 53.72,  3.13,  7.39),  # + 0.2°
    "BE": (49.30, 51.65,  2.33,  6.51),  # + 0.2°
    "LU": (49.34, 50.38,  5.54,  6.63),  # + 0.2°
    "DK": (54.34, 58.06,  7.72, 15.45),  # + 0.2°
    "SE": (55.13, 69.26, 10.63, 24.56),  # + 0.3°
    "NO": (57.62, 71.62,  4.08, 31.46),  # + 0.3°
    "FI": (59.45, 70.55, 19.51, 32.06),  # + 0.3°
    "PL": (48.65, 55.15, 13.75, 24.45),  # + 0.3°
    "CZ": (48.33, 51.26, 11.74, 19.16),  # + 0.3°
    "SK": (47.43, 50.17, 16.53, 23.33),  # + 0.3°
    "HU": (45.37, 49.17, 15.83, 23.36),  # + 0.3°
    "RO": (42.93, 48.85, 19.93, 30.57),  # + 0.3°
    "HR": (42.14, 47.08, 13.23, 20.07),  # + 0.3°
    "SI": (45.21, 47.18, 13.23, 16.92),  # + 0.3°
    "GR": (34.52, 42.34, 19.17, 29.94),  # + 0.5°
    "TR": (35.48, 42.57, 25.47, 45.48),  # + 0.5°
    "RU": (40.2,  83.0,  18.6, 180.0),   # large country — keep generous
    "US": (17.9,  72.4,-180.0, -65.9),   # large
    "CA": (40.7,  84.1,-142.0, -51.6),   # large
    "AU": (-44.7, -9.7, 112.2, 154.7),   # large
    "JP": (23.0,  46.6, 121.9, 154.0),   # + 0.5°
    "CN": (17.2,  54.6,  72.5, 135.8),   # large
    "IN": ( 7.0,  38.1,  67.2,  98.4),   # large
    "ZA": (-35.9,-21.1,  15.5,  33.9),   # + 0.5°
    "MX": (13.5,  33.7,-119.5, -85.7),   # large
    "BR": (-34.8,  6.3, -74.9, -33.8),   # large
    "AR": (-56.1,-20.8, -74.6, -52.6),   # large
    "MA": (27.6,  36.0, -14.0,   2.0),   # + 0.5°
    "EG": (21.9,  32.0,  24.7,  37.0),   # + 0.5°
    "ZZ": (-90.0, 90.0,-180.0, 180.0),   # unknown country — never reject
}


def _within_country_bbox(lat: float, lon: float, country_code: str) -> bool:
    bbox = _COUNTRY_BBOX.get(country_code)
    if bbox is None:
        return True  # unknown country → don't reject
    min_lat, max_lat, min_lon, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def _within_collection_bbox(lat: float, lon: float,
                             bbox: "tuple[float,float,float,float] | None") -> bool:
    if bbox is None:
        return True
    min_lat, max_lat, min_lon, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


# Geographic feature keywords (German/French/Italian/English/Romansh).
# Presence in record.location triggers an expanded proximity radius,
# matching the v1 logic: named natural features (gorges, passes, peaks,
# glaciers, …) can legitimately lie further from a city than buildings do.
_GEO_FEATURE_TOKENS = {
    # Passes / cols
    "pass", "passo", "joch", "col", "sattel", "forcella",
    # Mountains / peaks
    "berg", "horn", "stock", "gipfel", "spitze", "pic", "piz", "pizzo",
    "monte", "mount", "peak", "summit", "kulm", "haupt", "kopf",
    # Ridges / walls
    "grat", "arête", "wand", "flue", "flüe", "egg",
    # Gorges / canyons
    "schlucht", "tobel", "klamm", "klus", "gorge", "canyon", "ravine",
    "gola", "forra",
    # Valleys
    "tal", "val", "valle", "vallée", "valley",
    # Lakes
    "see", "lac", "lago", "lake",
    # Glaciers / snowfields
    "gletscher", "glacier", "ghiacciaio", "firn", "nevado",
    # Rivers / streams
    "bach", "fluss", "rivière", "torrent", "rio",
    # Cliffs / rocks
    "fels", "felsen", "stein", "burg", "ruin", "ruine",
    # Alpine meadows / alp features
    "alp", "alpe", "alpage",
    # Wetlands / moorland
    "moos", "ried", "moor", "sumpf",
    # Fjords / other
    "fjord", "fjell",
}
# Substrings checked for long keywords (≥5 chars) — catches compounds
# like "Viamala-Schlucht", "Taminaschlucht", "Rheinschlucht".
# 4-letter suffixes added selectively: "pass" (Julierpass), "joch" (Grimjoch),
# "horn" (Matterhorn) — "berg" excluded (too many city-name false positives).
_GEO_FEATURE_SUBSTRINGS = (
    {k for k in _GEO_FEATURE_TOKENS if len(k) >= 5}
    | {"pass", "joch", "horn"}
)


def _is_geo_feature(location: str) -> bool:
    """True if location looks like a natural/geographic feature rather than a building."""
    if not location:
        return False
    import re
    tokens = set(re.findall(r"\w+", location.lower()))
    if tokens & _GEO_FEATURE_TOKENS:
        return True
    loc_lower = location.lower()
    return any(sub in loc_lower for sub in _GEO_FEATURE_SUBSTRINGS)


def _city_radius_km(city_row) -> float:
    """Dynamic proximity radius based on city population.

    Small village (Flerden ~100):  2 km  — must be right there
    Large town  (Chur   ~36k):     6 km
    Major city  (Zürich ~400k):   15 km  — rejects Zug (22.7 km)
    Metropolis  (Tokyo  ~13M):    25 km
    """
    if city_row is None:
        return 5.0
    pop = city_row["population"] or 0
    if pop > 500_000: return 25.0
    if pop > 100_000: return 15.0
    if pop >  20_000: return  8.0
    if pop >   5_000: return  5.0
    if pop >   1_000: return  3.0
    return 2.0
# Wikidata disabled: public SPARQL endpoint (Blazegraph) times out on CONTAINS()
# queries because it lacks efficient full-text indexes → sequential scan over
# millions of labels. Re-enable if a local Wikidata instance is available.
# from .wikidata import Wikidata

# Generic title words that indicate no specific named location — skip external calls,
# go straight to city fallback. Saves ~25% of Wikidata/Nominatim calls for ZIN-style data.
_GENERIC_WORDS = {
    "ortsteilansicht", "gesamtansicht", "ortsgesamtansicht", "teilansicht",
    "gesamtaussenansicht", "teilaussenansicht", "gesamtinnenansicht", "teilinnenansicht",
    "dorfteilansicht", "ortsansicht", "dorfansicht", "stadtansicht", "dorfbild",
    "ansicht", "aussenansicht", "innenansicht", "gesamtbild", "ortsgesamtbild", "ortsbild",
    "landschaft", "panorama", "berglandschaft", "seelandschaft", "winterlandschaft",
    "herbstlandschaft", "gebirgslandschaft",
    "portrat", "portrait",
    "mannliche", "weibliche", "mannlicher", "weiblicher",
    "person", "personen", "frau", "frauen", "mann", "manner",
    "kind", "kinder", "knabe", "madchen", "man", "woman", "child", "people",
    "verschneit", "verschneite", "sommerlich", "winterlich",
    "und", "im", "mit", "vom", "von", "am", "an", "bei", "zur", "zum",
    "der", "die", "das", "in",
}


def _is_generic_title(title: str, city: str) -> bool:
    import re
    from .geo_db import ascii_norm
    norm = ascii_norm(title)
    if city:
        norm = norm.replace(ascii_norm(city), " ")
    words = set(re.findall(r"\w+", norm)) - {""}
    return bool(words) and words.issubset(_GENERIC_WORDS)


def _dist_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


class GeoStack:
    def __init__(
        self,
        geo_db: Optional[GeoDatabase],
        cache: Cache,
        nominatim: Nominatim,
        debug: bool = False,
        overrides: Optional[ExplicitStore] = None,
        tgn_db=None,
        collection_bbox: "tuple[float,float,float,float] | None" = None,
        country_dbs: "dict[str, CountryDB] | None" = None,
    ):
        self.geo_db          = geo_db
        self.cache           = cache
        self.nominatim       = nominatim
        self.tgn_db          = tgn_db
        self.debug           = debug
        self.overrides       = overrides
        self.collection_bbox = collection_bbox
        self.country_dbs     = country_dbs or {}
        self.ext_count = 0  # Nominatim calls

    def geocode(self, record: NormalizedRecord) -> GeoResult:
        # Strip canton/region prefixes that may survive in old norm-cache hits
        # (e.g. "Kanton Luzern" → "Luzern"). _normalize_city handles new records;
        # this ensures stale cached records are treated correctly too.
        record.city = re.sub(r"^(?:Kanton|Kt\.?|Canton)\s+", "", record.city or "",
                              flags=re.IGNORECASE).strip()
        hint = record.location or record.title
        cache_key = f"{hint}|{record.city}|{record.country}"

        # ── Step 0: manual overrides ─────────────────────────────────────────
        if self.overrides:
            ov = self.overrides.match(record.location, record.city, record.country)
            if ov:
                ov.debug_info = [f"override: {record.location!r}|{record.city!r}|{record.country!r}"]
                if self.debug:
                    print(f"  [override] {ov.match_name}")
                return ov

        # ── Step 1: geo_cache ─────────────────────────────────────────────────
        cached = self.cache.get_geo(cache_key)
        if cached and cached.lat is not None:
            cc = self.geo_db.country_to_code(record.country) if self.geo_db else None
            # Bbox check
            bbox_ok = (
                (_within_country_bbox(cached.lat, cached.lon, cc) if cc else True)
                and (cc or _within_collection_bbox(cached.lat, cached.lon, self.collection_bbox))
            )
            # Re-geocode a cached city fallback when a specific location is known —
            # gives Nominatim/GeoNames a chance to find the precise result.
            # Exception: skip re-geocode if the input explicitly marks location as unknown
            # (those entries produce Score 0 by design and should stay cached).
            if bbox_ok and cached.fallback and record.location:
                bbox_ok = False  # force re-geocode
            # Proximity check for all Nominatim results (precise and fallback).
            # Catches stale entries where Nominatim returned a canton/region centroid
            # instead of the actual city centre (e.g. "Kanton Luzern" → Hinterland).
            prox_ok = True
            if bbox_ok and cached.source == "nominatim" and self.geo_db:
                lookup = record.city or cached.match_name
                city_row_c = self.geo_db.find_city(lookup, cc) if lookup else None
                if city_row_c:
                    dist = _dist_km(cached.lat, cached.lon, city_row_c["lat"], city_row_c["lon"])
                    prox_ok = dist <= _city_radius_km(city_row_c)
            if bbox_ok and prox_ok:
                cached.debug_info = [f"cache hit: {cached.match_name or '—'}"]
                return cached
            # stale — fall through to re-geocode
        elif cached and cached.lat is None:
            # Score-0 cache hit: only trust it when there's no city anchor.
            # If we have a city, re-geocode — the city fallback (Score 3) is always possible.
            if not record.city:
                cached.debug_info = ["cache hit: —"]
                return cached
            # else: fall through and re-geocode → will return at least Score 3

        country_code = self.geo_db.country_to_code(record.country) if self.geo_db else None
        dbg: list[str] = [f"country_code={country_code}"]

        # Resolve region name → admin1 code for city disambiguation
        admin1_hint = None
        if self.geo_db and country_code and record.region:
            admin1_hint = self.geo_db.region_to_admin1(country_code, record.region)
            if admin1_hint:
                dbg.append(f"region={record.region!r} → admin1={admin1_hint}")

        # ── Step 2: GEO DB city — for proximity ranking ───────────────────────
        # When country is unknown, use collection bbox centre as a geographic prior
        # for GeoNames lookups — "Matterhorn" near Europe wins over Matterhorn in NZ.
        collection_center = None
        if country_code is None and self.collection_bbox:
            mn_lat, mx_lat, mn_lon, mx_lon = self.collection_bbox
            collection_center = ((mn_lat + mx_lat) / 2, (mn_lon + mx_lon) / 2)

        city_row = self.geo_db.find_city(record.city, country_code, admin1_hint) if (self.geo_db and record.city) else None
        near = (city_row["lat"], city_row["lon"]) if city_row else None
        city_radius = _city_radius_km(city_row)  # dynamic threshold based on city size
        geo_feature = _is_geo_feature(record.location)  # gorges/passes/peaks need bigger radius

        # If city not found as populated place, try as geographic feature (mountain,
        # lake, pass, etc.) — gives proximity anchor for cases like "Pilatus", "Rigi"
        geo_feat_city = None
        if not near and self.geo_db and record.city:
            # Use collection_center as proximity hint so globally ambiguous names
            # (e.g. "Matterhorn") resolve to the instance nearest the collection area.
            city_near_hint = collection_center
            geo_feat = self.geo_db.find_precise(record.city, country_code, near=city_near_hint, admin1_hint=admin1_hint)
            if geo_feat:
                near = (geo_feat["lat"], geo_feat["lon"])
                geo_feat_city = geo_feat  # keep for Score 3 fallback
                dbg.append(f"city as geo feature: {geo_feat['name']} [{geo_feat['feature_code']}]")

        # If Haiku didn't extract a country but GeoNames found the city anchor,
        # infer country_code — but only if the anchor is within the collection bbox.
        if country_code is None:
            anchor = city_row or geo_feat_city
            if anchor and anchor["country_code"]:
                if _within_collection_bbox(anchor["lat"], anchor["lon"], self.collection_bbox):
                    country_code = anchor["country_code"]
                    dbg.append(f"country_code inferred from city: {country_code}")
                else:
                    city_row = None
                    geo_feat_city = None
                    near = None
                    dbg.append(f"city anchor {anchor['name']!r} discarded (outside collection bbox)")

        # admin1 centroid — computed early so it can validate the Nominatim city anchor.
        admin1_near = None
        if admin1_hint and self.geo_db:
            admin1_near = self.geo_db.find_admin1_centroid(country_code, admin1_hint)

        # City has top priority: if GeoNames doesn't know the city at all,
        # ask Nominatim early so we have a reliable anchor for all subsequent
        # proximity checks AND a guaranteed Score 2 fallback at the end.
        nm_city_result = None
        if near is None and record.city:
            city_q = " ".join(filter(None, [record.city, record.country]))
            nm_city = self.nominatim.search(city_q)
            if nm_city:
                # Reject administrative boundaries — they're canton/region centroids,
                # not city centres (e.g. "Kanton Luzern" → Hinterland polygon centroid).
                if nm_city.get("place_type", "").lower() == "administrative":
                    dbg.append(f"Nominatim city anchor rejected (administrative boundary): {nm_city['name']}")
                else:
                    self.ext_count += 1
                    near = (nm_city["lat"], nm_city["lon"])
                    nm_city_result = nm_city
                    dbg.append(f"city anchor via Nominatim: {nm_city['name']}")
                    # Hard country check
                    if country_code and not _within_country_bbox(near[0], near[1], country_code):
                        dbg.append(f"Nominatim city anchor rejected (outside {country_code} bbox)")
                        near = None
                        nm_city_result = None
                    # Hard region check: reject if outside expected admin1 radius
                    elif admin1_near and admin1_hint and self.geo_db:
                        dist_a1 = _dist_km(near[0], near[1], admin1_near[0], admin1_near[1])
                        a1_radius = self.geo_db.find_admin1_radius_km(country_code, admin1_hint, admin1_near)
                        if dist_a1 > a1_radius:
                            dbg.append(
                                f"Nominatim city anchor rejected (outside {admin1_hint}: "
                                f"{dist_a1:.0f} km > {a1_radius:.0f} km)"
                            )
                            near = None
                            nm_city_result = None

        if admin1_near and near is None:
            dbg.append(f"admin1 centroid anchor: {admin1_near[0]:.3f}/{admin1_near[1]:.3f} ({admin1_hint})")

        # ── Step 3: GEO DB precise ────────────────────────────────────────────
        # For non-geographic features (buildings, named places within a town),
        # prefer candidates in the same municipality as the city anchor.
        # Geo-features (peaks, glaciers, passes) can legitimately lie outside
        # the city's commune and are not constrained.
        prefer_admin3 = (
            city_row["admin3"]
            if (city_row and not geo_feature and city_row["admin3"])
            else None
        )
        if record.location and self.geo_db:
            row = self.geo_db.find_precise(record.location, country_code, near=near,
                                           admin1_hint=admin1_hint, prefer_admin3=prefer_admin3)
            if row:
                # Primary check: result must be from the expected country.
                # This catches world-wide GeoNames expansions that land in the wrong country
                # (e.g. "Schirmhütte" CH → result country_code=DE → reject).
                if country_code and row["country_code"] and row["country_code"] != country_code:
                    dbg.append(f"GEO DB precise {row['name']!r} rejected (country {row['country_code']} ≠ {country_code})")
                    row = None
                dist = _dist_km(row["lat"], row["lon"], near[0], near[1]) if (near and row) else 0
                geo_radius = max(city_radius * (6 if geo_feature else 2), 15 if geo_feature else 5)
                if row and near and dist > geo_radius:
                    dbg.append(f"GEO DB precise {row['name']!r} rejected ({dist:.0f} km > {geo_radius:.0f} km)")
                elif row and country_code and not _within_country_bbox(row["lat"], row["lon"], country_code):
                    dbg.append(f"GEO DB precise {row['name']!r} rejected (outside {country_code} bbox)")
                elif row and not country_code and not _within_collection_bbox(row["lat"], row["lon"], self.collection_bbox):
                    dbg.append(f"GEO DB precise {row['name']!r} rejected (outside collection bbox)")
                elif row:
                    result = GeoResult(
                        lat=row["lat"], lon=row["lon"], quality_score=5,
                        fallback=False, source="geo_db", match_name=row["name"],
                    )
                    dbg.append(f"precise GEO DB: {row['name']} [{row['feature_class']}.{row['feature_code']}]")
                    return self._store(cache_key, result, dbg)
            else:
                dbg.append(f"GEO DB precise miss: {record.location!r}")

        # ── Step 2.5: TGN (local, no network — set by Phase 1c) ─────────────
        if record.tgn_id and self.tgn_db:
            tgn_row = self.tgn_db.get_by_id(record.tgn_id)
            if tgn_row and tgn_row["lat"] is not None:
                tgn_lat, tgn_lon = tgn_row["lat"], tgn_row["lon"]
                tgn_ok = True
                if near:
                    dist = _dist_km(tgn_lat, tgn_lon, near[0], near[1])
                    tgn_radius = max(city_radius * (6 if geo_feature else 4), 20 if not geo_feature else 15)
                    if dist > tgn_radius:
                        dbg.append(f"TGN {tgn_row['pref_name']!r} rejected ({dist:.0f} km > {tgn_radius:.0f} km)")
                        tgn_ok = False
                if tgn_ok and country_code and not _within_country_bbox(tgn_lat, tgn_lon, country_code):
                    dbg.append(f"TGN {tgn_row['pref_name']!r} rejected (outside {country_code} bbox)")
                    tgn_ok = False
                if tgn_ok and not country_code and not _within_collection_bbox(tgn_lat, tgn_lon, self.collection_bbox):
                    dbg.append(f"TGN {tgn_row['pref_name']!r} rejected (outside collection bbox)")
                    tgn_ok = False
                if tgn_ok:
                    result = GeoResult(
                        lat=tgn_lat, lon=tgn_lon, quality_score=5,
                        fallback=False, source="tgn",
                        match_name=tgn_row["pref_name"] or record.tgn_name,
                    )
                    dbg.append(f"TGN: {tgn_row['pref_name']} [{tgn_row['place_type']}] id={record.tgn_id}")
                    return self._store(cache_key, result, dbg)
            else:
                dbg.append(f"TGN miss for id={record.tgn_id}")

        # ── Step 2.7: Country DB precise ─────────────────────────────────────
        cdb = self.country_dbs.get(country_code) if country_code else None
        if cdb and record.location:
            crow = cdb.find_precise(record.location, country_code,
                                    near=near, radius_km=max(city_radius * 2, 5),
                                    admin1_hint=admin1_hint)
            if crow:
                result = GeoResult(
                    lat=crow["lat"], lon=crow["lon"], quality_score=5,
                    fallback=False, source="country_db",
                    match_name=crow["canonical_name"],
                )
                dbg.append(f"country_db precise: {crow['canonical_name']}")
                return self._store(cache_key, result, dbg)
            else:
                dbg.append(f"country_db precise miss: {record.location!r}")

        # ── Step 3: Nominatim ─────────────────────────────────────────────────
        # Only call Nominatim if we have at least a city or a specific location —
        # prevents nonsense results for records like "Hotel" with no geographic anchor.
        has_anchor = bool(record.city or record.location)
        skip_ext = not has_anchor or (
            not record.location
            and bool(record.city)
            and _is_generic_title(record.title, record.city)
        )

        ext_result = None
        if not skip_ext:
            query = " ".join(filter(None, [record.location or record.title,
                                           record.city, record.country]))

            # When no city anchor but region is known, include region in the query
            # so Nominatim disambiguates geographically (e.g. "Flüelastrasse Graubünden"
            # instead of "Flüelastrasse" which matches a street in Zürich).
            if record.location and not near and record.region:
                nm_query = " ".join(filter(None, [record.location, record.region, record.country]))
            elif record.location:
                nm_query = record.location
            else:
                nm_query = query
            dbg.append(f"Nominatim query: {nm_query!r}")
            nm = self.nominatim.search(nm_query)
            if nm is None and record.location and (record.city or record.country):
                dbg.append(f"Nominatim retry: {query!r}")
                nm = self.nominatim.search(query)
            if self.nominatim._last_error:
                dbg.append(f"Nominatim error: {self.nominatim._last_error}")
                self.nominatim._last_error = ""
            if nm:
                self.ext_count += 1
                # Proximity check: dynamic city radius (expanded for geo features) or admin1 centroid.
                effective_near = near or admin1_near
                if near:
                    threshold = max(city_radius * 4, 15) if geo_feature else city_radius
                else:
                    threshold = 100
                if nm.get("precise") and effective_near and record.location:
                    dist = _dist_km(nm["lat"], nm["lon"], effective_near[0], effective_near[1])
                    if dist > threshold:
                        anchor_label = "city" if near else f"{admin1_hint} centroid"
                        dbg.append(f"Nominatim precise hit {nm['name']!r} rejected ({dist:.0f} km from {anchor_label})")
                        nm2 = self.nominatim.search(query)
                        if nm2 and nm2.get("precise"):
                            dist2 = _dist_km(nm2["lat"], nm2["lon"], effective_near[0], effective_near[1])
                            if dist2 > threshold:
                                dbg.append(f"Nominatim retry {nm2['name']!r} also rejected ({dist2:.0f} km)")
                                nm2 = None
                        nm = nm2
                if nm:
                    ext_result = {**nm, "source": "nominatim"}
                    dbg.append(f"Nominatim hit: {nm['name']} (precise={nm['precise']})")
                else:
                    dbg.append("Nominatim: rejected (proximity), retry failed")
            else:
                dbg.append("Nominatim: no result")
        else:
            dbg.append(f"external skip: generic title {record.title!r}")

        if ext_result and ext_result.get("precise"):
            if country_code and not _within_country_bbox(ext_result["lat"], ext_result["lon"], country_code):
                dbg.append(f"Nominatim precise {ext_result['name']!r} rejected (outside {country_code} bbox)")
                ext_result = None
            elif not country_code and not _within_collection_bbox(ext_result["lat"], ext_result["lon"], self.collection_bbox):
                dbg.append(f"Nominatim precise {ext_result['name']!r} rejected (outside collection bbox)")
                ext_result = None
            elif admin1_near and admin1_hint and country_code and self.geo_db:
                dist_a1 = _dist_km(ext_result["lat"], ext_result["lon"], admin1_near[0], admin1_near[1])
                a1_radius = self.geo_db.find_admin1_radius_km(country_code, admin1_hint, admin1_near)
                if dist_a1 > a1_radius:
                    dbg.append(
                        f"Nominatim precise {ext_result['name']!r} rejected "
                        f"(region dist {dist_a1:.0f} km > {a1_radius:.0f} km)"
                    )
                    ext_result = None
            if ext_result:
                result = GeoResult(
                    lat=ext_result["lat"], lon=ext_result["lon"], quality_score=4,
                    fallback=False, source=ext_result["source"],
                    match_name=ext_result["name"],
                )
                return self._store(cache_key, result, dbg)

        if ext_result:
            dbg.append(f"external not precise: {ext_result['name']}")

        # ── Step 3.5: geocoding_name — letzter Präzisionsversuch ─────────────
        # Haiku extrahiert in Phase 1a den offiziellen lokalen Namen (z.B.
        # "Sprungschanze Vikersund" → "Hoppebakken"). Hier nochmals GEO DB,
        # Country DB und Nominatim mit diesem Namen versuchen.
        gn = record.geocoding_name
        if gn and gn != record.location:
            dbg.append(f"geocoding_name: {gn!r}")
            # GEO DB precise
            if self.geo_db:
                row_gn = self.geo_db.find_precise(gn, country_code, near=near,
                                                  admin1_hint=admin1_hint)
                if row_gn:
                    dist_gn = _dist_km(row_gn["lat"], row_gn["lon"], near[0], near[1]) if near else 0
                    gn_radius = max(city_radius * 2, 5)
                    if near and dist_gn > gn_radius:
                        dbg.append(f"geocoding_name GEO DB {row_gn['name']!r} rejected ({dist_gn:.0f} km)")
                        row_gn = None
                if row_gn and country_code and not _within_country_bbox(row_gn["lat"], row_gn["lon"], country_code):
                    dbg.append(f"geocoding_name GEO DB {row_gn['name']!r} rejected (outside bbox)")
                    row_gn = None
                if row_gn:
                    result = GeoResult(lat=row_gn["lat"], lon=row_gn["lon"], quality_score=5,
                                       fallback=False, source="geo_db", match_name=row_gn["name"])
                    dbg.append(f"geocoding_name → GEO DB: {row_gn['name']}")
                    return self._store(cache_key, result, dbg)
            # Country DB precise
            if cdb:
                crow_gn = cdb.find_precise(gn, country_code, near=near,
                                           radius_km=max(city_radius * 2, 5))
                if crow_gn:
                    result = GeoResult(lat=crow_gn["lat"], lon=crow_gn["lon"], quality_score=5,
                                       fallback=False, source="country_db",
                                       match_name=crow_gn["canonical_name"])
                    dbg.append(f"geocoding_name → country_db: {crow_gn['canonical_name']}")
                    return self._store(cache_key, result, dbg)
            # Nominatim mit geocoding_name
            nm_gn_q = " ".join(filter(None, [gn, record.city, record.country]))
            nm_gn = self.nominatim.search(nm_gn_q)
            if nm_gn and nm_gn.get("precise"):
                self.ext_count += 1
                if not country_code or _within_country_bbox(nm_gn["lat"], nm_gn["lon"], country_code):
                    result = GeoResult(lat=nm_gn["lat"], lon=nm_gn["lon"], quality_score=4,
                                       fallback=False, source="nominatim",
                                       match_name=nm_gn["name"])
                    dbg.append(f"geocoding_name → Nominatim: {nm_gn['name']}")
                    return self._store(cache_key, result, dbg)
            dbg.append(f"geocoding_name: all miss for {gn!r}")

        # ── Step 5: GEO DB city fallback ──────────────────────────────────────
        # Use city_row (PPL) or geo_feat_city (feature found via find_precise) as fallback.
        if not city_row and geo_feat_city:
            city_row = geo_feat_city
        if city_row:
            if country_code and not _within_country_bbox(city_row["lat"], city_row["lon"], country_code):
                dbg.append(f"city fallback {city_row['name']!r} rejected (outside {country_code} bbox)")
            elif not country_code and not _within_collection_bbox(city_row["lat"], city_row["lon"], self.collection_bbox):
                dbg.append(f"city fallback {city_row['name']!r} rejected (outside collection bbox)")
            else:
                result = GeoResult(
                    lat=city_row["lat"], lon=city_row["lon"], quality_score=3,
                    fallback=True, source="geo_db", match_name=city_row["name"],
                )
                dbg.append(f"city GEO DB: {city_row['name']} [{city_row['feature_code']}]")
                return self._store(cache_key, result, dbg)

        # ── Step 4.5: Country DB city fallback ───────────────────────────────
        if cdb and record.city and not city_row:
            ccrow = cdb.find_city(record.city, country_code, admin1_hint=admin1_hint)
            if ccrow:
                result = GeoResult(
                    lat=ccrow["lat"], lon=ccrow["lon"], quality_score=3,
                    fallback=True, source="country_db",
                    match_name=ccrow["canonical_name"],
                )
                dbg.append(f"country_db city: {ccrow['canonical_name']}")
                return self._store(cache_key, result, dbg)

        # ── Step 6: Nominatim city-only ───────────────────────────────────────
        # Use the early Nominatim city anchor (already fetched above) if available,
        # otherwise try a non-precise ext_result from the location search.
        city_fallback = nm_city_result or (ext_result if ext_result and not ext_result.get("precise") else None)
        if city_fallback:
            result = GeoResult(
                lat=city_fallback["lat"], lon=city_fallback["lon"], quality_score=2,
                fallback=True, source="nominatim",
                match_name=city_fallback["name"],
            )
            dbg.append(f"city fallback via Nominatim: {city_fallback['name']}")
            return self._store(cache_key, result, dbg)

        dbg.append("not found")
        return self._store(cache_key, GeoResult(), dbg)

    def _store(self, key: str, result: GeoResult, dbg: list[str]) -> GeoResult:
        result.debug_info = dbg
        self.cache.set_geo(key, result)
        return result
