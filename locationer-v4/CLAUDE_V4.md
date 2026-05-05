# CLAUDE.md — Locationer V4

This file provides guidance to Claude Code when working with this repository.

## Project

Geocoding-Pipeline für historische Fotoarchive — **ohne Google Places API**.
Liest CSV/XLSX, normalisiert Metadaten mit Claude Haiku, und löst Ortsangaben
zu GPS-Koordinaten auf via GEO DB (GeoNames), TGN (Getty) und Nominatim/OSM.

Rechtliche Grundlage: GeoNames (CC BY 4.0), TGN (ODC-By), OSM/Nominatim (ODbL)
— alle Quellen erlauben persistentes Caching und kommerzielle Nutzung.
**Kein GOOGLE_API_KEY** benötigt.

## Environment Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # ANTHROPIC_API_KEY eintragen
```

`.env` Keys:
```
ANTHROPIC_API_KEY=...            # Pflicht
GEO_DB_PATH=...                  # default: /Volumes/LCMT_JBTM/…/locationer_geo_global.sqlite
TGN_DB_PATH=...                  # default: /Volumes/LCMT_JBTM/…/tgn.sqlite (optional)
CACHE_PATH=...                   # default: cache/locationer.sqlite
OVERRIDES_PATH=...               # default: explicit_list/explicit.sqlite
NOMINATIM_URL=...                # default: https://nominatim.openstreetmap.org
NOMINATIM_USER_AGENT=...         # Pflicht für public Nominatim
```

## TGN importieren (einmalig, ~30–60 Min)

```bash
# Aus lokalem ZIP (empfohlen — kein Entpacken nötig):
python -m v4.import_tgn --from-zip cache/tgn_xml_0126.zip --output cache/tgn.sqlite

# Aus bereits entpacktem Verzeichnis:
python -m v4.import_tgn --from-dir cache/tgn --output cache/tgn.sqlite

# Neu von Getty herunterladen (~600 MB):
python -m v4.import_tgn --download --output cache/tgn.sqlite

# Statistik anzeigen:
python -m v4.import_tgn --check --output cache/tgn.sqlite

# Später auf Volume verschieben:
mv cache/tgn.sqlite /Volumes/LCMT_JBTM/LocationerGeo/tgn.sqlite
# dann in .env: TGN_DB_PATH=/Volumes/LCMT_JBTM/LocationerGeo/tgn.sqlite
```

## Running

```bash
python -m v4 v4/TestFile.csv                          # mit Metrik
python -m v4 v4/ZIN.xlsx --chunk-size 1000 --output out/zin.csv
python -m v4 v4/DIS.xlsx --limit 50 --mode debug
```

Parameter identisch zu V3: `--mode`, `--limit`, `--chunk-size`, `--output`, `--geo-db`.

## Architecture

**Entry point:** `python -m v4` → `v4/__main__.py`

### Phase 1a — Location-Extraktion (`v4/input_normalizer.py`)
- Claude Haiku, Batch à 20 → `title`, `country`, `city`, `location`
- City-Normalisierung: "Chur GR" → "Chur", "7000 Chur" → "Chur"
- Cache: `norm_cache`

### Phase 1b — Metadaten-Extraktion (`v4/input_normalizer.py`)
- Claude Haiku, Batch à 20 → `periode`, `urheber`, `technik`
- Cache: `meta_cache`

### Phase 1c — TGN Resolver (`v4/input_normalizer.py`, `v4/tgn_db.py`)
- Lokale SQLite-Suche, kein Netzwerk, kein API-Call
- Sucht `location` in TGN → setzt `tgn_id` + `tgn_name` auf NormalizedRecord
- TGN-IDs = stabile Entitäts-IDs → Basis für späteres Knowledge Graph

### Phase 2 — GeoStack (`v4/geostack.py`)

```
0.   Overrides        (manuell, explicit_list/explicit.sqlite)
1.   geo_cache        (persistent SQLite)
2.   GEO DB precise   find_precise() S/T/H/V/L + 50km-Validierung  → Score 5
2.5  TGN precise      tgn_id aus Phase 1c → get_by_id()             → Score 5
3.   Nominatim        Text Search, erst location allein, dann +city  → Score 4
     (nur wenn city ODER location bekannt — kein Aufruf ohne Anker)
4.   GEO DB city      find_city() P + ADM3/ADM4 Fallback             → Score 3
5.   Nominatim city   city+country, nur wenn GEO DB miss             → Score 2
→    Score 0          keine Koordinaten
```

**Wikidata deaktiviert:** Der public SPARQL-Endpoint (Blazegraph) hat keine
Full-Text-Indizes für `CONTAINS()` → sequential scan über Millionen Labels →
Timeouts. Re-aktivierbar wenn lokale Wikidata-Instanz verfügbar.

### Neue Module gegenüber V3
- `v4/tgn_db.py` — Query-Interface für lokale TGN SQLite
- `v4/import_tgn.py` — Download + Import TGN XML von Getty
- `v4/nominatim.py` — Nominatim REST API mit Rate-Limiter + Cache

### Von V3 übernommen (unverändert)
`models.py`, `cache.py`, `geo_db.py`, `explicit_store.py`, `overrides.py`,
`metrics.py`, `input_normalizer.py` (Phase 1c ergänzt)

## Quality Score

| Score | Quelle | Fallback | Bedeutung |
|---|---|---|---|
| **5** | GEO DB / TGN | Nein | Präziser Named Place |
| **4** | Nominatim | Nein | Präziser externer Treffer |
| **3** | GEO DB / Override | Ja* | Stadtzentrum aus GEO DB |
| **2** | Nominatim | Ja | Stadtzentrum via Nominatim |
| **0** | — | — | Nicht gefunden |

## Cache-Architektur

| Tabelle | Inhalt | Datei |
|---|---|---|
| `norm_cache` | Phase 1a Ergebnisse | `cache/locationer.sqlite` |
| `meta_cache` | Phase 1b Ergebnisse | `cache/locationer.sqlite` |
| `geo_cache` | Phase 2 Ergebnisse | `cache/locationer.sqlite` |
| `nominatim_cache` | Nominatim-Antworten | `cache/locationer.sqlite` |
| TGN SQLite | Alle TGN-Orte (1.6M) | `tgn.sqlite` (LCMT_JBTM) |
| `overrides` + `norm_overrides` | Manuelle Korrekturen | `explicit_list/explicit.sqlite` |

## Manuelle Overrides

Identisch zu V3:
```bash
python -m v4.overrides add "location|city|country" lat lon "Name" "Notiz"
python -m v4.overrides norm-add "Avers" --city "Avers" --country "Switzerland"
python -m v4.overrides list
python -m v4.overrides norm-list
```

## Kosten (Erstlauf, keine Cache-Treffer)

| Datenmenge | Haiku (1a+1b) | TGN | Nominatim | Total |
|---|---|---|---|---|
| 1'000 Zeilen | ~$0.35 | $0 | $0 | ~$0.35 |
| 700k Zeilen | ~$280 | $0 | $0* | ~$280 |

*Nominatim public: 1 req/s → 700k unique Queries ≈ 194h. Self-hosted empfohlen.

## Wichtig: Kein Overfitting

Lösungen müssen generell wirken. Einzelfall-Korrekturen → Overrides, nicht Code.
TestFile.csv ist Kontrollmessung, nicht Trainingsdaten.

## Design-Prinzipien

1. **GEO DB zuerst** — lokal, gratis, sofort
2. **TGN vor Nominatim** — besser für kulturelle/historische Objekte (Archiv-Fokus)
3. **Nominatim als robuster Fallback** — globale OSM-Abdeckung, rechtlich sauber
4. **Kein Aufruf ohne Anker** — Nominatim nur wenn city ODER location bekannt
5. **50-km-Check** — GEO DB precise > 50km vom Stadtzentrum → verwerfen
6. **Resumierbarkeit** — Cache + chunk-size wie V3
