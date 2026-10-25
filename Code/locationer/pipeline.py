"""Verarbeitung eines Pakets — gemeinsam fuer Terminal, Arbeiter und API.

Bis 2026-10-25 stand das in `__main__.py`.  Herausgeloest, damit der Arbeiter
auf dem Server (worker.py) und die Sofort-API (web.py) genau denselben Weg
gehen wie der Terminal-Lauf.  `Umgebung` baut alle Datenbanken und Dienste
fuer EINEN Thread; parallel laufende Pakete haben je eine eigene.
"""
import math
import os
import re
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.local")

import pandas as pd

from .cache import Cache, verbinde
from .country_db import load_country_dbs
from .explicit_store import ExplicitStore
from .geo_db import GeoDatabase
from .geostack import GeoStack
from .objekttyp import ist_punkt, _klein_norm as _klein_norm_lokal
from .geo_db import ascii_norm, ascii_norm_de
from .input_normalizer import InputNormalizer
from .kandidaten import Kandidatenpruefer
from .nachfassen import Nachfasser
from .input_normalizer import _woerter
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
    # Der Python-CSV-Leser bricht bei Feldern ueber 128 KB ab.  DISCO hat eine
    # Beschreibung mit 180000 Zeichen (Napoléon III, eine ganze Biografie);
    # beide Leser zaehlen damit dieselben 705224 Zeilen.
    import csv
    csv.field_size_limit(sys.maxsize)
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
    print(f"  city_source={rec.city_source!r}  location_kind={rec.location_kind!r}  image_type={rec.image_type!r}"
          + (f"  conflict={rec.conflict!r}" if rec.conflict else ""))
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
    nachfasser=None,
    ausgeben: bool = True,
    pruefer=None,
) -> tuple[list[dict], int, list[int]]:
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

    # Geostack fuer den ganzen Chunk vorab, damit Schritt 2 die Fehlschlaege
    # gesammelt nachfassen kann (nachfassen.py).
    unbekannt = [_has_unknown_location(row) for row in chunk_rows]
    geos = []
    for rec, unb in zip(normalized, unbekannt):
        if unb:
            # Skip geocoding when input explicitly states location is unknown
            g = GeoResult()
            g.debug_info = ["skipped: location unknown in input"]
        else:
            g = geostack.geocode(rec)
        geos.append(g)
    # Schritt 1.5: kein genauer Treffer, aber ein Objekt genannt → Kandidaten
    # pruefen (kandidaten.py).  Vor Schritt 2, damit Opus nur noch bekommt,
    # was danach ganz ohne Koordinate ist.
    if pruefer:
        pruefer.pruefen(normalized, geos, unbekannt)
    if nachfasser:
        nachfasser.nachfassen(normalized, geos, unbekannt)

    # Schlusskontrolle (dritte Sicherung gegen vertauschte Zeilen, nach
    # Nummer und Echo in input_normalizer._zuordnen): Haikus Titel muss ein
    # Wort mit Titel oder Beschreibung SEINER Eingabezeile teilen.  Meldet
    # auch Uebersetzungen („Londres" → „London") — lieber ein Fehlalarm zu
    # viel als eine stille Verwechslung.
    verdacht: list[int] = []     # Zeilennummern, deren Ergebnis nicht zur Eingabe passt
    for k, rec in enumerate(normalized):
        eigen = _woerter(rec.title)
        quelle = _woerter(rec.input_title) | _woerter(rec.description)
        # „Wolkenansicht" aus „Wolken, Ansicht": Teilwoerter zaehlen mit.
        if eigen and quelle and not any(a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a))
                                        for a in eigen for b in quelle):
            verdacht.append(chunk_start + k + 1)

    output_rows = []
    unknown_loc = sum(unbekannt)
    for i, (rec, row, meta, geo) in enumerate(zip(normalized, chunk_rows, metadata, geos), chunk_start + 1):

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
        if not ausgeben:
            pass
        elif mode == "human":
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
            # Phase 1a: Bildtyp (Porträts spaeter nicht teuer nachfassen) und
            # Widerspruch Eingabe ↔ Wissen als Pruefliste.  Eigene Spalten —
            # `Bemerkungen` gehoert der Importvorlage und wird durchgereicht.
            "Bildtyp": rec.image_type,
            "Widerspruch": rec.conflict,
            # Schritt 2: wer identifiziert hat und woher die Koordinate kommt.
            "Nachgefasst": rec.nachgefasst,
            "Ext-Calls": 1 if (geo.source in ("wikidata","nominatim") and geo.debug_info != ["cache hit"]) else 0,
        }
        if has_true_coords:
            out["Deviation_km"] = round(dev_km, 2) if dev_km is not None else None
        if geo.lat is not None:
            out["Map"] = f"https://www.openstreetmap.org/?mlat={geo.lat}&mlon={geo.lon}&zoom=14"
        else:
            out["Map"] = None
        output_rows.append(out)
    return output_rows, unknown_loc, verdacht


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
        print(f"  Hinweis: „{name}\" wird nur gelesen, wo „{genutzt[feld]}\" "
              f"leer ist ({feld})")

    getroffen = {a for aliase in COLUMN_MAP.values() for a in aliase if a in vorhanden}
    rest = [vorhanden[k] for k in vorhanden if k not in getroffen]
    if rest:
        zeige = ", ".join(rest[:8]) + (f" … (+{len(rest) - 8})" if len(rest) > 8 else "")
        print(f"  durchgereicht, nicht ausgewertet: {zeige}")
        print("  → einzelne davon der Beschreibung zufüttern: EXTRA_DESC_COLS in .env")

    if not ({"city", "title", "description"} & set(genutzt)):
        print("\n  ACHTUNG: weder city noch title noch description erkannt.")
        print("  Der Lauf findet so gut wie nichts. Spaltennamen prüfen, bevor es weitergeht.")


def _zelle(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    s = str(v).strip()
    try:
        return repr(float(s))       # „1865" und „1865.0" sind dieselbe Zelle
    except ValueError:
        return s


class Umgebung:
    """Alles, was ein Paket braucht — fuer GENAU EINEN Thread.

    SQLite-Verbindungen duerfen nicht zwischen Threads wandern; darum baut
    jeder parallele Arbeiter seine eigene Umgebung.  Gemeinsam sind nur die
    Kasse (kasse.py) und der Takt von Nominatim/Wikidata (prozessweit).
    """

    def __init__(self, *, kasse=None, bbox=None, extra_desc_cols=None, mode: str = "human",
                 geo_db_pfad: str = _GEO_DB_DEFAULT, nachfassen: "bool | None" = None,
                 fortschritt: bool = True):
        debug = mode == "debug"
        self.mode = mode
        self.cache = Cache(_CACHE_DEFAULT)
        self.overrides = ExplicitStore(_OVERRIDES_DEFAULT)
        self.geo_db = GeoDatabase(geo_db_pfad) if Path(geo_db_pfad).exists() else None
        self.tgn_db = TgnDatabase(_TGN_DB_DEFAULT) if Path(_TGN_DB_DEFAULT).exists() else None
        self.country_dbs = load_country_dbs(dict(os.environ))
        self.pictomap_ref = (PictomapRef(_PICTOMAP_REF_PATH)
                             if os.getenv("PICTOMAP_REF", "1") != "0" and Path(_PICTOMAP_REF_PATH).exists()
                             else None)
        self._ext_conn = verbinde(_CACHE_DEFAULT)
        nominatim = Nominatim(self._ext_conn, base_url=_NOMINATIM_URL, user_agent=_NOMINATIM_UA, debug=debug)
        self.normalizer = InputNormalizer(self.cache, debug=debug, tgn_db=self.tgn_db, geo_db=self.geo_db,
                                          extra_desc_cols=list(extra_desc_cols if extra_desc_cols is not None
                                                               else _EXTRA_DESC_COLS),
                                          kasse=kasse)
        self.normalizer.fortschritt = fortschritt
        # Abschaltbar: faellt die Wikidata-Schnittstelle aus, laeuft der Rest weiter.
        wikidata = Wikidata(self._ext_conn, debug=debug) if os.getenv("WIKIDATA", "1") != "0" else None
        self.geostack = GeoStack(self.geo_db, self.cache, nominatim, debug=debug, overrides=self.overrides,
                                 tgn_db=self.tgn_db, collection_bbox=bbox, country_dbs=self.country_dbs,
                                 wikidata=wikidata, pictomap_ref=self.pictomap_ref)
        if nachfassen is None:
            nachfassen = os.getenv("NACHFASSEN", "1") != "0"
        # Schritt 2 (nachfassen.py): Fehlschlaege per Triage + Opus nachfassen.
        self.nachfasser = Nachfasser(self.normalizer, self.geostack, self.cache, debug=debug) if nachfassen else None
        # Schritt 1.5 (kandidaten.py): unscharfe Namen ueber Wikidata-Kandidaten + Haiku.
        self.pruefer = (Kandidatenpruefer(self.normalizer, self.geostack, self.cache, debug=debug)
                        if wikidata and os.getenv("KANDIDATEN", "1") != "0" else None)

    def zaehler(self) -> dict:
        n = self.nachfasser
        return {"haiku": self.normalizer.haiku_count, "nominatim": self.geostack.ext_count,
                "pictomap": self.geostack.pm_count,
                "nachgefasst_kandidaten": n.kandidaten if n else 0,
                "nachgefasst_bestimmbar": n.bestimmbar if n else 0,
                "nachgefasst_gefunden": n.gefunden if n else 0,
                "kandidaten_geprueft": self.pruefer.geprueft if self.pruefer else 0,
                "kandidaten_gefunden": self.pruefer.gefunden if self.pruefer else 0}

    def schliessen(self) -> None:
        for db in (self.cache, self.overrides, self.pictomap_ref, self.geo_db, self.tgn_db,
                   *self.country_dbs.values()):
            if db is not None:
                db.close()
        self._ext_conn.close()


def paket(u: Umgebung, chunk_df: pd.DataFrame, chunk_start: int, total: int,
          has_true_coords: bool, ausgeben: bool = True) -> "tuple[pd.DataFrame, int, list[int]]":
    """Ein Paket rechnen → (Ausgabezeilen in Importstruktur, Ort unbekannt, Verdacht)."""
    chunk_rows = chunk_df.where(pd.notna(chunk_df), None).to_dict("records")
    output_rows, unknown_loc, verdacht = _process_chunk(
        chunk_rows, chunk_start, total, u.normalizer, u.geostack, u.geo_db,
        has_true_coords, u.mode, u.nachfasser, ausgeben, u.pruefer)
    out_df = pd.concat([chunk_df.reset_index(drop=True), pd.DataFrame(output_rows)], axis=1)
    out_df = _als_importstruktur(out_df)
    # `Gitter` ist ganzzahlig (Meter).  Enthaelt die Spalte Leerwerte, macht
    # pandas daraus float64 und schreibt „400.0" — das Zielfeld in Pictomap
    # ist ein Integer.  Int64 ist der nullbare Ganzzahltyp: leer bleibt leer.
    if "Gitter" in out_df.columns:
        out_df["Gitter"] = pd.to_numeric(out_df["Gitter"], errors="coerce").astype("Int64")
    return out_df, unknown_loc, verdacht
