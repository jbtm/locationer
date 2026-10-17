"""
import_swissnames.py — Import swisstopo SwissNAMES3D into CH country DB.

SwissNAMES3D is the official Swiss toponym dataset (swisstopo). It covers
261k Flurnamen, official multilingual names (DE/FR/IT/Romansh), peaks,
passes, valleys, lakes — features often absent from or incomplete in GeoNames.

All entries are imported as feature_type='precise' (Score 5 in pipeline).
No city-type entries: Swiss cities are already well covered by GeoNames CH.

Coordinates: LV95 (EPSG:2056) → WGS84 via swisstopo approximation (~1m accuracy).

Usage:
    python -m v4.import_swissnames --input-dir /path/to/CSV_Dateien_Enea \\
                                   --output /path/to/ch_country.sqlite

    # Merge into existing CH DB (recommended — adds on top of GeoNames CH):
    python -m v4.import_swissnames --input-dir /path/to/CSV_Dateien_Enea \\
                                   --output /path/to/ch_country.sqlite --merge

    # Stats only:
    python -m v4.import_swissnames --output /path/to/ch_country.sqlite --check
"""

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from .country_db import SCHEMA, _norm


# ── LV95 → WGS84 (swisstopo Näherungsformel, ~1m accuracy) ───────────────────
def _lv95_to_wgs84(E: float, N: float) -> tuple[float, float]:
    y = (E - 2_600_000) / 1_000_000
    x = (N - 1_200_000) / 1_000_000
    lon = (2.6779094 + 4.728982*y + 0.791484*y*x + 0.1306*y*x*x - 0.0436*y*y*y) * 100/36
    lat = (16.9023892 + 3.238272*x - 0.270978*y*y - 0.002528*x*x
           - 0.0447*y*y*x - 0.0140*x*x*x) * 100/36
    return lat, lon


# ── Kanton name → GeoNames admin1 code ───────────────────────────────────────
_KANTON_TO_ADMIN1: dict[str, str] = {
    "Aargau": "AG", "Appenzell Ausserrhoden": "AR", "Appenzell Innerrhoden": "AI",
    "Basel-Landschaft": "BL", "Basel-Stadt": "BS", "Bern": "BE",
    "Fribourg": "FR", "Genève": "GE", "Glarus": "GL", "Graubünden": "GR",
    "Jura": "JU", "Luzern": "LU", "Neuchâtel": "NE", "Nidwalden": "NW",
    "Obwalden": "OW", "Schaffhausen": "SH", "Schwyz": "SZ", "Solothurn": "SO",
    "St. Gallen": "SG", "Thurgau": "TG", "Ticino": "TI", "Uri": "UR",
    "Valais": "VS", "Vaud": "VD", "Zug": "ZG", "Zürich": "ZH",
}

# ── OBJEKTART filter ──────────────────────────────────────────────────────────
# PKT (points): which types to import
_PKT_INCLUDE = {
    "Hauptgipfel", "Gipfel", "Alpiner Gipfel", "Huegel", "Haupthuegel",
    "Pass", "Strassenpass",
    "Ort", "Ortsteil", "Quartierteil", "Quartier",
    "Lokalname swisstopo", "Flurname swisstopo",
    "Haltestelle Bahn", "Haltestelle Schiff", "Uebrige Bahnen",
    "Kapelle", "Gebaeude", "Offenes Gebaeude",
}

# PLY (polygons): which types to import (centroid used as coordinates)
_PLY_INCLUDE = {
    "Tal", "Haupttal",
    "See", "Gletscher",
    "Grat", "Huegelzug", "Massiv",
    "Gebiet", "Landschaftsname",
    "Historisches Areal", "Klosterareal",
    "Ort", "Ortsteil", "Quartierteil", "Quartier",
}

# LIN (lines): skipped — no meaningful centroid for linear features


# ── CSV reader ────────────────────────────────────────────────────────────────
def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


# ── Parse one file ────────────────────────────────────────────────────────────
def _parse_rows(rows: list[dict], include_types: set[str]) -> list[dict]:
    """
    Returns a list of place dicts, one per name variant (= one per CSV row).
    Groups by NAME_UUID to assign canonical_name (German preferred).
    """
    # Pass 1: collect all rows per NAME_UUID, pick canonical name (German preferred)
    by_uuid: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("OBJEKTART") not in include_types:
            continue
        uuid = row.get("NAME_UUID", "").strip("{}")
        if not uuid:
            continue
        try:
            E = float(row["E"])
            N = float(row["N"])
        except (ValueError, KeyError):
            continue
        by_uuid[uuid].append(row)

    # Pass 2: for each UUID, determine canonical_name and emit name variants
    places = []
    for uuid, variants in by_uuid.items():
        # Canonical: prefer German official, then first official, then first row
        canonical_row = next(
            (r for r in variants if "Hochdeutsch" in r.get("SPRACHCODE", "")
             and r.get("STATUS") == "offiziell"),
            next(
                (r for r in variants if r.get("STATUS") == "offiziell"),
                variants[0]
            )
        )
        canonical_name = canonical_row["NAME"].strip()
        E = float(canonical_row["E"])
        N = float(canonical_row["N"])
        lat, lon = _lv95_to_wgs84(E, N)
        admin1 = _KANTON_TO_ADMIN1.get(canonical_row.get("Kanton", ""))

        # Emit one row per unique name variant (dedup by norm)
        seen_norms: set[str] = set()
        for v in variants:
            name = v["NAME"].strip()
            if not name:
                continue
            n = _norm(name)
            if n in seen_norms:
                continue
            seen_norms.add(n)
            places.append({
                "place_id":      uuid,
                "canonical_name": canonical_name,
                "name_norm":     n,
                "lat":           lat,
                "lon":           lon,
                "feature_type":  "precise",
                "admin1":        admin1,
                "population":    0,
                "country_code":  "CH",
            })
    return places


# ── Build / merge ─────────────────────────────────────────────────────────────
def build(input_dir: Path, output_path: str, merge: bool = False):
    pkt_file = input_dir / "swissNAMES3D_PKT_mit_kanton_mit_gemeinde.csv"
    ply_file = input_dir / "swissNAMES3D_PLY_mit_kanton_mit_gemeinde.csv"

    for f in (pkt_file, ply_file):
        if not f.exists():
            sys.exit(f"File not found: {f}")

    print(f"\nSwissNAMES3D → {output_path}  ({'merge' if merge else 'new'})")

    print("  Reading PKT…", end="", flush=True)
    pkt_rows = _read_csv(pkt_file)
    print(f" {len(pkt_rows):,} rows")

    print("  Reading PLY…", end="", flush=True)
    ply_rows = _read_csv(ply_file)
    print(f" {len(ply_rows):,} rows")

    print("  Parsing PKT…", end="", flush=True)
    pkt_places = _parse_rows(pkt_rows, _PKT_INCLUDE)
    print(f" {len(pkt_places):,} name variants")

    print("  Parsing PLY…", end="", flush=True)
    ply_places = _parse_rows(ply_rows, _PLY_INCLUDE)
    print(f" {len(ply_places):,} name variants")

    all_places = pkt_places + ply_places
    print(f"  Total: {len(all_places):,} name variants "
          f"({len({p['place_id'] for p in all_places}):,} unique places)")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not merge and out.exists():
        out.unlink()

    con = sqlite3.connect(output_path)
    con.executescript(SCHEMA)

    rows = [
        (p["place_id"], p["canonical_name"], p["name_norm"],
         p["lat"], p["lon"], p["feature_type"],
         p["admin1"], p["population"], p["country_code"])
        for p in all_places
    ]
    con.executemany(
        "INSERT INTO places (place_id, canonical_name, name_norm, lat, lon, "
        "feature_type, admin1, population, country_code) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
        ("swissnames_imported",      "true"),
        ("swissnames_date",          date.today().isoformat()),
        ("swissnames_pkt_variants",  str(len(pkt_places))),
        ("swissnames_ply_variants",  str(len(ply_places))),
    ])
    con.commit()

    n_total = con.execute("SELECT COUNT(DISTINCT place_id) FROM places").fetchone()[0]
    n_rows  = con.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    con.close()

    print(f"  Written: {n_total:,} unique places, {n_rows:,} name variants → {output_path}")


def check(output_path: str):
    if not Path(output_path).exists():
        print(f"Not found: {output_path}")
        return
    con = sqlite3.connect(output_path)
    n_p = con.execute("SELECT COUNT(DISTINCT place_id) FROM places").fetchone()[0]
    n_r = con.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    n_sn = con.execute(
        "SELECT COUNT(DISTINCT place_id) FROM places WHERE population = 0 "
        "AND feature_type = 'precise'"
    ).fetchone()[0]
    meta = {r[0]: r[1] for r in con.execute("SELECT key, value FROM meta").fetchall()}
    con.close()
    print(f"DB: {output_path}")
    print(f"  Total unique places:  {n_p:,}")
    print(f"  Total name variants:  {n_r:,}")
    print(f"  SwissNAMES3D precise: {n_sn:,}")
    for k, v in meta.items():
        print(f"  {k}: {v}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Import swisstopo SwissNAMES3D into CH country DB"
    )
    ap.add_argument("--input-dir", "-i",
                    help="Directory with swissNAMES3D_*_mit_kanton_mit_gemeinde.csv files")
    ap.add_argument("--output", "-o", required=True,
                    help="Output (or existing) CH country SQLite")
    ap.add_argument("--merge", action="store_true",
                    help="Merge into existing DB instead of creating new")
    ap.add_argument("--check", action="store_true",
                    help="Show stats for existing DB, no import")
    args = ap.parse_args()

    if args.check:
        check(args.output)
        return

    if not args.input_dir:
        ap.error("--input-dir required unless --check")

    build(Path(args.input_dir), args.output, merge=args.merge)


if __name__ == "__main__":
    main()
