import argparse
import math
import os
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.local")

import pandas as pd

from .cache import Cache
from .country_db import load_country_dbs
from .explicit_store import ExplicitStore
from .geo_db import GeoDatabase
from .geostack import GeoStack
from .objekttyp import ist_punkt, _klein_norm as _klein_norm_lokal
from .geo_db import ascii_norm, ascii_norm_de
from .input_normalizer import InputNormalizer
from .metrics import TESTFILE
from .models import GeoResult, NormalizedRecord
from .nominatim import Nominatim
from .pictomap_ref import REF_PATH as _PICTOMAP_REF_PATH, PictomapRef
from .wikidata import Wikidata
from .tgn_db import TgnDatabase

_QUALIFIERS = re.compile(
    r'(?i)\b(circa|ca\.?|um|vor|nach|around|about|early|late|'
    r'frühes?|spätes?|anfang|ende|mitte|mid|c\.)\b\s*'
)

def _fmt(year: str, month: str | None = None) -> str | None:
    try:
        y = int(year)
        if not (1000 <= y <= 2100):
            return None
    except (ValueError, TypeError):
        return None
    if month:
        try:
            m = int(month)
            if 1 <= m <= 12:
                return f"{y:04d}:{m:02d}"
        except (ValueError, TypeError):
            pass
    return f"{y:04d}"


def parse_periode_pctm(text) -> str | None:
    """Convert Haiku's Periode string to PCTM date format.

    Rules:
      single point : yyyy  or  yyyy:mm
      range        : yyyy-yyyy  or  yyyy:mm-yyyy:mm  (and mixed)
    Separator year/month = ':', separator start/end = '-'.
    """
    if not text or str(text).strip() in ('', 'nan', 'None'):
        return None
    s = _QUALIFIERS.sub('', str(text)).strip(' .,;')
    if not s:
        return None

    # yyyy:mm-yyyy:mm  (already in target format — normalise)
    m = re.match(r'^(\d{4}):(\d{1,2})\s*[-–]\s*(\d{4}):(\d{1,2})$', s)
    if m:
        a = _fmt(m.group(1), m.group(2))
        b = _fmt(m.group(3), m.group(4))
        return f"{a}-{b}" if a and b else None

    # yyyy-mm-yyyy  or  yyyy-yyyy:mm  (mixed)
    m = re.match(r'^(\d{4})[-/](\d{1,2})\s*[-–]\s*(\d{4})$', s)
    if m and int(m.group(2)) <= 12:
        a = _fmt(m.group(1), m.group(2))
        b = _fmt(m.group(3))
        return f"{a}-{b}" if a and b else None

    m = re.match(r'^(\d{4})\s*[-–]\s*(\d{4})[-/](\d{1,2})$', s)
    if m and int(m.group(3)) <= 12:
        a = _fmt(m.group(1))
        b = _fmt(m.group(2), m.group(3))
        return f"{a}-{b}" if a and b else None

    # yyyy-yyyy  (year range, second part may be 2 or 4 digits)
    # Guard: 2-digit value ≤ 12 is a month (1947-09 → 1947:09, not 1947-1909)
    m = re.match(r'^(\d{4})\s*[-–]\s*(\d{2,4})$', s)
    if m:
        second = m.group(2)
        y1 = int(m.group(1))
        raw = int(second)
        if len(second) == 2 and raw <= 12:
            return _fmt(str(y1), second)   # treat as month: 1947-09 → 1947:09
        y2 = (y1 // 100) * 100 + raw if len(second) == 2 else raw  # 1914-18 → 1918
        if y2 > 12:
            a = _fmt(str(y1))
            b = _fmt(str(y2))
            return f"{a}-{b}" if a and b else None

    # yyyy-mm  (single month — 1-digit: 1947-9 → 1947:09)
    m = re.match(r'^(\d{4})[-/:](\d{1,2})$', s)
    if m and int(m.group(2)) <= 12:
        return _fmt(m.group(1), m.group(2))

    # yyyy  (single year)
    m = re.match(r'^(\d{4})$', s)
    if m:
        return _fmt(m.group(1))

    return None


_EXTRA_DESC_COLS = [c.strip() for c in os.getenv("EXTRA_DESC_COLS", "").split(",") if c.strip()]

def _parse_collection_bbox(raw: str) -> "tuple[float,float,float,float] | None":
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) == 4:
        try:
            return tuple(float(p) for p in parts)
        except ValueError:
            pass
    return None

_COLLECTION_BBOX = _parse_collection_bbox(os.getenv("COLLECTION_BBOX", ""))

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
    """Resolve a canonical ADM1 name for the output Region column.

    Trusts rec.region (from input column or Haiku) when set — normalizes it
    via region_to_admin1 + find_region_name for the canonical spelling
    (e.g. 'Graubünden' → 'Kanton Graubünden'). Falls back to deriving the
    region from rec.city only when rec.region is empty.
    """
    if geo_db is None:
        return rec.region or ""
    country_code = geo_db.country_to_code(rec.country) if rec.country else None
    if rec.region and country_code:
        a1 = geo_db.region_to_admin1(country_code, rec.region)
        if a1:
            canonical = geo_db.find_region_name(country_code, a1)
            if canonical:
                return canonical
        return rec.region
    if geo.lat is None or not rec.city:
        return rec.region or ""
    city_row, _ = geo_db.find_city(rec.city, country_code)
    if not city_row or not city_row["admin1"]:
        return rec.region or ""
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
           true: tuple | None = None, google_total: int = 0, unknown_loc: bool = False):
    score_label = {5: "●●●●●", 4: "●●●●○", 3: "●●●○○", 2: "●●○○○", 0: "○○○○○"}.get(
        geo.quality_score, str(geo.quality_score)
    )
    loc = "/".join(filter(None, [rec.country, rec.city])) or "?"
    fb = " (city fallback)" if geo.fallback else ""
    src = geo.source if geo.source != "none" else "—"
    unk = "  [Lok.unsicher]" if unknown_loc else ""
    amb = "  [?ambig]" if geo.ambiguous else ""
    title = rec.title[:48].ljust(49)
    g_str = f"  [N:{google_total}]" if google_total else ""
    print(f"[{i:>5}/{total}] {title}  {loc:<28}  {score_label}  {src}{fb}{_dev_str(geo, true)}{unk}{amb}{g_str}")


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


# ── Laendernamen auf Deutsch ─────────────────────────────────────────────────
#
# Der Geostack arbeitet intern mit englischen Namen (GeoNames `country_name`).
# In Pictomap wurde `country` am 2026-08-22 auf deutsche Namen vereinheitlicht —
# ein Reimport mit englischen Werten machte das rueckgaengig.
#
# Unbekanntes bleibt unveraendert stehen, statt zu verschwinden: eine Luecke
# faellt beim Sichten auf, ein stilles Weglassen nicht.
#
# ACHTUNG, nicht geloest: fuer ORTSNAMEN gibt es keine solche Liste.  `City`
# hat 1.172 verschiedene Werte — „Firenze" oder „Florenz", „Praha" oder „Prag"
# ist eine offene Frage (Dec, 2026-09-06: „kein Sprachkonzept fuer fremde
# Objekte").  Diese Tabelle loest die Laender, nicht das Problem dahinter.
LAND_DE = {
    "Switzerland": "Schweiz",          "Italy": "Italien",
    "France": "Frankreich",            "Germany": "Deutschland",
    "Liechtenstein": "Liechtenstein",  "Austria": "Österreich",
    "Denmark": "Dänemark",            "Egypt": "Ägypten",
    "South Africa": "Südafrika",      "Greece": "Griechenland",
    "Sweden": "Schweden",              "Algeria": "Algerien",
    "Norway": "Norwegen",              "Czechia": "Tschechien",
    "Tunisia": "Tunesien",             "Monaco": "Monaco",
    "Canada": "Kanada",                "Netherlands": "Niederlande",
    "Belgium": "Belgien",              "Spain": "Spanien",
    "Portugal": "Portugal",            "Poland": "Polen",
    "Hungary": "Ungarn",               "Croatia": "Kroatien",
    "Slovenia": "Slowenien",           "Slovakia": "Slowakei",
    "United Kingdom": "Vereinigtes Königreich",
    "United States": "Vereinigte Staaten",
    "Turkey": "Türkei",               "Morocco": "Marokko",
    "Japan": "Japan",                  "China": "China",
    "Russia": "Russland",              "Finland": "Finnland",
    "Ireland": "Irland",               "Luxembourg": "Luxemburg",
    "Serbia": "Serbien",               "Romania": "Rumänien",
    "Bulgaria": "Bulgarien",           "Ukraine": "Ukraine",
}


def _land_de(name):
    """Englischer Laendername auf Deutsch. Unbekanntes bleibt, wie es ist."""
    if not name:
        return name
    return LAND_DE.get(name.strip(), name)


# ── Gitter: Unschaerferadius in Metern ────────────────────────────────────────
#
# Der Wert sagt, wie weit ein Bild von seiner Koordinate weg gestreut werden
# darf.  Er wandert ueber den Import in das Feld `gitter` auf `images` und
# ersetzt dort die Schaetzung, die der LayerCurator bisher bei jedem Lauf neu
# aus der Einwohnerzahl gezogen hat.  Wer die Koordinate gefunden hat, weiss am
# besten, wie sicher sie ist — deshalb gehoert die Angabe hierher.
#
#     Objekttreffer      100 m   ein benanntes Bauwerk, ein Platz, eine Bruecke
#     Ortsmittelpunkt    CityRadius aus der Einwohnerzahl
#     nichts gefunden    leer    — der Curator nimmt dann CityRadius/2
#
# Die 100 m sind eine Festlegung, keine Messung.  Ein Versuch, sie aus 1016
# menschlich gesetzten Koordinaten abzuleiten, scheiterte am 2026-08-21 daran,
# dass 70 % dieser Referenzen selbst geteilte Sammelpunkte sind (506 Bilder auf
# 200 Koordinaten): der Pruefsatz misst dort, wie gut ein Verfahren einen
# Sammelpunkt reproduziert, und belohnt ausgerechnet den Ortsmittelpunkt.
GITTER_TREFFER_M = 100

_gitter_cache: dict = {}


def _city_radius_m(population: int) -> int:
    """Unschaerfe eines Ortsmittelpunkts, abgeleitet aus der Einwohnerzahl.

    Dieselbe Staffelung wie im LayerCurator (`city_radius_km` × 0.20).  Sie
    loest auf Dorfebene auf, nicht auf Gemeindeebene: Fuldera mit 136
    Einwohnern ergibt 400 m, nicht die Ausdehnung von Val Muestair.
    """
    km = (25.0 if population > 500_000 else
          15.0 if population > 100_000 else
          8.0 if population > 20_000 else
          5.0 if population > 5_000 else
          3.0 if population > 1_000 else 2.0)
    return int(km * 0.20 * 1000)


def _gitter_m(geo: GeoResult, geo_db: "GeoDatabase | None",
              punkt: bool = False) -> "int | None":
    """Der Radius fuer diese Fundstelle — None, wenn es nichts zu streuen gibt.

    `punkt` ist der bereits gefaellte Entscheid aus `ist_punkt()`.  Er wird
    hereingereicht statt hier nochmal gerechnet: derselbe Entscheid darf nicht
    zweimal unabhaengig fallen, sonst koennen `gitter` und
    `Exakte Koordinaten` auseinanderlaufen.
    """
    if geo.lat is None:
        return None
    if geo.gitter_m is not None:
        return geo.gitter_m               # die Quelle kennt ihre Unschaerfe selbst
    if not geo.fallback and geo.quality_score >= 4:
        # Ein punktfoermiges Objekt gilt als praezise verortet — und `gitter = 0`
        # heisst im Curator genau das: „Position bekannt, nicht verschieben".
        # Stuende hier 100, widerspraechen sich in Directus zwei Felder:
        # `localization_is_exact` sagte „nicht bewegen", `gitter` sagte „100 m
        # sind erlaubt".  Der Curator loeste es intern auf, ein Mensch nicht.
        if punkt:
            return 0
        # Kein Punkt, aber ein Treffer: der Schwerpunkt einer Flaeche.  100 m
        # trennen die Bilder optisch, ohne sie weit vom Objekt zu ruecken.
        return GITTER_TREFFER_M
    if not geo.fallback:
        return None                       # Score 0: keine belastbare Fundstelle
    if geo_db is None:
        return None

    # Einwohnerzahl DER STADT, auf die zurueckgefallen wurde — nicht des Orts,
    # der der Koordinate zufaellig am naechsten liegt.
    #
    # Frueher wurde hier der naechstgelegene Ort gesucht und das Ergebnis pro
    # 0,1-Grad-Zelle (~11 km) gemerkt.  In derselben Zelle liegen Zuerich
    # (415.367 Einwohner) und Weiler mit 18; gemessen am 2026-08-25 lagen in 60
    # von 350 Zellen Orte mit Faktor-5-Unterschied.  Zwei Bilder wenige
    # Kilometer auseinander bekamen so 400 und 3.000 Meter.
    #
    # Dabei ist die Stadt in diesem Moment bekannt: `geo.match_name` traegt bei
    # jedem Rueckfall ihren Namen (geostack.py, Zeilen 653/666/680).  Sie wird
    # ueber den Namen nachgeschlagen und, da die Koordinate ihr Mittelpunkt IST,
    # ueber die Naehe eindeutig bestimmt — Homonyme koennen nicht greifen.
    if not geo.match_name:
        return None
    key = geo.match_name.lower()
    if key not in _gitter_cache:
        # UEBER DIE INDIZIERTEN NORMALFORMEN.
        # Eine Abfrage auf `name` greift zwar den Klassenindex fuer
        # feature_class='P', muss danach aber 1,6 Mio. Zeilen nach dem Namen
        # durchsehen: 60 s JE ORTSNAME, gemessen 2026-09-06.  Bei 1.172
        # verschiedenen Orten im ZIN-Bestand waeren das rund 20 Stunden.
        #
        # Drei Schreibvarianten, weil die beiden indizierten Spalten
        # verschieden normalisiert sind und keine einzelne beide trifft:
        #     name_norm        zürichsee    Umlaute bleiben
        #     ascii_name_norm  zuerichsee   ascii_norm_de
        rows = []
        for spalte, wert in (("name_norm",       _klein_norm_lokal(geo.match_name)),
                             ("ascii_name_norm", ascii_norm_de(geo.match_name)),
                             ("ascii_name_norm", ascii_norm(geo.match_name))):
            rows = geo_db.conn.execute(
                f"SELECT population, lat, lon FROM geofeature "
                f"WHERE {spalte} = ? AND feature_class='P' AND population > 0 "
                f"AND feature_code NOT IN ('PPLL','PPLX')",
                (wert,),
            ).fetchall()
            if rows:
                break
        beste = (min(rows, key=lambda r: _haversine_km(geo.lat, geo.lon, r[1], r[2]) or 0)
                 if rows else None)
        _gitter_cache[key] = _city_radius_m(beste[0] if beste else 0)
    return _gitter_cache[key]


# ── Ausgabestruktur: Pictomap-Importfile ─────────────────────────────────────
#
# Die Ausgabe soll ohne Nacharbeit in Pictomap importierbar sein.  Deshalb
# stehen zuerst die Spalten der Importvorlage — in DEREN Reihenfolge und mit
# DEREN Schreibweise — dann `Gitter`, dann die uebrigen Locationer-Spalten.
#
# Vorlage: Massenupload/Zinggeler-Wehrli/ZIN-TI-SG-LU-ZH_UPLOAD_2026-07-19.csv
PICTOMAP_SPALTEN = [
    "Name", "Beschreibung", "Exakte Koordinaten", "Datei", "Periode", "Quelle",
    "Referenz", "Urheber:in", "Land", "Region", "Stadt/Ort", "Breitengrad",
    "Längengrad", "Bemerkungen",
]

# Zielspalte ← Quellspalte im bisherigen Output.  Alle Werte liegen dort bereits
# vor; hier wird nur umbenannt und umsortiert, nichts neu berechnet.
#
# Kommt ein Name doppelt vor (`Periode` und `Region` stehen sowohl in der
# Eingabe als auch im Locationer-Ergebnis), gilt das SPAETERE — also das
# Locationer-Ergebnis.  Sonst waere die Datei beim Reimport wirkungslos.
_UMBENENNEN = {
    "Name":               "Title",
    "Beschreibung":       "Description",
    "Exakte Koordinaten": "Exakte Koordinaten",
    "Datei":              "Datei",
    "Periode":            "Periode",
    "Quelle":             "Quelle",
    "Referenz":           "Referenz",
    "Urheber:in":         "Urheber",
    "Land":               "Country",
    "Region":             "Region",
    "Stadt/Ort":          "City",
    "Breitengrad":        "Lat",
    "Längengrad":         "Lon",
    "Bemerkungen":        "Bemerkungen",
}


def _als_importstruktur(out_df):
    """Spalten in die Reihenfolge und Schreibweise der Importvorlage bringen.

    14 Vorlagenspalten · `Gitter` · alles Uebrige in bisheriger Reihenfolge.
    Es wird ausschliesslich umbenannt und umsortiert.
    """
    def hole(name):
        # Bei doppelten Namen die letzte Spalte nehmen (Locationer schlaegt Eingabe).
        treffer = [i for i, c in enumerate(out_df.columns) if c == name]
        if not treffer:
            return pd.Series([None] * len(out_df))
        return out_df.iloc[:, treffer[-1]].reset_index(drop=True)

    genutzt = set(_UMBENENNEN.values()) | {"Gitter"}
    teile = [hole(quelle).rename(ziel) for ziel, quelle in _UMBENENNEN.items()]
    teile.append(hole("Gitter").rename("Gitter"))

    # Durchgereichte Eingabespalten, deren Name schon vergeben ist, bekommen ein
    # Suffix.  Zwei gleichnamige Spalten sind im CSV zwar erlaubt, aber der
    # Import koennte die falsche erwischen — und damit die alte Koordinate statt
    # der geokodierten nehmen.
    vergeben = {s.name for s in teile}
    for i, spalte in enumerate(out_df.columns):
        if spalte in genutzt:
            continue
        name = spalte
        if name in vergeben:
            name = f"{spalte}_original"
            n = 2
            while name in vergeben:
                name, n = f"{spalte}_original{n}", n + 1
        vergeben.add(name)
        teile.append(out_df.iloc[:, i].reset_index(drop=True).rename(name))
    return pd.concat(teile, axis=1)


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

    # Apply norm_overrides: correct city/country/location extracted by Phase 1 AI
    if geostack.overrides:
        from .input_normalizer import _map_columns
        for rec, row in zip(normalized, chunk_rows):
            # Die Stadt, die schon dasteht: aus der Eingabe, sonst von Haiku.
            # Ein Override darf sie praezisieren, nicht ersetzen (explicit_store.py).
            stadt = _map_columns(row, _EXTRA_DESC_COLS)["city"] or rec.city
            correction = geostack.overrides.match_norm(_raw_text(row), stadt)
            if correction:
                # Country acts as both prerequisite and fill-in: skip the entire
                # override if the record already has a different country.
                target_country = correction.get("country", "")
                if target_country and rec.country and \
                        rec.country.lower() != target_country.lower():
                    if mode == "debug":
                        print(f"  [norm override] skipped (country {rec.country!r} ≠ {target_country!r})")
                    continue
                if "city" in correction:
                    rec.city = correction["city"]
                    if mode == "debug":
                        print(f"  [norm override] city → {correction['city']!r}")
                if "location" in correction:
                    rec.location = correction["location"]
                    if mode == "debug":
                        print(f"  [norm override] location → {correction['location']!r}")
                if target_country and not rec.country:
                    rec.country = target_country

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

        # Stadt ergaenzen, wenn sie fehlte und ein Objekt gefunden wurde
        # (Suchraum Region/Land, geostack.py).  Naechster Ort in derselben
        # Region — „Schloss Chillon" → Veytaux.
        if not rec.city and geo.lat is not None and not geo.fallback and geo_db:
            _cc = geo_db.country_to_code(rec.country) if rec.country else None
            _a1 = geo_db.region_to_admin1(_cc, rec.region) if (_cc and rec.region) else None
            _ort = geo_db.naechster_ort(geo.lat, geo.lon, _cc, _a1)
            if _ort:
                rec.city = _ort
                geo.debug_info.append(f"Stadt ergaenzt: {_ort}")

        true = _true_coords(row) if has_true_coords else None
        dev_km = _haversine_km(geo.lat, geo.lon, true[0], true[1]) if (true and geo.lat is not None) else None
        region = _get_region(geo_db, rec, geo)

        unknown = _has_unknown_location(row)
        if mode == "human":
            _human(i, total, rec, geo, true, google_total=geostack.ext_count, unknown_loc=unknown)
        else:
            _debug(i, total, rec, geo, true)

        # Einmal entscheiden, zweimal verwenden — fuer `Gitter` und fuer
        # `Exakte Koordinaten`.  Siehe objekttyp.py.
        _punkt = ist_punkt(geo, geostack)

        out = {
            "Title": rec.title,
            "Description": rec.description,
            "Periode": parse_periode_pctm(meta.get("periode")),
            "Urheber": meta.get("urheber"),
            "Technik": meta.get("technik"),
            "Country": _land_de(rec.country),
            "Region": region,
            "City": rec.city,
            "Lat": geo.lat,
            "Lon": geo.lon,
            "Coord-Quality-Score": geo.quality_score,
            "Fallback": geo.fallback,
            "Gitter": _gitter_m(geo, geo_db, _punkt),
            # Praezise heisst: ein PUNKTFOERMIGES Objekt wurde gefunden.
            # Der Typ kommt aus der Quelle selbst (siehe objekttyp.py), nicht
            # aus Stichwoertern im Namen — sonst haenge es an der Sprache.
            # Ueberschreibt die durchgereichte Eingabespalte gleichen Namens,
            # weil `_als_importstruktur()` die SPAETERE Spalte nimmt.
            "Exakte Koordinaten": _punkt,
            "Ambiguous": geo.ambiguous,
            "Ext-Calls": 1 if (geo.source in ("wikidata","nominatim") and geo.debug_info != ["cache hit"]) else 0,
        }
        if has_true_coords:
            out["Deviation_km"] = round(dev_km, 2) if dev_km is not None else None
        if geo.lat is not None:
            out["Map"] = f"https://www.openstreetmap.org/?mlat={geo.lat}&mlon={geo.lon}&zoom=14"
        else:
            out["Map"] = None
        output_rows.append(out)
    return output_rows, unknown_loc


def _spaltenbericht(df) -> None:
    """Vor dem Lauf zeigen, welche der fünf Felder in der Eingabe gefunden wurden.

    Der Locationer liest NUR diese fünf Felder; alles andere wird unverändert
    durchgereicht.  Die Erkennung ist exakter Namensvergleich — „Bildtitel"
    statt „Titel" und das Feld bleibt leer.  Ohne diese Ausgabe fällt das
    nirgends auf: der Lauf geht stundenlang durch und findet nichts.
    """
    from .input_normalizer import COLUMN_MAP

    vorhanden = {str(c).lower().strip(): str(c) for c in df.columns}
    genutzt: dict[str, str] = {}
    verdeckt: list[tuple[str, str]] = []
    for feld, aliase in COLUMN_MAP.items():
        treffer = [a for a in aliase if a in vorhanden]
        if treffer:
            genutzt[feld] = vorhanden[treffer[0]]
            verdeckt += [(feld, vorhanden[a]) for a in treffer[1:]]

    print("\nEINGABESPALTEN — gelesen wird nur, was exakt so heisst (Gross/Klein egal):")
    for feld, aliase in COLUMN_MAP.items():
        if feld in genutzt:
            print(f"  {feld:<12} ← „{genutzt[feld]}\"")
        else:
            print(f"  {feld:<12} FEHLT    mögliche Namen: {', '.join(aliase)}")

    for feld, name in verdeckt:
        print(f"  Hinweis: „{name}\" wird NICHT gelesen — „{genutzt[feld]}\" "
              f"hat Vorrang für {feld}")

    getroffen = {a for aliase in COLUMN_MAP.values() for a in aliase if a in vorhanden}
    rest = [vorhanden[k] for k in vorhanden if k not in getroffen]
    if rest:
        zeige = ", ".join(rest[:8]) + (f" … (+{len(rest) - 8})" if len(rest) > 8 else "")
        print(f"  durchgereicht, nicht ausgewertet: {zeige}")
        print("  → einzelne davon der Beschreibung zufüttern: EXTRA_DESC_COLS in .env")

    if not ({"city", "title", "description"} & set(genutzt)):
        print("\n  ACHTUNG: weder city noch title noch description erkannt.")
        print("  Der Lauf findet so gut wie nichts. Spaltennamen prüfen, bevor es weitergeht.")


def main():
    ap = argparse.ArgumentParser(description="Locationer – geocoding pipeline")
    ap.add_argument("input", help="Input CSV or XLSX file")
    ap.add_argument("--mode", choices=["human", "debug"], default="human")
    ap.add_argument("--output", "-o", help="Output CSV (default: <input>_loc-<datum>.csv)")
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

    ist_testdatei = Path(args.input).resolve() == Path(TESTFILE).resolve()
    if args.output:
        out_path = args.output
    elif ist_testdatei:
        # Fester Name, kein Datum -- die Rauchprobe baut sich ueber viele
        # kleine --limit-Laeufe (Chunk-Resume) zu einer vollen Messung gegen
        # alle 45 Testzeilen auf; ein taeglich wechselnder Name wuerde das
        # jeden Tag auf 0 zuruecksetzen (siehe description.md, Falle Testdatei).
        out_path = str(Path(args.input).with_suffix("")) + "_geo.csv"
    else:
        out_path = f"{Path(args.input).with_suffix('')}_loc-{date.today().isoformat()}.csv"

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

    print(f"Locationer  ·  {total} records  ·  mode={args.mode}"
          + (f"  ·  chunk={args.chunk_size}" if args.chunk_size else "")
          + (f"  ·  ab Zeile {skip_rows + 1}" if skip_rows else ""))

    _spaltenbericht(df)

    # COLLECTION_BBOX check
    global _COLLECTION_BBOX
    if _COLLECTION_BBOX:
        mn_lat, mx_lat, mn_lon, mx_lon = _COLLECTION_BBOX
        print(f"\nCollection-Bbox: {mn_lat},{mx_lat},{mn_lon},{mx_lon}")
        answer = input("  Passt das für diese Sammlung? [Y/n/-=ohne Filter/neuer Wert] ").strip()
        if answer.lower() == "n":
            sys.exit(f"Abgebrochen — bitte COLLECTION_BBOX in {Path('.env').resolve()} anpassen.")
        elif answer == "-":
            _COLLECTION_BBOX = None
            print("  → Kein Schwerpunktfilter für diesen Lauf.")
        elif answer and answer.lower() not in ("y", "j", ""):
            new_bbox = _parse_collection_bbox(answer)
            if new_bbox:
                _COLLECTION_BBOX = new_bbox
                print(f"  → Bbox für diesen Lauf: {answer}")
            else:
                print("  → Ungültiges Format, bestehende Bbox wird verwendet.")
    else:
        print("\nCollection-Bbox: nicht gesetzt (global — kein Schwerpunktfilter)")
        answer = input("  COLLECTION_BBOX setzen? [Enter=weiter / min_lat,max_lat,min_lon,max_lon] ").strip()
        if answer:
            new_bbox = _parse_collection_bbox(answer)
            if new_bbox:
                _COLLECTION_BBOX = new_bbox
                print(f"  → Bbox für diesen Lauf: {answer}")
    print()

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
        print(f"TGN DB:  not found (run python -m locationer.import_tgn to build)")

    # Country DBs (optional — auto-loaded from COUNTRY_DB_XX env vars)
    country_dbs = load_country_dbs(dict(os.environ))
    if country_dbs:
        print(f"Country DBs: {', '.join(sorted(country_dbs.keys()))}")
    else:
        print("Country DBs: none configured (set COUNTRY_DB_CH etc. in .env)")

    # Pictomap-Referenz (optional — aufbauen mit: python -m locationer.pictomap_ref sync)
    pictomap_ref = None
    if os.getenv("PICTOMAP_REF", "1") != "0" and Path(_PICTOMAP_REF_PATH).exists():
        pictomap_ref = PictomapRef(_PICTOMAP_REF_PATH)
        _last = pictomap_ref.conn.execute(
            "SELECT value FROM meta WHERE key='last_sync'").fetchone()
        print(f"Pictomap-Referenz: {pictomap_ref.count()} Punkte"
              f"  ·  Abgleich {_last[0] if _last else '—'}")
    else:
        print("Pictomap-Referenz: aus (python -m locationer.pictomap_ref sync)")

    import sqlite3 as _sqlite3
    _ext_conn = _sqlite3.connect(_CACHE_DEFAULT)
    nominatim = Nominatim(_ext_conn, base_url=_NOMINATIM_URL, user_agent=_NOMINATIM_UA,
                          debug=(args.mode == "debug"))
    normalizer = InputNormalizer(cache, debug=(args.mode == "debug"), tgn_db=tgn_db, geo_db=geo_db,
                                extra_desc_cols=_EXTRA_DESC_COLS)
    # Abschaltbar: faellt die Wikidata-Schnittstelle aus, laeuft der Rest weiter.
    # Dient ausserdem dem sauberen Vergleich zweier Laeufe (WIKIDATA=0).
    wikidata   = (Wikidata(_ext_conn, debug=(args.mode == "debug"))
                  if os.getenv("WIKIDATA", "1") != "0" else None)
    geostack   = GeoStack(geo_db, cache, nominatim,
                          debug=(args.mode == "debug"), overrides=overrides,
                          tgn_db=tgn_db, collection_bbox=_COLLECTION_BBOX,
                          country_dbs=country_dbs, wikidata=wikidata,
                          pictomap_ref=pictomap_ref)

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

        out_df = pd.concat(
            [chunk_df.reset_index(drop=True), pd.DataFrame(output_rows)],
            axis=1,
        )
        out_df = _als_importstruktur(out_df)
        # `Gitter` ist ganzzahlig (Meter).  Enthaelt die Spalte Leerwerte, macht
        # pandas daraus float64 und schreibt „400.0" — das Zielfeld in Pictomap
        # ist ein Integer.  Int64 ist der nullbare Ganzzahltyp: leer bleibt leer.
        if "Gitter" in out_df.columns:
            out_df["Gitter"] = pd.to_numeric(out_df["Gitter"], errors="coerce").astype("Int64")
        out_df.to_csv(
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
    print(f"Pictomap-Referenz Treffer: {geostack.pm_count}")
    _print_stats(all_output_rows, total_unknown_loc)
    print()

    cache.close()
    overrides.close()
    if pictomap_ref:
        pictomap_ref.close()
    _ext_conn.close()
    if geo_db:
        geo_db.close()
    if tgn_db:
        tgn_db.close()

    # Auto-generate QA map
    try:
        from locationer.map import main as map_main
        import sys as _sys
        _argv = _sys.argv
        _sys.argv = ["locationer.map", out_path]
        map_main()
        _sys.argv = _argv
    except Exception as e:
        print(f"Karte nicht generiert: {e}")

    from locationer.metrics import measure, to_row, update_log, print_table
    if ist_testdatei:
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
