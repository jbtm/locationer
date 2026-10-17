"""
Import the Getty TGN full XML dump into a local SQLite database.

Modes:
    --from-zip <path>    Read directly from a local tgn_xml_*.zip (no extraction needed)
    --from-dir <path>    Read individual *.xml files from a directory
    --download           Download from Getty and import (needs network, ~600 MB)

Usage examples:
    python -m v4.import_tgn --from-zip cache/tgn_xml_0126.zip --output cache/tgn.sqlite
    python -m v4.import_tgn --from-dir cache/tgn         --output cache/tgn.sqlite
    python -m v4.import_tgn --download                    --output cache/tgn.sqlite
    python -m v4.import_tgn --check --output cache/tgn.sqlite

ODC-By licence.
"""

import argparse
import gzip
import io
import re
import sqlite3
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import requests

TGN_BASE       = "http://tgndownloads.getty.edu/VocabData/"
TGN_PLACES_URL = TGN_BASE + "tgn_xml_0126.zip"


def ascii_norm(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace(".", "")


def _create_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tgn_place (
            tgn_id        TEXT PRIMARY KEY,
            pref_name     TEXT,
            name_norm     TEXT,
            lat           REAL,
            lon           REAL,
            place_type    TEXT,
            parent_tgn_id TEXT,
            country_code  TEXT
        );
        CREATE TABLE IF NOT EXISTS tgn_name (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            tgn_id    TEXT NOT NULL,
            name      TEXT NOT NULL,
            name_norm TEXT NOT NULL,
            lang      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tgn_place_norm   ON tgn_place(name_norm);
        CREATE INDEX IF NOT EXISTS idx_tgn_name_norm    ON tgn_name(name_norm);
        CREATE INDEX IF NOT EXISTS idx_tgn_name_tgn_id  ON tgn_name(tgn_id);
    """)
    conn.commit()


def _parse_subject_elem(elem) -> tuple | None:
    """
    Parse a <Subject Subject_ID="..."> element from Getty TGN XML.
    Returns (place_row, [name_rows]) or None.

    Actual XML structure (from tgn_xml_0126.zip individual files):
      <Subject Subject_ID="...">
        <Terms>
          <Preferred_Term><Term_Text>...</Term_Text></Preferred_Term>
          <Non-Preferred_Term><Term_Text>...</Term_Text></Non-Preferred_Term>
        </Terms>
        <Coordinates><Standard>
          <Latitude><Decimal>42.2845</Decimal></Latitude>
          <Longitude><Decimal>-101.1230</Decimal></Longitude>
        </Standard></Coordinates>
        <Place_Types>
          <Preferred_Place_Type><Place_Type_ID>29000/continent</Place_Type_ID></Preferred_Place_Type>
        </Place_Types>
        <Parent_Relationships>
          <Preferred_Parent><Parent_Subject_ID>7029392</Parent_Subject_ID></Preferred_Parent>
        </Parent_Relationships>
      </Subject>
    """
    tgn_id = elem.get("Subject_ID", "")
    if not tgn_id:
        return None

    def ft(paths):
        for path in paths:
            e = elem.find(path)
            if e is not None and e.text:
                return e.text.strip()
        return None

    def fall(path):
        return [e.text.strip() for e in elem.findall(path) if e is not None and e.text]

    # Preferred name
    pref = ft([".//Preferred_Term/Term_Text"])
    # Alt names — actual tag is <Non-Preferred_Term> (singular)
    alts = fall(".//Non-Preferred_Term/Term_Text")

    # Coordinates — decimal value is one level deeper
    lat_str = ft([".//Coordinates/Standard/Latitude/Decimal"])
    lon_str = ft([".//Coordinates/Standard/Longitude/Decimal"])
    try:
        lat = float(lat_str) if lat_str else None
        lon = float(lon_str) if lon_str else None
    except ValueError:
        lat = lon = None

    # Place type — stored as "29000/continent", we keep the readable part
    type_raw = ft([".//Place_Types/Preferred_Place_Type/Place_Type_ID"])
    place_type = type_raw.split("/", 1)[-1] if type_raw else None

    # Parent TGN ID
    parent = ft([".//Parent_Relationships/Preferred_Parent/Parent_Subject_ID"])

    name = pref or ""
    name_norm = ascii_norm(name)
    place_row = (tgn_id, name, name_norm, lat, lon, place_type, parent, None)
    name_rows = [
        (tgn_id, alt, ascii_norm(alt), "")
        for alt in alts if alt and alt != name
    ]
    return place_row, name_rows


def _flush(conn, places_batch, names_batch):
    conn.executemany(
        "INSERT OR REPLACE INTO tgn_place "
        "(tgn_id,pref_name,name_norm,lat,lon,place_type,parent_tgn_id,country_code) "
        "VALUES (?,?,?,?,?,?,?,?)",
        places_batch,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO tgn_name (tgn_id,name,name_norm,lang) VALUES (?,?,?,?)",
        names_batch,
    )
    conn.commit()
    places_batch.clear()
    names_batch.clear()


def import_from_zip(conn: sqlite3.Connection, zip_path: str):
    """Read all *.xml members from a local zip file without extracting to disk."""
    BATCH = 10_000
    places_batch = []
    names_batch  = []
    n = 0
    errors = 0

    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.namelist() if m.endswith(".xml")]
        total = len(members)
        print(f"Found {total:,} XML files in {zip_path}")

        for i, member in enumerate(members):
            try:
                xml_bytes = zf.read(member)
                root = ET.fromstring(xml_bytes)
                # Root may be <Vocabulary> wrapping <Subject>, or bare <Subject>
                tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
                subject = root if tag == "Subject" else root.find(".//{*}Subject") or root.find(".//Subject")
                if subject is None:
                    continue
                result = _parse_subject_elem(subject)
                if result is None:
                    continue
                place_row, name_rows = result
                places_batch.append(place_row)
                names_batch.extend(name_rows)
                n += 1
            except ET.ParseError:
                errors += 1
                continue

            if n % BATCH == 0:
                _flush(conn, places_batch, names_batch)
                pct = (i + 1) / total * 100
                print(f"\r  {n:,} subjects imported ({pct:.1f}%)  errors: {errors}", end="", flush=True)

    _flush(conn, places_batch, names_batch)
    print(f"\r  {n:,} subjects imported (100%)  errors: {errors}          ")


def import_from_dir(conn: sqlite3.Connection, dir_path: str):
    """Read all *.xml files from a directory of individual TGN subject files."""
    BATCH = 10_000
    places_batch = []
    names_batch  = []
    n = 0
    errors = 0

    xml_files = sorted(Path(dir_path).glob("*.xml"))
    total = len(xml_files)
    print(f"Found {total:,} XML files in {dir_path}")

    for i, xml_file in enumerate(xml_files):
        try:
            xml_bytes = xml_file.read_bytes()
            root = ET.fromstring(xml_bytes)
            tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
            subject = root if tag == "Subject" else root.find(".//{*}Subject") or root.find(".//Subject")
            if subject is None:
                continue
            result = _parse_subject_elem(subject)
            if result is None:
                continue
            place_row, name_rows = result
            places_batch.append(place_row)
            names_batch.extend(name_rows)
            n += 1
        except ET.ParseError:
            errors += 1
            continue

        if n % BATCH == 0:
            _flush(conn, places_batch, names_batch)
            pct = (i + 1) / total * 100
            print(f"\r  {n:,} subjects imported ({pct:.1f}%)  errors: {errors}", end="", flush=True)

    _flush(conn, places_batch, names_batch)
    print(f"\r  {n:,} subjects imported (100%)  errors: {errors}          ")


def download_and_import(conn: sqlite3.Connection):
    """Download TGN zip from Getty and import (needs ~600 MB bandwidth)."""
    print(f"Downloading from {TGN_PLACES_URL} …")
    try:
        resp = requests.get(TGN_PLACES_URL, stream=True, timeout=120)
        resp.raise_for_status()
    except Exception as e:
        print(f"Download failed: {e}")
        sys.exit(1)

    total = int(resp.headers.get("Content-Length", 0))
    data = b""
    downloaded = 0
    for chunk in resp.iter_content(chunk_size=1024 * 1024):
        data += chunk
        downloaded += len(chunk)
        if total:
            print(f"\r  {downloaded/1024/1024:.0f} / {total/1024/1024:.0f} MB", end="", flush=True)
    print()

    tmp_zip = Path("cache/_tgn_download.zip")
    tmp_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip.write_bytes(data)
    print(f"Saved to {tmp_zip}, importing…")
    import_from_zip(conn, str(tmp_zip))


def main():
    ap = argparse.ArgumentParser(description="Import Getty TGN into SQLite")
    ap.add_argument("--output", default="cache/tgn.sqlite",
                    help="Output SQLite path (default: cache/tgn.sqlite)")
    ap.add_argument("--from-zip", metavar="PATH",
                    help="Import from a local tgn_xml_*.zip without extracting")
    ap.add_argument("--from-dir", metavar="PATH",
                    help="Import from a directory of individual TGN *.xml files")
    ap.add_argument("--download", action="store_true",
                    help="Download from Getty and import (~600 MB)")
    ap.add_argument("--check", action="store_true",
                    help="Show DB stats only (no import)")
    args = ap.parse_args()

    out = Path(args.output)

    if args.check:
        if not out.exists():
            print(f"Not found: {out}")
            sys.exit(1)
        conn = sqlite3.connect(str(out))
        n_places = conn.execute("SELECT COUNT(*) FROM tgn_place").fetchone()[0]
        n_names  = conn.execute("SELECT COUNT(*) FROM tgn_name").fetchone()[0]
        n_coords = conn.execute("SELECT COUNT(*) FROM tgn_place WHERE lat IS NOT NULL").fetchone()[0]
        print(f"TGN places:       {n_places:,}")
        print(f"TGN with coords:  {n_coords:,}")
        print(f"Alt names:        {n_names:,}")
        conn.close()
        return

    if not args.from_zip and not args.from_dir and not args.download:
        ap.error("Specify one of: --from-zip, --from-dir, --download")

    out.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(out))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _create_schema(conn)

    if args.from_zip:
        import_from_zip(conn, args.from_zip)
    elif args.from_dir:
        import_from_dir(conn, args.from_dir)
    elif args.download:
        download_and_import(conn)

    print("Building indexes and analyzing…")
    conn.execute("ANALYZE")
    conn.close()
    size = out.stat().st_size / 1024 / 1024 / 1024
    print(f"Done. {out}  ({size:.2f} GB)")


if __name__ == "__main__":
    main()
