"""
import_country.py — Download and import country-specific place-name databases.

Data source: GeoNames per-country files with full alternate names.
Each country DB supplements the global GeoNames DB with complete name variants
(incl. regional languages: Romansh/CH, Ladin/IT, Low German/DE, Nynorsk/NO, …).

Usage:
    python -m v4.import_country CH --output /path/to/ch_country.sqlite
    python -m v4.import_country DE FR IT AT CH NO --output-dir /path/to/dir/
    python -m v4.import_country CH --check   # show stats only

Downloads:
    https://download.geonames.org/export/dump/{CC}.zip           (places)
    https://download.geonames.org/export/dump/alternateNames/{CC}.zip  (names)
"""

import argparse
import io
import sqlite3
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

from .country_db import SCHEMA, SUPPORTED, _norm

# ── GeoNames feature codes ────────────────────────────────────────────────────
# 'precise' = named landmark, geographic feature, building, etc.
_PRECISE_CLASSES = {"S", "T", "H", "V", "L", "U"}
# 'city'    = populated place or admin unit used as city-level fallback
_CITY_CODES = {
    "PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLC", "PPLX",
    "ADM3", "ADM4",
}

_GEONAMES_BASE = "https://download.geonames.org/export/dump"

# Languages to include as alt-name variants per country
_EXTRA_LANGS: dict[str, set[str]] = {
    "CH": {"rm", "fr", "it", "de"},   # Romansh, French, Italian, German
    "IT": {"de", "lld", "fur"},        # German (Südtirol), Ladin, Friulian
    "NO": {"nn", "nb", "se"},          # Nynorsk, Bokmål, Sami
    "DE": {"nds", "ksh", "bar"},       # Low German, Colognian, Bavarian
    "FR": {"oc", "br", "co", "als"},   # Occitan, Breton, Corsican, Alsatian
    "AT": {"bar"},                      # Bavarian
}


# ── Download helpers ──────────────────────────────────────────────────────────
def _download_zip(url: str, desc: str) -> bytes:
    print(f"  Downloading {desc}… ", end="", flush=True)
    r = requests.get(url, timeout=120, stream=True)
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0))
    buf = io.BytesIO()
    done = 0
    for chunk in r.iter_content(65536):
        buf.write(chunk)
        done += len(chunk)
        if total:
            print(f"\r  Downloading {desc}… {done//1024:,} KB / {total//1024:,} KB   ", end="", flush=True)
    print(f"\r  Downloaded {desc}: {done//1024:,} KB          ")
    buf.seek(0)
    return buf.read()


def _read_tsv_from_zip(data: bytes, filename: str) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        with zf.open(filename) as f:
            return [line.decode("utf-8").rstrip("\n").split("\t")
                    for line in f if not line.startswith(b"#")]


# ── Parser ────────────────────────────────────────────────────────────────────
def _parse_places(rows: list[list[str]], cc: str) -> list[dict]:
    """Parse GeoNames main file into place dicts."""
    # GeoNames columns:
    # 0=geonameid 1=name 2=asciiname 3=alternatenames 4=lat 5=lon
    # 6=feature_class 7=feature_code 8=country_code ... 14=population ...
    places = []
    for row in rows:
        if len(row) < 15:
            continue
        if row[8] != cc:
            continue
        try:
            lat = float(row[4])
            lon = float(row[5])
        except ValueError:
            continue

        fc = row[6]   # feature class
        fcode = row[7]  # feature code

        if fc in _PRECISE_CLASSES:
            ftype = "precise"
        elif fcode in _CITY_CODES:
            ftype = "city"
        else:
            continue  # skip admin boundaries, etc.

        try:
            pop = int(row[14]) if row[14] else 0
        except ValueError:
            pop = 0

        places.append({
            "place_id":      row[0],
            "canonical_name": row[1],
            "name_norm":     _norm(row[1]),
            "lat":           lat,
            "lon":           lon,
            "feature_type":  ftype,
            "admin1":        row[10] or None,
            "population":    pop,
            "country_code":  cc,
        })
    return places


def _parse_alt_names(data: bytes, cc: str, extra_langs: set[str]) -> dict[str, list[str]]:
    """
    Parse alternateNames file. Returns {geonames_id: [norm1, norm2, …]}
    for names in the target language set.
    """
    alt: dict[str, list[str]] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        fname = f"{cc}.txt" if f"{cc}.txt" in zf.namelist() else zf.namelist()[0]
        with zf.open(fname) as f:
            for line in f:
                parts = line.decode("utf-8").rstrip("\n").split("\t")
                if len(parts) < 5:
                    continue
                gid  = parts[1]
                lang = parts[2]
                name = parts[3]
                if lang not in extra_langs and lang != "":
                    continue
                if not name:
                    continue
                norm = _norm(name)
                if gid not in alt:
                    alt[gid] = []
                if norm not in alt[gid]:
                    alt[gid].append(norm)
    return alt


# ── Build DB ──────────────────────────────────────────────────────────────────
def build(cc: str, output_path: str, check_only: bool = False):
    cc = cc.upper()
    if cc not in SUPPORTED:
        sys.exit(f"Unsupported country: {cc}. Supported: {', '.join(sorted(SUPPORTED))}")

    if check_only:
        if not Path(output_path).exists():
            print(f"{cc}: {output_path} not found")
            return
        con = sqlite3.connect(output_path)
        n_places = con.execute("SELECT COUNT(DISTINCT place_id) FROM places").fetchone()[0]
        n_rows   = con.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        meta     = {r[0]: r[1] for r in con.execute("SELECT key, value FROM meta").fetchall()}
        con.close()
        print(f"{cc}: {n_places:,} places, {n_rows:,} name variants")
        for k, v in meta.items():
            print(f"  {k}: {v}")
        return

    print(f"\n{'='*50}")
    print(f"Importing {cc} → {output_path}")
    print(f"{'='*50}")

    # 1. Download places
    places_zip  = _download_zip(f"{_GEONAMES_BASE}/{cc}.zip", f"{cc}.zip")
    places_data = _read_tsv_from_zip(places_zip, f"{cc}.txt")
    places      = _parse_places(places_data, cc)
    print(f"  Parsed: {len(places):,} places")

    # 2. Download alternate names
    extra_langs = _EXTRA_LANGS.get(cc, set())
    alt: dict[str, list[str]] = {}
    if extra_langs:
        try:
            alt_zip = _download_zip(
                f"{_GEONAMES_BASE}/alternateNames/{cc}.zip",
                f"alternateNames/{cc}.zip"
            )
            alt = _parse_alt_names(alt_zip, cc, extra_langs)
            print(f"  Alternate names: {sum(len(v) for v in alt.values()):,} variants "
                  f"for {len(alt):,} places")
        except Exception as e:
            print(f"  Warning: could not fetch alternate names: {e}")

    # 3. Write to SQLite
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    con = sqlite3.connect(output_path)
    con.executescript(SCHEMA)

    rows: list[tuple] = []
    for p in places:
        rows.append((
            p["place_id"], p["canonical_name"], p["name_norm"],
            p["lat"], p["lon"], p["feature_type"],
            p["admin1"], p["population"], p["country_code"],
        ))
        # Additional name variants from alternate names
        for anorm in alt.get(p["place_id"], []):
            if anorm != p["name_norm"]:
                rows.append((
                    p["place_id"], p["canonical_name"], anorm,
                    p["lat"], p["lon"], p["feature_type"],
                    p["admin1"], p["population"], p["country_code"],
                ))

    con.executemany(
        "INSERT INTO places (place_id, canonical_name, name_norm, lat, lon, "
        "feature_type, admin1, population, country_code) VALUES (?,?,?,?,?,?,?,?,?)",
        rows
    )

    # Meta
    from datetime import date
    n_places = len(places)
    n_rows   = len(rows)
    con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
        ("country_code",   cc),
        ("source",         "GeoNames + alternateNames"),
        ("built_date",     date.today().isoformat()),
        ("n_places",       str(n_places)),
        ("n_name_variants", str(n_rows)),
        ("extra_langs",    ",".join(sorted(extra_langs))),
    ])
    con.commit()
    con.close()

    print(f"  Written: {n_places:,} places, {n_rows:,} name variants → {output_path}")
    print(f"  Extra languages: {', '.join(sorted(extra_langs)) or '(none)'}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Build country-specific GeoNames databases for Locationer v4"
    )
    ap.add_argument("countries", nargs="+",
                    help=f"Country codes: {', '.join(sorted(SUPPORTED))}")
    ap.add_argument("--output", "-o",
                    help="Output SQLite path (single country only)")
    ap.add_argument("--output-dir", "-d", default=".",
                    help="Output directory (for multiple countries, default: .)")
    ap.add_argument("--check", action="store_true",
                    help="Show stats for existing DB, no download")
    args = ap.parse_args()

    countries = [c.upper() for c in args.countries]

    if args.output and len(countries) > 1:
        sys.exit("--output only works with a single country. Use --output-dir for multiple.")

    for cc in countries:
        if args.output:
            out = args.output
        else:
            out = str(Path(args.output_dir) / f"{cc.lower()}_country.sqlite")
        build(cc, out, check_only=args.check)

    print("\nDone. Add to .env:")
    for cc in countries:
        if args.output:
            out = args.output
        else:
            out = str(Path(args.output_dir).resolve() / f"{cc.lower()}_country.sqlite")
        print(f"  COUNTRY_DB_{cc}={out}")


if __name__ == "__main__":
    main()
