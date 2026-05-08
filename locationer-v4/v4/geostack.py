import math
from typing import Optional

from .cache import Cache
from .explicit_store import ExplicitStore
from .geo_db import GeoDatabase
from .models import GeoResult, NormalizedRecord
from .nominatim import Nominatim

# (min_lat, max_lat, min_lon, max_lon) — 1-degree tolerance included (~111 km)
_COUNTRY_BBOX: dict[str, tuple[float, float, float, float]] = {
    "CH": (44.8, 49.0,  4.9, 11.5),
    "LI": (46.0, 48.3,  8.5, 10.7),
    "AT": (45.3, 50.1,  8.5, 18.2),
    "DE": (46.2, 56.2,  4.8, 16.0),
    "FR": (40.3, 52.1, -6.2, 10.7),
    "IT": (34.4, 48.2,  5.5, 19.9),
    "ES": (26.6, 44.8,-19.2,  5.4),
    "PT": (31.6, 43.2,-32.3, -5.1),
    "GB": (48.8, 62.0, -9.7,  2.8),
    "IE": (50.4, 56.0,-11.5, -4.5),
    "NL": (49.7, 54.7,  2.3,  8.3),
    "BE": (48.4, 52.6,  1.5,  7.5),
    "LU": (48.4, 51.2,  4.7,  7.6),
    "DK": (53.5, 58.8,  7.0, 16.3),
    "SE": (54.3, 70.1, 10.0, 25.2),
    "NO": (56.9, 72.2,  3.5, 32.2),
    "FI": (58.7, 71.1, 19.5, 32.6),
    "PL": (48.0, 56.0, 13.1, 25.2),
    "CZ": (47.5, 52.1, 11.1, 19.9),
    "SK": (46.7, 50.6, 15.8, 23.6),
    "HU": (44.7, 49.6, 15.1, 23.9),
    "RO": (42.6, 49.3, 19.2, 31.0),
    "HR": (41.4, 47.6, 12.5, 20.5),
    "SI": (44.4, 47.9, 12.4, 17.6),
    "GR": (33.8, 43.0, 18.4, 29.3),
    "TR": (34.8, 43.1, 24.7, 45.8),
    "RU": (40.2, 83.0, 18.6, 180.0),
    "US": (17.9, 72.4,-180.0,-65.9),
    "CA": (40.7, 84.1,-142.0,-51.6),
    "AU": (-44.7, -9.7, 112.2, 154.7),
    "JP": (23.0, 46.6, 121.9, 154.0),
    "CN": (17.2, 54.6,  72.5, 135.8),
    "IN": ( 7.0, 38.1,  67.2,  98.4),
    "ZA": (-35.9,-21.1,  15.5,  33.9),
    "MX": (13.5, 33.7,-119.5,-85.7),
    "BR": (-34.8,  6.3,-74.9,-33.8),
    "AR": (-56.1,-20.8,-74.6,-52.6),
    "MA": (27.6, 36.0, -14.0,  2.0),
    "EG": (21.9, 32.0,  24.7, 37.0),
    "ZZ": (-90.0, 90.0, -180.0, 180.0),  # unknown country — never reject
}


def _within_country_bbox(lat: float, lon: float, country_code: str) -> bool:
    bbox = _COUNTRY_BBOX.get(country_code)
    if bbox is None:
        return True  # unknown country → don't reject
    min_lat, max_lat, min_lon, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
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
    ):
        self.geo_db    = geo_db
        self.cache     = cache
        self.nominatim = nominatim
        self.tgn_db    = tgn_db
        self.debug     = debug
        self.overrides = overrides
        self.ext_count = 0  # Nominatim calls

    def geocode(self, record: NormalizedRecord) -> GeoResult:
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
        if cached:
            cached.debug_info = [f"cache hit: {cached.match_name or '—'}"]
            return cached

        country_code = self.geo_db.country_to_code(record.country) if self.geo_db else None
        dbg: list[str] = [f"country_code={country_code}"]

        # Resolve region name → admin1 code for city disambiguation
        admin1_hint = None
        if self.geo_db and country_code and record.region:
            admin1_hint = self.geo_db.region_to_admin1(country_code, record.region)
            if admin1_hint:
                dbg.append(f"region={record.region!r} → admin1={admin1_hint}")

        # ── Step 2: GEO DB city — for proximity ranking ───────────────────────
        city_row = self.geo_db.find_city(record.city, country_code, admin1_hint) if (self.geo_db and record.city) else None
        near = (city_row["lat"], city_row["lon"]) if city_row else None

        # If city not found as populated place, try as geographic feature (mountain,
        # lake, pass, etc.) — gives proximity anchor for cases like "Pilatus", "Rigi"
        if not near and self.geo_db and record.city:
            geo_feat = self.geo_db.find_precise(record.city, country_code)
            if geo_feat:
                near = (geo_feat["lat"], geo_feat["lon"])
                dbg.append(f"city as geo feature: {geo_feat['name']} [{geo_feat['feature_code']}]")

        # ── Step 3: GEO DB precise ────────────────────────────────────────────
        if record.location and self.geo_db:
            row = self.geo_db.find_precise(record.location, country_code, near=near)
            if row:
                dist = _dist_km(row["lat"], row["lon"], near[0], near[1]) if near else 0
                if near and dist > 50:
                    dbg.append(f"GEO DB precise {row['name']!r} rejected ({dist:.0f} km from city)")
                elif country_code and not _within_country_bbox(row["lat"], row["lon"], country_code):
                    dbg.append(f"GEO DB precise {row['name']!r} rejected (outside {country_code} bbox)")
                else:
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
                    if dist > 50:
                        dbg.append(f"TGN {tgn_row['pref_name']!r} rejected ({dist:.0f} km from city)")
                        tgn_ok = False
                if tgn_ok and country_code and not _within_country_bbox(tgn_lat, tgn_lon, country_code):
                    dbg.append(f"TGN {tgn_row['pref_name']!r} rejected (outside {country_code} bbox)")
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

            # Try location alone first (better Nominatim recall),
            # then with city+country for disambiguation
            nm_query = record.location if record.location else query
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
                # Proximity check: if we have a city anchor and Nominatim's precise hit
                # is too far away, it matched a homonym in a different place — retry
                # with city+country to find the geographically correct result.
                if nm.get("precise") and near and record.location:
                    dist = _dist_km(nm["lat"], nm["lon"], near[0], near[1])
                    if dist > 50:
                        dbg.append(f"Nominatim precise hit {nm['name']!r} rejected ({dist:.0f} km from city)")
                        nm2 = self.nominatim.search(query)
                        nm = nm2  # None if retry also fails — do not use the rejected hit
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
            result = GeoResult(
                lat=ext_result["lat"], lon=ext_result["lon"], quality_score=4,
                fallback=False, source=ext_result["source"],
                match_name=ext_result["name"],
            )
            return self._store(cache_key, result, dbg)

        if ext_result:
            dbg.append(f"external not precise: {ext_result['name']}")

        # ── Step 5: GEO DB city fallback ──────────────────────────────────────
        if city_row:
            if country_code and not _within_country_bbox(city_row["lat"], city_row["lon"], country_code):
                dbg.append(f"city fallback {city_row['name']!r} rejected (outside {country_code} bbox)")
            else:
                result = GeoResult(
                    lat=city_row["lat"], lon=city_row["lon"], quality_score=3,
                    fallback=True, source="geo_db", match_name=city_row["name"],
                )
                dbg.append(f"city GEO DB: {city_row['name']} [{city_row['feature_code']}]")
                return self._store(cache_key, result, dbg)

        # ── Step 6: Nominatim city-only (non-EU countries not in GEO DB) ──────
        if ext_result and not ext_result.get("precise"):
            result = GeoResult(
                lat=ext_result["lat"], lon=ext_result["lon"], quality_score=2,
                fallback=True, source=ext_result["source"],
                match_name=ext_result["name"],
            )
            dbg.append(f"city fallback via {ext_result['source']}: {ext_result['name']}")
            return self._store(cache_key, result, dbg)

        # City not found anywhere — try Nominatim city-only as last resort.
        # Always attempt this regardless of skip_ext: a city name is a concrete
        # anchor, not an ambiguous location string.
        if record.city and not city_row:
            city_q = " ".join(filter(None, [record.city, record.country]))
            nm_city = self.nominatim.search(city_q)
            if nm_city:
                self.ext_count += 1
                result = GeoResult(
                    lat=nm_city["lat"], lon=nm_city["lon"], quality_score=2,
                    fallback=True, source="nominatim", match_name=nm_city["name"],
                )
                dbg.append(f"Nominatim city retry: {nm_city['name']}")
                return self._store(cache_key, result, dbg)

        dbg.append("not found")
        return self._store(cache_key, GeoResult(), dbg)

    def _store(self, key: str, result: GeoResult, dbg: list[str]) -> GeoResult:
        result.debug_info = dbg
        self.cache.set_geo(key, result)
        return result
