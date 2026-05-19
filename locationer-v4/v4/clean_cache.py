"""
Validate and purge stale geo_cache entries.

Checks score-2 (Nominatim city-fallback) entries against GEO DB:
if the cached coordinates deviate more than the city radius, the entry
is deleted so the next pipeline run re-geocodes it correctly.

Usage:
    python -m v4.clean_cache [--dry-run] [--all-nominatim]
"""

import argparse
import math
import os
import sqlite3

from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.local")

from .geo_db import GeoDatabase

CACHE_PATH   = os.getenv("CACHE_PATH", "cache/locationer.sqlite")
GEO_DB_PATH  = os.getenv("GEO_DB_PATH", "")


def _dist_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _city_radius_km(city_row) -> float:
    if city_row is None:
        return 5.0
    pop = city_row["population"] or 0
    if pop > 500_000: return 25.0
    if pop > 100_000: return 15.0
    if pop >  20_000: return  8.0
    if pop >   5_000: return  5.0
    if pop >   1_000: return  3.0
    return 2.0


# Country bounding boxes — same as geostack.py
_COUNTRY_BBOX = {
    "CH": (45.62, 48.01,  5.76, 10.69),
    "LI": (46.85, 47.47,  9.28,  9.84),
    "AT": (46.18, 49.22,  9.33, 17.36),
    "DE": (46.87, 55.26,  5.67, 15.23),
    "FR": (41.13, 51.30, -5.25,  9.76),
    "IT": (35.29, 47.29,  6.35, 18.81),
}


def _infer_country(lat: float, lon: float) -> str | None:
    """Return the most specific country code for the given coordinates."""
    best = None
    best_area = float("inf")
    for cc, (mn_lat, mx_lat, mn_lon, mx_lon) in _COUNTRY_BBOX.items():
        if mn_lat <= lat <= mx_lat and mn_lon <= lon <= mx_lon:
            area = (mx_lat - mn_lat) * (mx_lon - mn_lon)
            if area < best_area:
                best, best_area = cc, area
    return best


def main():
    ap = argparse.ArgumentParser(description="Purge stale geo_cache entries")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    ap.add_argument("--all-nominatim", action="store_true",
                    help="Check all Nominatim entries (score 2+4), not just fallbacks")
    args = ap.parse_args()

    if not GEO_DB_PATH or not os.path.exists(GEO_DB_PATH):
        print(f"GEO DB not found: {GEO_DB_PATH!r}")
        return

    geo_db = GeoDatabase(GEO_DB_PATH)
    conn   = sqlite3.connect(CACHE_PATH)

    if args.all_nominatim:
        rows = conn.execute(
            "SELECT key, lat, lon, quality_score, match_name FROM geo_cache "
            "WHERE source='nominatim' AND lat IS NOT NULL"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT key, lat, lon, quality_score, match_name FROM geo_cache "
            "WHERE source='nominatim' AND fallback=1 AND lat IS NOT NULL"
        ).fetchall()

    print(f"Checking {len(rows)} entries …\n")

    to_delete = []
    for key, lat, lon, score, match_name in rows:
        if not match_name:
            continue
        # Infer country from cached coordinates, then do a country-constrained lookup
        cc = _infer_country(lat, lon)
        city_row = geo_db.find_city(match_name, cc) if cc else geo_db.find_city(match_name, None)
        if not city_row:
            continue
        ref_dist = _dist_km(lat, lon, city_row["lat"], city_row["lon"])
        # Skip if GEO DB found a same-named city far away — likely wrong reference
        if ref_dist > 300:
            continue
        radius = _city_radius_km(city_row)
        if ref_dist > radius:
            to_delete.append((key, match_name, lat, lon, city_row["lat"], city_row["lon"], ref_dist, radius))

    if not to_delete:
        print("Keine faulen Einträge gefunden.")
        return

    print(f"{'match_name':<25} {'cached':>22} {'GEO DB':>22} {'dist':>7} {'radius':>7}")
    print("─" * 90)
    for key, name, lat, lon, clat, clon, dist, radius in to_delete:
        print(f"{name:<25} ({lat:8.4f},{lon:8.4f})  ({clat:8.4f},{clon:8.4f})  {dist:6.1f}km  {radius:5.1f}km")

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Lösche {len(to_delete)} Einträge …")
    if not args.dry_run:
        for key, *_ in to_delete:
            conn.execute("DELETE FROM geo_cache WHERE key=?", (key,))
        conn.commit()
        print("Fertig.")
    else:
        print("(nichts gelöscht — dry-run)")

    conn.close()


if __name__ == "__main__":
    main()
