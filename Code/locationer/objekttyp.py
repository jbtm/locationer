"""Punkt oder Fläche — beantwortet aus dem Typ, den die Quelle selbst mitführt.

Die Frage lautet: hat der Locationer ein PUNKTFÖRMIGES Objekt gefunden (ein
Bauwerk, einen Gipfel) oder den Schwerpunkt einer FLÄCHE (einen See, ein Tal,
einen Ortsmittelpunkt)?  Nur im ersten Fall ist die Koordinate eine Verortung;
im zweiten ist sie eine Sammelstelle, und dafür gibt es `gitter`.

WARUM NICHT ÜBER STICHWÖRTER IM NAMEN
─────────────────────────────────────
Eine Wortliste („see", „tal", „berg") hängt an der Sprache und muss mit jedem
neuen Bestand wachsen.  Sie scheitert schon an deutschen Zusammensetzungen:
„Zürichsee" ist ein Token, nicht „Zürich" + „see".

Jede Quelle führt den Typ aber selbst — strukturiert und sprachunabhängig.
„Lago di Como" und „Zürichsee" sind beide `H.LK`.  Diese Vokabulare sind
endlich und stabil; eine neue Sprache im Bestand ändert an ihnen nichts.

    GeoNames    feature_class + feature_code    S.CSTL · H.LK · T.PK
    Nominatim   place_type                      castle · lake · peak
    TGN         place_type                      castle · lake · peak
    Wikidata    —                               nur label gespeichert

KEIN NEUES FELD, KEINE NEU-GEOKODIERUNG
───────────────────────────────────────
Der Typ wird beim Schreiben der Zeile nachgeschlagen, aus denselben
persistenten Speichern, aus denen die Koordinate kam.  Schlüssel ist, was
`GeoResult` ohnehin trägt: `source`, `match_name`, `lat`, `lon`.  Dasselbe
Muster benutzt `_gitter_m()` schon heute.

DIE VORGABE (Dec, 2026-09-06)
─────────────────────────────
    Punkt    Bauwerke — Schloss, Bahnhof, Kirche, Hotel, Brücke
             Gipfel und Pässe
    Fläche   ALLE Siedlungen, vom Weiler bis zur Stadt
             Gewässer · Täler, Schluchten, Gebirgszüge
             Inseln · Parks, Regionen, Verwaltungseinheiten

Siedlungen sind ausnahmslos Fläche.  Das kommt ohne Größenschwelle aus: der
Unterschied zwischen Fuldera und Zürich steckt sauber im `gitter`-Radius,
nicht in einer willkürlichen Grenze zwischen Weiler und Dorf.

**Unbekanntes gilt als Fläche.**  Ein unbekannter Typ darf keine Genauigkeit
behaupten, die niemand geprüft hat.  Die Vorgabe lautet: lieber weniger
behaupten als etwas Unwahres.
"""
from __future__ import annotations

import math
import unicodedata

from .geo_db import ascii_norm, ascii_norm_de


# ── GeoNames ─────────────────────────────────────────────────────────────────
# Klasse S ist die Klasse der punktfoermigen Dinge: Bauwerke, Anlagen, Bahnhoefe.
# Aus Klasse T (Terrain) sind nur die Gipfel und Paesse Punkte — ein Gebirgszug
# (MTS), ein Tal (VAL) oder eine Schlucht (GRGE) sind Flaechen.
_GEONAMES_PUNKT_KLASSEN = {"S"}
_GEONAMES_PUNKT_CODES = {
    "PK",    # Gipfel
    "MT",    # Berg
    "HLL",   # Huegel
    "VLC",   # Vulkan
    "PASS",  # Pass — ein definierter Punkt auf einem Weg
}


# ── Nominatim (OpenStreetMap) ────────────────────────────────────────────────
# Aufgezaehlt sind die PUNKTE; alles Uebrige gilt als Flaeche.  Grundlage sind
# die 291 Typen, die im ZIN-Bestand tatsaechlich vorkommen.
_NOMINATIM_PUNKT = {
    # Sakralbauten und historische Objekte
    "place_of_worship", "church", "chapel", "monastery", "shrine", "cathedral",
    "castle", "fort", "manor", "palace", "ruins", "archaeological_site",
    "monument", "memorial", "tomb", "wayside_cross", "wayside_shrine",
    "city_gate", "tower", "windmill", "watermill",
    # Beherbergung und Gastronomie
    "hotel", "hostel", "guest_house", "chalet", "alpine_hut", "wilderness_hut",
    "hut", "restaurant", "cafe", "bar", "pub", "biergarten",
    # Verkehrsbauten
    "station", "train_station", "halt", "stop", "bus_stop", "platform",
    "funicular", "aerialway", "station_entrance", "ferry_terminal", "pier",
    "bridge", "viaduct", "tunnel", "lighthouse", "aerodrome", "helipad",
    # Oeffentliche Bauten und Einrichtungen
    "townhall", "government", "public", "school", "university", "college",
    "hospital", "clinic", "museum", "theatre", "cinema", "library",
    "community_centre", "social_facility", "post_office", "bank", "police",
    "fire_station", "courthouse", "prison", "embassy",
    # Gebaeude allgemein
    "yes", "house", "detached", "apartments", "apartment", "residential_building",
    "commercial_building", "farmhouse", "barn", "chapel_building",
    # Einzelne Objekte im Freien
    "fountain", "artwork", "statue", "obelisk", "cross", "bench",
    "viewpoint", "attraction", "guidepost", "board", "information",
    "picnic_site", "drinking_water", "toilets", "defibrillator",
    "disaster_help_point", "camp_site", "parking", "playground", "pitch",
    "sports_centre", "swimming_pool", "cave_entrance", "spring", "well",
    "mine", "mineshaft", "quarry_point", "rock", "boulder", "tree",
    # Gelaendepunkte
    "peak", "saddle", "volcano", "waterfall", "rapids", "ford",
}


# ── TGN (Getty Thesaurus of Geographic Names) ────────────────────────────────
# Wie bei Nominatim: aufgezaehlt sind die PUNKTE.
_TGN_PUNKT = {
    "building", "buildings", "church", "cathedral", "chapel", "monastery",
    "abbey", "temple", "shrine", "mosque", "synagogue",
    "castle", "fortification", "fort", "palace", "country house", "villa",
    "ruins", "archaeological site", "monument", "memorial", "tomb", "tomb(s)",
    "tower", "windmill", "mill",
    "railroad station", "station", "transport point", "bridge", "tunnel",
    "lighthouse", "airport", "harbor", "port",
    "school", "university", "hospital", "museum", "theater", "library",
    "hotel", "inn",
    "peak", "mountain", "hill", "volcano", "pass", "point",
    "spring", "well", "cave", "waterfall", "rock", "mine",
}


def _klein_norm(s: str) -> str:
    """Wie `name_norm` in der Datenbank: klein, ohne Punkte, Umlaute BLEIBEN."""
    s = unicodedata.normalize("NFC", (s or "").lower())
    return " ".join(s.replace(".", "").replace("/", " ").split())


# ── Letzter Rueckfall: der naechstgelegene GeoNames-Eintrag ──────────────────
#
# Fuer Quellen, die keinen eigenen Typ fuehren — Wikidata und die Laender-DBs.
# Der Gedanke ist Decs: Wikidata hat den Ort ueber einen Aliasnamen gefunden
# („Bahnhof Pontresina"), unter dem ihn GeoNames nicht kennt.  Der NAME geht
# also fehl, die KOORDINATE nicht — beide zeigen auf dasselbe reale Objekt.
#
#     Bahnhof Pontresina  →  Pontresina station          S.RSTN   12 m
#     Bahnhof St. Moritz  →  St. Moritz Railway Station  S.RSTN   58 m
#
# ZWEI REGELN MACHEN DAS BELASTBAR
#
# 1. Der NAECHSTGELEGENE zaehlt, nicht der erstbeste.  Ein erster Entwurf ging
#    die Klassen in fester Reihenfolge durch (S zuerst) — das bevorzugt
#    systematisch „Punkt", wenn ein Bauwerk 60 m neben einem Ortsmittelpunkt
#    liegt.
#
# 2. Enger Radius.  Gemessen am 2026-09-06 ueber 852 Wikidata-Treffer, wie oft
#    das Urteil kippte, haette man den zweitnaechsten genommen:
#
#        15 m   11 % Abdeckung    5 % wackelig
#        60 m   39 %              8 %      ← gewaehlt
#       100 m   55 %             15 %
#       150 m   68 %             21 %
#
#    Ueber 60 m trifft die Abfrage, was zufaellig danebensteht, nicht das
#    gesuchte Objekt: „Maggiadelta" fand ein Bauernhaus in 119 m, „rifugio
#    Tabaretta" einen Gipfel in 101 m.
NAH_M = 60


def _naechster_geonames(conn, lat, lon, radius_m=NAH_M):
    """Der naechstgelegene Eintrag im Umkreis — oder None."""
    d = radius_m / 111000.0
    dlon = d / max(math.cos(math.radians(lat)), 0.01)
    beste = None
    for klasse in ("S", "T", "H", "L", "V", "P", "A", "U"):
        for fc, code, la, lo in conn.execute(
                "SELECT feature_class, feature_code, lat, lon FROM geofeature "
                "WHERE feature_class=? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                (klasse, lat - d, lat + d, lon - dlon, lon + dlon)):
            p = math.pi / 180
            dist = 2 * 6371000.0 * math.asin(math.sqrt(
                math.sin((la - lat) * p / 2) ** 2 + math.cos(lat * p) * math.cos(la * p)
                * math.sin((lo - lon) * p / 2) ** 2))
            if dist <= radius_m and (beste is None or dist < beste[0]):
                beste = (dist, fc, code)
    return beste


def _hole(conn, sql, args):
    try:
        return conn.execute(sql, args).fetchone()
    except Exception:
        return None


def ist_punkt(geo, geostack) -> bool:
    """True, wenn die Fundstelle ein punktfoermiges Objekt ist.

    Schlaegt den Typ dort nach, woher die Koordinate kam.  Bei Unsicherheit —
    kein Treffer, unbekannte Quelle, unbekannter Typ — False.
    """
    if geo.lat is None or geo.fallback:
        return False

    quelle = (geo.source or "").lower()

    # ── Pictomap-Referenz: nie exakt ─────────────────────────────────────────
    # Der Punkt ist fuer ein ANDERES Bild bestaetigt worden.  Fuer dieses ist
    # er eine sehr gute Schaetzung, aber keine Bestaetigung — die Unschaerfe
    # steht in `geo.gitter_m` (Streuung der Referenzpunkte).
    if quelle.startswith("pictomap_"):
        return False

    # ── GeoNames: ueber den Namen, ueber die Naehe eindeutig gemacht ──────────
    # Die Koordinate IST der Mittelpunkt des Treffers, deshalb kann ein
    # Homonym nicht dazwischenkommen.
    if quelle in ("geo_db", "country_db"):
        db = getattr(geostack, "geo_db", None)
        if db is None or not geo.match_name:
            return False
        # UEBER DIE NORMALISIERTEN SPALTEN, nicht ueber `name`.
        # Indiziert sind nur `name_norm` und `ascii_name_norm`; eine Abfrage auf
        # `name` laeuft als voller Tabellendurchlauf und braucht 11,6 s bei
        # 13,4 Mio. Zeilen (gemessen 2026-09-06) statt 0,001 s ueber den Index.
        #
        # Die beiden Spalten sind VERSCHIEDEN normalisiert, und keiner der
        # vorhandenen Normalisierer trifft beide:
        #
        #     name        Zürichsee
        #     name_norm   zürichsee    klein, Umlaute BLEIBEN
        #     ascii_name_norm  zuerichsee    deutsche Umschrift, ue → ascii_norm_de
        #     ascii_norm() ergaebe  zurichsee    — trifft KEINE der beiden
        #
        # Deshalb der Reihe nach alle drei Schreibvarianten.  Bei 12,1 von
        # 13,4 Mio. Zeilen sind beide Spalten ohnehin gleich (Namen ohne
        # Diakritika); die Zusatzabfragen betreffen nur den Rest.
        r = None
        for spalte, wert in (("name_norm",       _klein_norm(geo.match_name)),
                             ("ascii_name_norm", ascii_norm_de(geo.match_name)),
                             ("ascii_name_norm", ascii_norm(geo.match_name))):
            r = _hole(db.conn,
                      f"SELECT feature_class, feature_code FROM geofeature "
                      f"WHERE {spalte} = ? "
                      f"AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? LIMIT 1",
                      (wert, geo.lat - 0.002, geo.lat + 0.002,
                       geo.lon - 0.003, geo.lon + 0.003))
            if r:
                break
        if not r:
            # Die Laender-DBs fuehren nur `feature_type` mit den Werten
            # `precise`/`city`, keine Feature-Klasse — und ihre Namen stehen
            # nicht immer im globalen GeoNames.  Dann ueber die Koordinate.
            naechst = _naechster_geonames(db.conn, geo.lat, geo.lon)
            if not naechst:
                return False
            return (naechst[1] in _GEONAMES_PUNKT_KLASSEN
                    or naechst[2] in _GEONAMES_PUNKT_CODES)
        return r[0] in _GEONAMES_PUNKT_KLASSEN or r[1] in _GEONAMES_PUNKT_CODES

    # ── TGN ──────────────────────────────────────────────────────────────────
    if quelle == "tgn":
        db = getattr(geostack, "tgn_db", None)
        conn = getattr(db, "conn", None)
        if conn is None or not geo.match_name:
            return False
        # UEBER DEN NAMEN, nicht ueber die Koordinate: auf lat/lon gibt es
        # keinen Index, eine Bereichsabfrage laeuft als voller Durchlauf ueber
        # 2,99 Mio. Zeilen und braucht 1,57 s (gemessen 2026-09-06).
        # `idx_tgn_place_norm` liegt auf `name_norm`.
        r = None
        for wert in (_klein_norm(geo.match_name),
                     ascii_norm_de(geo.match_name),
                     ascii_norm(geo.match_name)):
            r = _hole(conn,
                      "SELECT place_type FROM tgn_place WHERE name_norm = ? "
                      "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? LIMIT 1",
                      (wert, geo.lat - 0.002, geo.lat + 0.002,
                       geo.lon - 0.003, geo.lon + 0.003))
            if r:
                break
        if r:
            return (r[0] or "").strip().lower() in _TGN_PUNKT
        # Findet TGN den Namen nicht, derselbe Rueckfall wie bei den anderen
        # Quellen — sonst waere „nicht gefunden" hier stillschweigend „Flaeche",
        # waehrend geo_db und Wikidata noch eine Chance bekommen.
        db_geo = getattr(geostack, "geo_db", None)
        if db_geo is None:
            return False
        naechst = _naechster_geonames(db_geo.conn, geo.lat, geo.lon)
        if not naechst:
            return False
        return (naechst[1] in _GEONAMES_PUNKT_KLASSEN
                or naechst[2] in _GEONAMES_PUNKT_CODES)

    # ── Nominatim: der Typ liegt im eigenen Cache, zu 100 % gefuellt ─────────
    if quelle == "nominatim":
        nm = getattr(geostack, "nominatim", None)
        conn = getattr(nm, "cache_conn", None)
        if conn is None:
            return False
        r = _hole(conn,
                  "SELECT place_type FROM nominatim_cache "
                  "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? LIMIT 1",
                  (geo.lat - 0.0001, geo.lat + 0.0001,
                   geo.lon - 0.0001, geo.lon + 0.0001))
        return bool(r) and (r[0] or "").strip().lower() in _NOMINATIM_PUNKT

    # ── Wikidata und alles Uebrige ───────────────────────────────────────────
    # `wikidata_cache` fuehrt nur key, lat, lon, label — keinen Typ.  Deshalb
    # der Umweg ueber die Koordinate: siehe _naechster_geonames().
    #
    # (Wikidata haette mit P31 die beste Typinformation von allen.  Sie zu holen
    # und mitzuspeichern waere der saubere Weg und wuerde diesen Rueckfall
    # ueberfluessig machen.)
    db = getattr(geostack, "geo_db", None)
    if db is None:
        return False
    naechst = _naechster_geonames(db.conn, geo.lat, geo.lon)
    if not naechst:
        return False
    return naechst[1] in _GEONAMES_PUNKT_KLASSEN or naechst[2] in _GEONAMES_PUNKT_CODES
