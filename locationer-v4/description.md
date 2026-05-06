# Locationer V4 — Beschreibung

Geocoding-Pipeline für historische Fotoarchive. Liest CSV/XLSX, extrahiert Ortsinformationen mit Claude Haiku und löst diese zu GPS-Koordinaten auf — ohne Google Places API.

Quellen: GeoNames (CC BY 4.0), Getty TGN (ODC-By), OpenStreetMap/Nominatim (ODbL) — alle erlauben persistentes Caching und kommerzielle Nutzung.

---

## 1. Lizenz und Nutzungsrechte (TGN)

Getty TGN wird unter **ODC-By** (Open Data Commons Attribution License) veröffentlicht. Für die Nutzung im Locationer / Pictomap gilt:

| Nutzungsart | Status |
|---|---|
| Lokale Speicherung als SQLite | ✓ erlaubt |
| Interne Nutzung in Phase 1c und Step 2.5 | ✓ erlaubt |
| Kommerzielle Nutzung (Pictomap als Service) | ✓ erlaubt |
| Offline-Betrieb ohne Netzwerk | ✓ erlaubt |
| Integration in eigenes Geocoding-System | ✓ erlaubt |

**Einzige Pflicht: Attribution.** Irgendwo im Produkt oder in der Dokumentation muss stehen:

> Geografische Daten (teilweise) vom Getty Research Institute, Thesaurus of Geographic Names (TGN).

Solange TGN-Ortsnamen nur intern für Koordinatenlookups verwendet werden (aktueller Stand), reicht ein Hinweis in der Dokumentation. Wenn TGN-Namen später in Pictomap öffentlich angezeigt werden (z.B. als Quellangabe bei einem Treffer), soll die Attribution auch dort sichtbar sein.

Keine Probleme entstehen durch: kommerzielle Nutzung, Offline-Betrieb, persistentes Caching, Integration in einen Geocoding-Service, oder den Weiterbetrieb nach Haiku-Preprocessing.

### Was TGN zum Output beiträgt

| Output-Feld | Quelle | TGN? |
|---|---|---|
| `Country` | Claude Haiku (Phase 1a) aus Originaltext | nein |
| `City` | Claude Haiku (Phase 1a) aus Originaltext | nein |
| `Region` | GeoNames-DB (`admin1`-Lookup) | nein |
| `Lat` / `Lon` | GeoStack-Gewinner — kann TGN sein (Step 2.5, Score 5) | manchmal |
| `Coord-Quality-Score` | intern | nein |

TGN liefert ausschliesslich **Koordinaten** wenn Step 2.5 gewinnt. Koordinaten sind Fakten — ODC-By schützt die Datenbank als Werk, nicht individuelle Fakten daraus.

`tgn_name` wird intern im `NormalizedRecord` gespeichert, aber nicht in die CSV-Ausgabe geschrieben und nicht an Pictomap übergeben. Country, Region und City kommen nie aus TGN. Solange das so bleibt, greift die Anzeige-Attribution nicht.

---

## 2. Schnellstart

```bash
source venv/bin/activate
python -m v4 v4/TestFile.csv
```

---

## 3. Hauptpipeline — Aufruf und Parameter

```
python -m v4 <input> [--mode human|debug] [--output PATH] [--limit N]
                     [--chunk-size N] [--geo-db PATH]
```

| Parameter | Default | Beschreibung |
|---|---|---|
| `input` | — | CSV oder XLSX Eingabedatei (Pflicht) |
| `--mode` | `human` | `human` = kompakte Tabellenausgabe, `debug` = vollständige Entscheidungspfade |
| `--output` / `-o` | `<input>_geo.csv` | Pfad der Ausgabedatei |
| `--limit N` | — | Nur die ersten N Zeilen verarbeiten |
| `--chunk-size N` | `20` | Verarbeitung in Blöcken à N Zeilen; setzt bei Unterbrechung automatisch fort |
| `--geo-db PATH` | aus `.env` | Pfad zur GeoNames-SQLite (überschreibt `GEO_DB_PATH`) |

### Beispiele

```bash
# Testlauf, erste 10 Zeilen
python -m v4 v4/TestFile.csv --limit 10

# Voller Lauf mit Chunk-Resuming (sicher bei grossen Dateien)
python -m v4 v4/ZIN_Complete.csv --chunk-size 500 --output out/zin_geo.csv

# Detaillierte Entscheidungsausgabe für Debugging
python -m v4 v4/TestFile.csv --mode debug --limit 5

# Andere GEO DB (z.B. Europa-only)
python -m v4 meinedaten.csv --geo-db /Volumes/LCMT_JBTM/LocationerGeo/locationer_geo_EUROPA.sqlite
```

### Eingabeformat

CSV (Trennzeichen `,` oder `;` wird automatisch erkannt) oder XLSX. Relevante Spalten werden per Aliasmatching gefunden:

| Internes Feld | Erkannte Spaltennamen |
|---|---|
| `title` | title, titel, name, subject |
| `description` | description, beschreibung, desc, text, caption, legende |
| `country` | country, land, pays, pais, country_name |
| `city` | city, stadt, ville, adresse, address, ort, place |

Optionale Spalten `lat_true` / `lon_true` (oder `Breitengrad` / `Längengrad`): wenn vorhanden, berechnet die Pipeline die Abweichung der Geocodierung von der bekannten Position und schreibt `Deviation_km` in den Output. Grundlage für die Testmetrik.

### Ausgabeformat

```
Title, Description, Periode, Urheber, Technik,
Country, Region, City, Lat, Lon,
Coord-Quality-Score, Fallback, Ext-Calls[, Deviation_km]
```

`Deviation_km` erscheint nur wenn `lat_true`/`lon_true` im Input vorhanden sind.

---

## 4. Verarbeitungsablauf

```
Eingabe (CSV/XLSX)
        │
        ▼
┌───────────────────────────────────────┐
│  Phase 1a — Normalisierung (Haiku)    │
│  Batch à 20 Zeilen                    │
│  → title, country, city, location     │
│  Cache: norm_cache (SHA-256 des Inputs)│
└────────────────┬──────────────────────┘
                 │
                 ▼
┌───────────────────────────────────────┐
│  Phase 1b — Metadaten (Haiku)         │
│  Batch à 20 Zeilen                    │
│  → periode, urheber, technik          │
│  Cache: meta_cache                    │
└────────────────┬──────────────────────┘
                 │
                 ▼
┌───────────────────────────────────────┐
│  Phase 1c — TGN-Lookup (lokal)        │
│  Kein API-Call, kein Netzwerk         │
│  Input: rec.location (aus Phase 1a)   │
│  → setzt tgn_id + tgn_name           │
└────────────────┬──────────────────────┘
                 │
                 ▼
┌───────────────────────────────────────┐
│  Phase 2 — GeoStack                   │
│  Pro Datensatz, Entscheidungsbaum     │
│  (siehe Abschnitt 4)                  │
│  → Lat, Lon, Quality-Score            │
└────────────────┬──────────────────────┘
                 │
                 ▼
        Ausgabe (CSV)
```

### Phase 1a — Normalisierung

Claude Haiku extrahiert aus Titel und Beschreibung:

| Feld | Beschreibung |
|---|---|
| `title` | Bereinigter Titel (max. 60 Zeichen, Originalsprache) |
| `country` | Landesname auf Englisch |
| `region` | Kanton, Bundesland, Provinz (z.B. `"Graubünden"`, `"Bavaria"`) — leer wenn nicht erwähnt |
| `city` | Stadt oder Ortschaft inkl. Disambiguierungssuffix (z.B. `"Bingen am Rhein"` statt `"Bingen"`) |
| `location` | Spezifischer benannter Ort in Originalsprache (z.B. `"Schloss Vaduz"`, `"Montalin Schulhaus"`) — leer wenn nur Stadt, Portrait oder generische Szene |
| `location_type` | Generischer Gebäudetyp im Originalwort wenn `location` leer (z.B. `"Bahnhof"`, `"Kirche"`) — kombiniert mit city zu Nominatim-Query |

Regeln für `location`:
- Originalsprache, nie übersetzen
- Komposita mit Eigennamenpräfix aufbrechen: `"Montalinschulhaus"` → `"Montalin Schulhaus"`, `"Quaderschulhaus"` → `"Quader Schulhaus"`
- Generische Wörter allein ohne Eigennamenpräfix → leer; stattdessen `location_type` setzen

`location_type`-Mechanismus: Wenn Haiku `location_type="Bahnhof"` und `city="Göschenen"` zurückgibt, setzt die Pipeline `location="Bahnhof Göschenen"` → Nominatim findet das OSM-Objekt in der richtigen Sprache.

Nach dem AI-Call normalisiert der Code Stadtnamen:
- `"7000 Chur"` → `"Chur"` (Postleitzahlen)
- `"Chur GR"` → `"Chur"` (Kantonskürzeln)
- `"Kilchberg (ZH)"` → `"Kilchberg"`

Robustheit: Wenn Haiku den Input-Stadtnamen kürzer zurückgibt als der Originalwert und kein Teilstring-Verhältnis besteht (z.B. `"Lzern"` ≠ `"Luzern"`), wird der Originalwert aus dem Input bevorzugt.

### Phase 1b — Metadaten

Separater Haiku-Call für historische Metadaten:
- `periode`: Zeitraum (z.B. `"1914-1918"`)
- `urheber`: Fotograf / Künstler
- `technik`: Fotografietechnik (z.B. `"Albumin"`, `"Photochrome"`, `"Cyanotype"`)

Nur explizit im Text genannte Werte werden extrahiert, keine Inferenz.

### Phase 1c — TGN-Lookup

Lokaler SQLite-Lookup auf `rec.location`. Der `country_code` (aus `rec.country` via GeoNames aufgelöst) wird mitgegeben, damit TGN bei Homonymen die geografisch korrekte Variante bevorzugt (z.B. `"Tödi"` → Schweizer Berg, nicht gleichnamiger Fluss in Pakistan). Wenn TGN einen Treffer findet, wird `tgn_id` im `NormalizedRecord` gesetzt. Dieser ID-Zeiger wird in Phase 2 Step 2.5 genutzt.

---

## 5. GeoStack — Entscheidungsbaum

Pro Datensatz durchläuft die Pipeline folgende Schritte. Der erste Treffer gewinnt.

```
Step 0   Overrides (manuell)
           Pattern location|city|country → exakte Koordinaten aus explicit.sqlite
           Score konfigurierbar (Standard 3)
           ↓ miss
Step 1   geo_cache
           Persistent SQLite — Ergebnis bereits berechnet
           ↓ miss
           [Proximity-Anker bestimmen]
           find_city(city, country_code, admin1_hint) → near=(lat,lon)
           Falls kein PPL-Eintrag: find_precise(city) → near aus geogr. Feature
           (z.B. "Pilatus" ist ein Berg [MTS], kein Ort — trotzdem als Anker nutzbar)
           region → admin1_hint via region_to_admin1() für Disambiguierung
           ↓
Step 2   GEO DB precise (GeoNames)
           find_precise(location, country_code, near=city_coords)
           Typen: S (Spots), T (Terrain), H (Hydrography), V (Vegetation), L (Locality)
           50-km-Check: Treffer > 50 km vom Stadtzentrum → verwerfen
           → Score 5 (kein Fallback)
           ↓ miss
Step 2.5 TGN (Getty Thesaurus of Geographic Names)
           Nur wenn Phase 1c eine tgn_id gesetzt hat
           get_by_id(tgn_id) → direkter ID-Lookup, kein Netzwerk
           Stark für: Kulturerbe, historische Stätten, Berge, Kunstorte
           → Score 5 (kein Fallback)
           ↓ miss
Step 3   Nominatim (OpenStreetMap)
           Nur wenn city ODER location bekannt (kein Aufruf ohne Anker)
           Generische Titel ohne location_type → überspringen (→ Step 4)
           Erst: location allein; bei Miss: location + city + country
           Proximity-Check: präziser Treffer > 50 km von near → verwerfen, retry
           Bei verweigertem Treffer und fehlgeschlagenem Retry → kein Ergebnis
           (kein Fallback auf den abgelehnten Treffer)
           Précis-Treffer → Score 4
           ↓ miss oder nicht präzis
Step 4   GEO DB city (Stadtzentrum)
           find_city(city, country_code, admin1_hint)
           admin1_hint aus region-Feld → disambiguiert gleichnamige Orte in verschiedenen Kantonen
           Typ P + ADM3/ADM4-Fallback
           → Score 3 (Fallback = True)
           ↓ miss
Step 5   Nominatim city-only (für Länder mit schlechter GEO-DB-Abdeckung)
           Nicht-präziser Nominatim-Treffer aus Step 3 → Score 2 (Fallback = True)
           oder neuer city+country-Query → Score 2
           ↓ miss
→          Score 0 — nicht gefunden
```

### Generische Titel (Skip-Logik)

Wenn `location` leer ist, kein `location_type` gesetzt wurde, und der Titel ausschliesslich aus generischen Wörtern besteht, wird Nominatim übersprungen. Beispiele: `ortsteilansicht`, `panorama`, `portrait`, `landschaft`, `dorfbild`. Der Record landet direkt bei Step 4. Ist `location_type` gesetzt (z.B. `"Kirche"`), wird Nominatim trotzdem aufgerufen — `location_type + city` ergibt einen brauchbaren Query.

### Region-Disambiguierung

`region` (aus Phase 1a) wird via `region_to_admin1()` in einen GeoNames `admin1`-Code aufgelöst. Dieser wird als Tiebreaker in `find_city()` verwendet — z.B. `city="Lohn"`, `region="Graubünden"` → `admin1="GR"` → der Lohn in GR wird dem Lohn in Schaffhausen vorgezogen. Funktioniert für alle Sprachen (Haiku gibt die Variante zurück wie im Originaltext, GeoNames hat die Alt-Namen mehrsprachig).

### Quality-Score

| Score | Quelle | Fallback | Bedeutung |
|---|---|---|---|
| 5 | GEO DB / TGN | Nein | Präziser Named Place |
| 4 | Nominatim | Nein | Präziser externer Treffer |
| 3 | GEO DB / Override | Ja | Stadtzentrum aus GEO DB |
| 2 | Nominatim | Ja | Stadtzentrum via Nominatim |
| 0 | — | — | Nicht gefunden |

---

## 6. Cache-Architektur

Alle Caches sind persistente SQLite-Dateien. Ergebnisse werden einmalig berechnet und danach direkt abgerufen.

| Cache-Tabelle | Inhalt | Datei |
|---|---|---|
| `norm_cache` | Phase 1a Ergebnisse (title/country/region/city/location) | `cache/locationer.sqlite` |
| `meta_cache` | Phase 1b Ergebnisse (periode/urheber/technik) | `cache/locationer.sqlite` |
| `geo_cache` | Phase 2 Ergebnisse (Koordinaten + Score) | `cache/locationer.sqlite` |
| `nominatim_cache` | Nominatim-Antworten | `cache/locationer.sqlite` |
| TGN-Datenbank | 2.99 Mio. Orte aus Getty TGN | `cache/tgn.sqlite` |
| Overrides | Manuelle Koordinaten + Normalisierungen | `explicit_list/explicit.sqlite` |

### Auto-Resume

`--chunk-size 20` ist der Default — d.h. jeder Lauf schreibt alle 20 Zeilen auf Disk und kann nach einem Crash oder Unterbruch nahtlos fortgesetzt werden. Einfach **denselben Befehl nochmals ausführen** — die Pipeline erkennt die bestehende Output-Datei und macht ab der nächsten unverarbeiteten Zeile weiter.

```bash
# Erster Lauf (oder Resume nach Unterbruch — gleicher Befehl):
python -m v4 v4/ZIN_complete.csv
```

`--chunk-size 20` entspricht genau der internen Haiku-Batch-Grösse → keine Zusatzkosten gegenüber grösseren Chunk-Sizes. Maximaler Datenverlust bei Crash: 19 Zeilen.

---

## 7. Overrides — manuelle Korrekturen

Overrides haben höchste Priorität (Step 0, vor allen Datenbanken). Zwei Typen:

### Geo-Overrides (Phase 2 — Koordinaten)

Fixer Koordinatenpunkt für ein Pattern `location|city|country`. `*` = Wildcard.

```bash
# Hinzufügen
python -m v4.overrides add "Cresta||Switzerland" 46.453581 9.542744 "Avers GR" "Bemerkung"
python -m v4.overrides add "*|Avers|Switzerland" 46.453581 9.542744 "Avers GR"
python -m v4.overrides add "Cresta|*|Switzerland" 46.453581 9.542744 "Avers GR" --score 5

# Alle anzeigen
python -m v4.overrides list

# Entfernen
python -m v4.overrides remove "Cresta||Switzerland"
```

Patternformat:
- `"Schloss Vaduz||Liechtenstein"` — exakt: location=Schloss Vaduz, kein city, country=Liechtenstein
- `"*|Avers|Switzerland"` — jeder Record mit city=Avers in Switzerland
- `"Cresta|*|Switzerland"` — jeder Record mit location=Cresta in Switzerland, egal welche city

### Norm-Overrides (Phase 1 — Normalisierungskorrektur)

Korrigiert city/country wenn Haiku systematisch falsch liegt. Substring-Match auf dem rohen Eingabetext.

```bash
# Hinzufügen
python -m v4.overrides norm-add "Avers" --city "Avers" --country "Switzerland"
python -m v4.overrides norm-add "Calancatal" --country "Switzerland"

# Alle anzeigen
python -m v4.overrides norm-list

# Entfernen
python -m v4.overrides norm-remove "Avers"
```

Norm-Overrides greifen nur wenn das entsprechende Feld im Haiku-Output leer ist (sie ergänzen, überschreiben nicht).

---

## 8. TGN-Datenbank — Import

Einmalig ausführen. Liest direkt aus dem lokalen ZIP, kein vollständiges Entpacken nötig (~10–15 Minuten).

```bash
# Empfohlen: aus lokalem ZIP
python -m v4.import_tgn --from-zip cache/tgn_xml_0126.zip --output cache/tgn.sqlite

# Alternativ: aus entpacktem Verzeichnis
python -m v4.import_tgn --from-dir cache/tgn --output cache/tgn.sqlite

# Direkt von Getty herunterladen (~600 MB)
python -m v4.import_tgn --download --output cache/tgn.sqlite

# Statistik anzeigen
python -m v4.import_tgn --check --output cache/tgn.sqlite

# Datei auf Volume verschieben, dann .env anpassen
mv cache/tgn.sqlite /Volumes/LCMT_JBTM/LocationerGeo/tgn.sqlite
# .env: TGN_DB_PATH=/Volumes/LCMT_JBTM/LocationerGeo/tgn.sqlite
```

Ergebnis nach Import: ~2.99 Mio. Orte, davon ~2.97 Mio. mit Koordinaten, ~2.3 Mio. Alternativnamen (inkl. Mehrsprachigkeit).

---

## 9. Konfiguration (.env)

```
ANTHROPIC_API_KEY=sk-ant-...          # Pflicht
GEO_DB_PATH=/Volumes/.../locationer_geo_global.sqlite  # Pflicht (4.5 GB)
TGN_DB_PATH=cache/tgn.sqlite          # Optional (534 MB, lokal oder Volume)
CACHE_PATH=cache/locationer.sqlite    # Default
OVERRIDES_PATH=explicit_list/explicit.sqlite  # Default
NOMINATIM_URL=https://nominatim.openstreetmap.org  # Default
NOMINATIM_USER_AGENT=locationer/4.0 (email)  # Pflicht für public Nominatim
```

Alle Pfade können relativ (zum Arbeitsverzeichnis) oder absolut angegeben werden.

---

## 10. Testmetrik

Wenn `input` identisch mit `v4/TestFile.csv` ist, berechnet die Pipeline automatisch eine Qualitätsmetrik und schreibt sie in `TESTLOG.md`.

Voraussetzung: `TestFile.csv` muss Spalten `lat_true`/`lon_true` mit verifizierten Referenzkoordinaten enthalten.

```
| Timestamp        | <100m | 100-1km | 1-10km | >10km | kein | FB%  | S5%  | Ext   | n/a→∅ |
| <100m            | Treffer < 100 m Abweichung
| 100-1km          | Treffer 100 m – 1 km
| 1-10km           | Treffer 1 – 10 km
| >10km            | Treffer > 10 km (vermutlich falsche Zuordnung)
| kein             | Keine Koordinaten gefunden (Score 0)
| FB%              | Anteil Fallback-Treffer (Stadtzentrum statt exakter Ort)
| S5%              | Anteil Score-5-Treffer (GEO DB / TGN precise)
| Ext              | Anzahl Nominatim-Calls (nicht aus Cache)
| n/a→∅            | Zeilen ohne Referenzkoordinaten (ok/bad)
```

---

## 11. Kostenabschätzung

| Datenmenge | Claude Haiku (Phase 1a+1b) | TGN | Nominatim | Total |
|---|---|---|---|---|
| 1'000 Zeilen | ~$0.10 | $0 | $0 | ~$0.10 |
| 700'000 Zeilen | ~$70 | $0 | $0* | ~$70 |

Haiku-Calls: ceil(N/20) pro Phase × 2 Phasen. Bei 1000 Zeilen ~100 Calls. Gemessene Kosten ZIN_1000: ~$0.10.

*Nominatim public: 1 req/s → bei 700k unique Queries ~194h. Self-hosted empfohlen für grosse Volumen. Cache hält alle Ergebnisse — Wiederholungsläufe kosten nichts.

### Output-Anzeige

Pro Zeile: `[N:n]` zeigt kumulierte Nominatim-Calls. Am Ende:
```
Haiku calls (Phase 1a+1b): 2
Nominatim calls:           14
```

---

## 12. Dateistruktur

```
locationer-v4/
├── v4/
│   ├── __main__.py          Einstiegspunkt, CLI, Ausgabe
│   ├── input_normalizer.py  Phase 1a/1b/1c (Haiku + TGN-Lookup)
│   ├── geostack.py          Phase 2: Entscheidungsbaum
│   ├── geo_db.py            GeoNames SQLite Interface
│   ├── tgn_db.py            TGN SQLite Interface
│   ├── import_tgn.py        TGN Import (ZIP → SQLite)
│   ├── nominatim.py         Nominatim REST API + Cache
│   ├── overrides.py         Override CLI
│   ├── explicit_store.py    Override SQLite Interface
│   ├── cache.py             Cache SQLite Interface
│   ├── models.py            NormalizedRecord, GeoResult
│   ├── metrics.py           Testmetrik
│   ├── wikidata.py          Wikidata (deaktiviert — Timeouts)
│   ├── TestFile.csv         Testdaten (mit lat_true/lon_true)
│   └── TestFile_geo.csv     Letzter Testlauf-Output
├── cache/
│   ├── locationer.sqlite    norm/meta/geo/nominatim cache
│   ├── tgn.sqlite           Getty TGN (2.99 Mio. Orte)
│   └── tgn_xml_0126.zip     TGN Quelldaten (Originalformat)
├── explicit_list/
│   └── explicit.sqlite      Manuelle Overrides
├── .env                     Konfiguration (nicht in Git)
├── .env.example             Vorlage
├── requirements.txt
└── TESTLOG.md               Messhistorie
```
