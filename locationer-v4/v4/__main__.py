import argparse
import math
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import pandas as pd

from .cache import Cache
from .explicit_store import ExplicitStore
from .geo_db import GeoDatabase
from .geostack import GeoStack
from .input_normalizer import InputNormalizer
from .models import GeoResult, NormalizedRecord
from .nominatim import Nominatim
from .tgn_db import TgnDatabase

_GEO_DB_DEFAULT    = os.getenv("GEO_DB_PATH",   "/Volumes/LCMT_JBTM/LocationerGeo/locationer_geo_global.sqlite")
_TGN_DB_DEFAULT    = os.getenv("TGN_DB_PATH",   "/Volumes/LCMT_JBTM/LocationerGeo/tgn.sqlite")
_OVERRIDES_DEFAULT = os.getenv("OVERRIDES_PATH", "explicit_list/explicit.sqlite")
_CACHE_DEFAULT     = os.getenv("CACHE_PATH",     "cache/locationer.sqlite")
_NOMINATIM_URL     = os.getenv("NOMINATIM_URL",  "https://nominatim.openstreetmap.org")
_NOMINATIM_UA      = os.getenv("NOMINATIM_USER_AGENT", "locationer/4.0")


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _true_coords(row: dict) -> tuple[float, float] | None:
    """Return (lat, lon) from lat_true/lon_true if both are numeric, else None.."""
    try:
        lat = float(row.get("lat_true") or row.get("Lat_true") or row.get("Breitengrad") or "")
        lon = float(row.get("lon_true") or row.get("Lon_true") or row.get("Längengrad") or "")
        return lat, lon
    except (TypeError, ValueError):
        return None


def _get_region(geo_db: "GeoDatabase | None", rec: NormalizedRecord, geo: GeoResult) -> str:
    """Look up ADM1 (canton/state/province) from the city in the normalized record."""
    if geo_db is None or geo.lat is None or not rec.city:
        return ""
    country_code = geo_db.country_to_code(rec.country)
    city_row = geo_db.find_city(rec.city, country_code)
    if not city_row or not city_row["admin1"]:
        return ""
    return geo_db.find_region_name(city_row["country_code"], city_row["admin1"])


# Phrases that indicate the location is explicitly unknown → skip geocoding
_UNKNOWN_LOCATION_PHRASES = {
    "lokalisierung unsicher", "ort unbekannt", "standort unbekannt",
    "lokalisierung unbekannt", "nicht lokalisiert", "ohne ortsangabe",
    "place not defined", "location unknown", "place unknown",
    "location not identified", "place not identified", "not identified",
    "lieu inconnu", "localisation incertaine", "localisation inconnue",
    "luogo sconosciuto", "lugar desconocido",
}


def _has_unknown_location(row: dict) -> bool:
    for v in row.values():
        if v is None:
            continue
        text = str(v).lower()
        if any(phrase in text for phrase in _UNKNOWN_LOCATION_PHRASES):
            return True
    return False


def _read(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(p)
    else:
        with open(p, "rb") as f:
            first = f.readline().decode("utf-8", errors="replace")
        sep = ";" if first.count(";") > first.count(",") else ","
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                df = pd.read_csv(p, sep=sep, quotechar='"', engine="python", encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError(f"Cannot decode {p.name} with utf-8, utf-8-sig or latin-1")
    return df.dropna(how="all").reset_index(drop=True)


def _dev_str(geo: GeoResult, true: tuple | None) -> str:
    if true is None or geo.lat is None:
        return ""
    km = _haversine_km(geo.lat, geo.lon, true[0], true[1])
    return f"  Δ{km:.1f} km"


def _human(i: int, total: int, rec: NormalizedRecord, geo: GeoResult,
           true: tuple | None = None, google_total: int = 0):
    score_label = {5: "●●●●●", 4: "●●●●○", 3: "●●●○○", 2: "●●○○○", 0: "○○○○○"}.get(
        geo.quality_score, str(geo.quality_score)
    )
    loc = "/".join(filter(None, [rec.country, rec.city])) or "?"
    fb = " (city fallback)" if geo.fallback else ""
    src = geo.source if geo.source != "none" else "—"
    title = rec.title[:48].ljust(49)
    g_str = f"  [N:{google_total}]" if google_total else ""
    print(f"[{i:>5}/{total}] {title}  {loc:<28}  {score_label}  {src}{fb}{_dev_str(geo, true)}{g_str}")


def _debug(i: int, total: int, rec: NormalizedRecord, geo: GeoResult, true: tuple | None = None):
    print(f"\n{'─'*70}")
    print(f"[{i}/{total}] {rec.title}")
    print(f"  country={rec.country!r}  region={rec.region!r}  city={rec.city!r}  location={rec.location!r}")
    print(f"  description={rec.description[:80]!r}{'…' if len(rec.description)>80 else ''}")
    for step in geo.debug_info:
        print(f"  ▸ {step}")
    if geo.lat is not None:
        print(f"  → lat={geo.lat:.5f}  lon={geo.lon:.5f}  score={geo.quality_score}  fallback={geo.fallback}  source={geo.source}")
        print(f"  → matched: {geo.match_name}")
        if true:
            km = _haversine_km(geo.lat, geo.lon, true[0], true[1])
            print(f"  → deviation: {km:.2f} km  (true: {true[0]:.5f}, {true[1]:.5f})")
    else:
        print("  → NOT FOUND")


def _raw_text(row: dict) -> str:
    return " ".join(str(v) for v in row.values() if v is not None and str(v).strip())


def _print_stats(output_rows: list[dict], unknown_loc_count: int) -> None:
    n = len(output_rows)
    if n == 0:
        return
    scores = {0: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    fb = 0
    for row in output_rows:
        s = row.get("Coord-Quality-Score", 0)
        scores[s] = scores.get(s, 0) + 1
        if row.get("Fallback"):
            fb += 1

    print(f"\n{'─'*70}")
    print(f"SCORE-STATISTIK  (n={n})")
    print(f"{'─'*70}")
    labels = {
        5: "Score 5 ●●●●● GEO DB / TGN präzis",
        4: "Score 4 ●●●●○ Nominatim präzis",
        3: "Score 3 ●●●○○ Stadtzentrum GEO DB",
        2: "Score 2 ●●○○○ Stadtzentrum Nominatim",
        0: "Score 0 ○○○○○ nicht gefunden",
    }
    for s in [5, 4, 3, 2, 0]:
        cnt = scores.get(s, 0)
        bar = "█" * int(cnt / n * 40)
        print(f"  {labels[s]:38s} {cnt:5d}  {cnt/n*100:5.1f}%  {bar}")
    print(f"  {'davon Ort unbekannt (explizit)':38s} {unknown_loc_count:5d}  {unknown_loc_count/n*100:5.1f}%")
    found = n - scores.get(0, 0)
    print(f"  {'─'*70}")
    print(f"  {'Treffer total (Score > 0)':38s} {found:5d}  {found/n*100:5.1f}%")
    print(f"  {'davon Fallback (Stadtzentrum)':38s} {fb:5d}  {fb/n*100:5.1f}%")


def _process_chunk(
    chunk_rows: list[dict],
    chunk_start: int,
    total: int,
    normalizer,
    geostack,
    geo_db: "GeoDatabase",
    has_true_coords: bool,
    mode: str,
) -> tuple[list[dict], int]:
    normalized  = normalizer.normalize_batch(chunk_rows)
    metadata    = normalizer.extract_metadata_batch(chunk_rows)   # Phase 1b

    # Apply norm_overrides: correct city/country extracted by Phase 1 AI
    if geostack.overrides:
        for rec, row in zip(normalized, chunk_rows):
            correction = geostack.overrides.match_norm(_raw_text(row))
            if correction:
                if "city" in correction and not rec.city:
                    rec.city = correction["city"]
                    if mode == "debug":
                        print(f"  [norm override] city → {correction['city']!r}")
                if "country" in correction and not rec.country:
                    rec.country = correction["country"]

    output_rows = []
    unknown_loc = 0
    for i, (rec, row, meta) in enumerate(zip(normalized, chunk_rows, metadata), chunk_start + 1):
        # Skip geocoding when input explicitly states location is unknown
        if _has_unknown_location(row):
            geo = GeoResult()
            geo.debug_info = ["skipped: location unknown in input"]
            unknown_loc += 1
        else:
            geo = geostack.geocode(rec)

        true = _true_coords(row) if has_true_coords else None
        dev_km = _haversine_km(geo.lat, geo.lon, true[0], true[1]) if (true and geo.lat is not None) else None
        region = _get_region(geo_db, rec, geo)

        if mode == "human":
            _human(i, total, rec, geo, true, google_total=geostack.ext_count)
        else:
            _debug(i, total, rec, geo, true)

        out = {
            "Title": rec.title,
            "Description": rec.description,
            "Periode": meta.get("periode"),
            "Urheber": meta.get("urheber"),
            "Technik": meta.get("technik"),
            "Country": rec.country,
            "Region": region,
            "City": rec.city,
            "Lat": geo.lat,
            "Lon": geo.lon,
            "Coord-Quality-Score": geo.quality_score,
            "Fallback": geo.fallback,
            "Ext-Calls": 1 if (geo.source in ("wikidata","nominatim") and geo.debug_info != ["cache hit"]) else 0,
        }
        if has_true_coords:
            out["Deviation_km"] = round(dev_km, 2) if dev_km is not None else None
        output_rows.append(out)
    return output_rows, unknown_loc


def main():
    ap = argparse.ArgumentParser(description="Locationer V4 – geocoding pipeline")
    ap.add_argument("input", help="Input CSV or XLSX file")
    ap.add_argument("--mode", choices=["human", "debug"], default="human")
    ap.add_argument("--output", "-o", help="Output CSV (default: <input>_geo.csv)")
    ap.add_argument("--limit", type=int, help="Process only first N rows")
    ap.add_argument("--chunk-size", type=int, default=20, dest="chunk_size",
                    help="Process in chunks of N rows, auto-resume on restart (default: 20)")
    ap.add_argument("--geo-db", default=_GEO_DB_DEFAULT, dest="geo_db")
    args = ap.parse_args()

    try:
        df = _read(args.input)
    except Exception as e:
        sys.exit(f"Cannot read {args.input}: {e}")

    if args.limit:
        df = df.head(args.limit)

    out_path = args.output or str(Path(args.input).with_suffix("")) + "_geo.csv"

    # With --chunk-size: automatically resume if output already exists.
    # To restart from scratch, use a different --output path.
    skip_rows = 0
    if args.chunk_size and Path(out_path).exists():
        try:
            existing = pd.read_csv(out_path)
            skip_rows = len(existing)
            df = df.iloc[skip_rows:].reset_index(drop=True)
            if skip_rows:
                print(f"Resuming: {skip_rows} Zeilen bereits geschrieben, weiter ab Zeile {skip_rows + 1}")
        except Exception:
            pass

    total = len(df)                    # rows to process this run
    has_true_coords = any(_true_coords(r) for r in df.head(5).where(pd.notna(df.head(5)), None).to_dict("records"))

    print(f"Locationer V4  ·  {total} records  ·  mode={args.mode}"
          + (f"  ·  chunk={args.chunk_size}" if args.chunk_size else "")
          + (f"  ·  ab Zeile {skip_rows + 1}" if skip_rows else ""))

    # GEO DB availability check
    geo_db_path = Path(args.geo_db)
    if not geo_db_path.exists():
        print(f"\n⚠  GEO DB nicht gefunden: {args.geo_db}")
        print("   Externe Disk LCMT_JBTM nicht gemountet?")
        print("   Ohne GEO DB: ausschliesslich Google (teurer, langsamer, weniger präzise).")
        answer = input("\n   Trotzdem starten? [y/N] ").strip().lower()
        if answer != "y":
            sys.exit("Abgebrochen.")
        geo_db_available = False
        print()
    else:
        geo_db_available = True
        print(f"GEO DB: {args.geo_db}")
    print()

    cache     = Cache(_CACHE_DEFAULT)
    overrides = ExplicitStore(_OVERRIDES_DEFAULT)
    geo_db    = GeoDatabase(args.geo_db) if geo_db_available else None

    # TGN (optional — gracefully absent if not yet imported)
    tgn_db = None
    if Path(_TGN_DB_DEFAULT).exists():
        tgn_db = TgnDatabase(_TGN_DB_DEFAULT)
        print(f"TGN DB:  {_TGN_DB_DEFAULT}")
    else:
        print(f"TGN DB:  not found (run python -m v4.import_tgn to build)")

    import sqlite3 as _sqlite3
    _ext_conn = _sqlite3.connect(_CACHE_DEFAULT)
    nominatim = Nominatim(_ext_conn, base_url=_NOMINATIM_URL, user_agent=_NOMINATIM_UA,
                          debug=(args.mode == "debug"))
    normalizer = InputNormalizer(cache, debug=(args.mode == "debug"), tgn_db=tgn_db, geo_db=geo_db)
    geostack   = GeoStack(geo_db, cache, nominatim,
                          debug=(args.mode == "debug"), overrides=overrides,
                          tgn_db=tgn_db)

    chunk_size = args.chunk_size or total  # no chunking = one big chunk

    if args.mode == "human":
        dev_col = "  Deviation" if has_true_coords else ""
        print(f"  {'Title':<49}  {'Country/City':<28}  Score  Source{dev_col}")
        print(f"  {'─'*49}  {'─'*28}  {'─'*5}  {'─'*12}")

    # In resume mode the output file already exists → append without header
    first_chunk = skip_rows == 0  # append if resuming, write fresh if starting new
    written = 0
    all_output_rows: list[dict] = []
    total_unknown_loc = 0
    for chunk_start in range(0, total, chunk_size):
        chunk_df = df.iloc[chunk_start:chunk_start + chunk_size]
        chunk_rows = chunk_df.where(pd.notna(chunk_df), None).to_dict("records")

        if args.chunk_size:
            end = min(chunk_start + chunk_size, total)
            print(f"\n── Chunk {chunk_start+1}–{end} / {total} ──────────────")

        output_rows, unknown_loc = _process_chunk(
            chunk_rows, chunk_start, total,
            normalizer, geostack, geo_db,
            has_true_coords, args.mode,
        )

        pd.DataFrame(output_rows).to_csv(
            out_path,
            mode="w" if first_chunk else "a",
            header=first_chunk,
            index=False,
        )
        first_chunk = False
        written += len(output_rows)
        all_output_rows.extend(output_rows)
        total_unknown_loc += unknown_loc

        if args.chunk_size:
            print(f"   → {written}/{total} geschrieben  |  Nominatim bisher: {geostack.ext_count}")

    print(f"\n{'─'*70}")
    print(f"Output:          {out_path}")
    print(f"Haiku calls (Phase 1a+1b): {normalizer.haiku_count}")
    print(f"Nominatim calls:           {geostack.ext_count}")
    _print_stats(all_output_rows, total_unknown_loc)
    print()

    cache.close()
    overrides.close()
    _ext_conn.close()
    if geo_db:
        geo_db.close()
    if tgn_db:
        tgn_db.close()

    from v4.metrics import TESTFILE, measure, to_row, update_log, print_table
    if Path(args.input).resolve() == Path(TESTFILE).resolve():
        from datetime import datetime
        out_df = pd.read_csv(out_path)
        m   = measure(out_df, geostack.ext_count)
        row = to_row(m, datetime.now().strftime("%Y-%m-%d %H:%M"))
        update_log(row)
        print_table(row)

        # Metadaten-Auswertung (nur bei TestFile)
        n = len(out_df)
        for col in ("Periode", "Urheber", "Technik"):
            found = out_df[col].notna().sum() if col in out_df.columns else 0
            examples = out_df[col].dropna().unique()[:3].tolist() if found else []
            ex_str = "  z.B.: " + ", ".join(f'"{e}"' for e in examples) if examples else ""
            print(f"  {col:<8} {found:3d}/{n} ({found/n*100:4.0f}%){ex_str}")


if __name__ == "__main__":
    main()
