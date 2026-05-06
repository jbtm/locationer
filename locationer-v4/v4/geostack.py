import math
from typing import Optional

from .cache import Cache
from .explicit_store import ExplicitStore
from .geo_db import GeoDatabase
from .models import GeoResult, NormalizedRecord
from .nominatim import Nominatim
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
                result = GeoResult(
                    lat=tgn_row["lat"], lon=tgn_row["lon"], quality_score=5,
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
