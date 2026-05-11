# Locationer V4 — Beschreibung

Geocoding-Pipeline für historische Fotoarchive. Liest CSV/XLSX, extrahiert Ortsinformationen mit Claude Haiku und löst diese zu GPS-Koordinaten auf — ohne Google Places API.

Quellen: GeoNames (CC BY 4.0), Getty TGN (ODC-By), OpenStreetMap/Nominatim (ODbL) — alle erlauben persistentes Caching und kommerzielle Nutzung.

---

## 0. Checkliste: Neue Sammlung einrichten

Vor dem ersten Lauf mit einer neuen Sammlung diese Punkte abarbeiten:

### Inputfile vorbereiten

- [ ] **Format:** CSV (`,` oder `;` als Separator, automatisch erkannt) oder XLSX
- [ ] **Encoding:** UTF-8 oder UTF-8-BOM. Latin-1 wird toleriert, kann aber Sonderzeichen korrumpieren. Im Zweifel in UTF-8 konvertieren: `iconv -f latin-1 -t utf-8 input.csv > input_utf8.csv`
- [ ] **Spaltennamen:** Spalten die automatisch erkannt werden (Aliase — Gross/Kleinschreibung egal):

| Locationer-Feld | Erkannte Spaltennamen |
|---|---|
| `title` | title, titel, name, subject |
| `description` | description, beschreibung, desc, text, caption, legende |
| `country` | country, land, pays, pais, country_name |
| `city` | city, stadt, ville, adresse, address, ort, place |
| `region` | **Region** (empfohlen), kanton, canton, bundesland, state, province, provincia, département, county |

  → Wenn eine Spalte anders heisst: **umbenennen zu einem der obigen Aliase**, empfohlen ist immer `Region` für Region/Kanton/Bundesland/State.

- [ ] **Region-Spalte:** Falls bekannt (z.B. Kanton CH, Bundesland DE, State US) → Spalte `Region` mit vollen Namen befüllen (z.B. `"Graubünden"`, `"Bavaria"`, `"California"`), **nicht** Abkürzungen. Diese Information verbessert die Disambiguierung erheblich.
- [ ] **Beschreibungen bereinigen:** Wikimedia-Markup (`de|1=Text|1=Text`) wird automatisch bereinigt. Andere proprietäre Formate müssen manuell bereinigt werden.

### .env konfigurieren

- [ ] **ANTHROPIC_API_KEY** — Pflicht
- [ ] **GEO_DB_PATH** — Pfad zur GeoNames SQLite (4.5 GB)
- [ ] **COLLECTION_BBOX** — Wichtigste sammlungsspezifische Einstellung:

```
# Format: min_lat,max_lat,min_lon,max_lon
# Regel: grosszügig genug für alle legitimen Treffer, eng genug um falsche Weltregionen auszuschliessen

COLLECTION_BBOX=43.0,62.0,-5.0,20.0    # Schweiz + Europa
# COLLECTION_BBOX=18.0,38.0,-5.0,40.0  # Nordafrika
# COLLECTION_BBOX=25.0,50.0,60.0,90.0  # Zentralasien
# COLLECTION_BBOX=                       # Global (kein Filter)
```

  Greift nur wenn Haiku kein Land erkennt (`country=""`). Bilder mit bekanntem Land (`country="Egypt"`) werden immer korrekt geocodiert.

- [ ] **NOMINATIM_USER_AGENT** — eigene E-Mail eintragen (Pflicht für public Nominatim)

### Testlauf

```bash
# Erst 20 Zeilen testen
python -m v4 meine_sammlung.csv --limit 20 --mode debug

# Karte anschauen — sind die Treffer plausibel?
# Dann vollen Lauf starten mit caffeinate (verhindert Sleep bei Nachtläufen):
caffeinate -i python -m v4 meine_sammlung.csv
```

### Optionale Verbesserungen nach erstem Lauf

- [ ] QA-Karte reviewen → offensichtliche Fehler als Overrides erfassen
- [ ] Score-0-Treffer anschauen → ev. Overrides für bekannte Problemnamen (Grenzgipfel, historische Namen)
- [ ] Norm-Overrides für systematische Haiku-Fehler bei spezifischen Ortsnamen

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

# Nachtlauf ohne Sleep-Unterbruch:
caffeinate -i python -m v4 meine_sammlung.csv
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

Das Output-CSV enthält zuerst **alle Originalspalten des Inputs** (unverändert), dann die geocodierten Felder:

```
<alle Input-Spalten> …
Title, Description, Periode, Urheber, Technik,
Country, Region, City, Lat, Lon,
Coord-Quality-Score, Fallback, Ext-Calls[, Deviation_km]
```

Wenn eine Input-Spalte denselben Namen wie eine Geocoding-Spalte trägt (z.B. `Title`), bleibt der Originalwert erhalten und der normalisierte Wert erscheint zusätzlich am Ende.

`Periode` — immer im PCTM-Format. Haiku extrahiert den Zeitraum aus Description + `EXTRA_DESC_COLS`, die Pipeline normalisiert ihn. Trennzeichen Jahr/Monat = `:`, Trennzeichen Start/Ende = `-`.

| Input (Haiku-Rohwert) | Periode (PCTM) | Regel |
|---|---|---|
| `1908` | `1908` | Einzeljahr |
| `circa 1890` | `1890` | Qualifier wird gestripped |
| `1914-1918` | `1914-1918` | Jahres-Range (4+4 Stellen) |
| `1914-18` | `1914-1918` | Kurzjahr (2 Stellen, >12 → Jahres-Range) |
| `1910-03` | `1910:03` | Monat (2 Stellen, ≤12 → Monat, nicht 1910-1903!) |
| `1947-09` | `1947:09` | idem — 09 ≤ 12 → September, nicht 1947-1909 |
| `1947-9` | `1947:09` | 1 Stelle → immer Monat |
| `1910-03-1920-05` | `1910:03-1920:05` | Monats-Range |
| nicht erwähnt | *(leer)* | kein Halluzinieren |

**Kritische Unterscheidung:** `1947-09` → `1947:09` (September 1947), nicht `1947-1909`. Zweistellige Werte ≤ 12 werden immer als Monat interpretiert. Zweistellige Werte >12 als Kurzjahr (`1914-18` → `1914-1918`).

`Deviation_km` erscheint nur wenn `lat_true`/`lon_true` im Input vorhanden sind.

Konfiguration von `EXTRA_DESC_COLS` → siehe Abschnitt 9 (Konfiguration).

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
| `region` | Kanton, Bundesland, Provinz — explizit genannt oder aus geographischem Kontext inferiert (z.B. `"Berninagruppe"` → `"Graubünden"`, `"Jungfrau"` → `"Bern"`) |
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

Separater Haiku-Call für historische Metadaten. Haiku sieht dabei **alle Spalten des Inputfiles** (nicht nur title/description) — Felder wie `PeriodeRAW`, `Urheber_Roh` etc. werden automatisch einbezogen, ohne dass `EXTRA_DESC_COLS` nötig ist.

Extrahierte Felder:
- `periode`: Zeitraum als Rohwert (z.B. `"circa 1890"`, `"1914-1918"`) → wird von der Pipeline in PCTM-Format normalisiert
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
           [Proximity-Anker + Plausibilitätsprüfung]
           find_city(city, country_code, admin1_hint) → near=(lat,lon)
           Bei X/Y-Stadtnamen (z.B. "Bergün/Bravuogn") werden beide Teile versucht
           Falls kein PPL-Eintrag: find_precise(city) → near aus geogr. Feature
           Falls near=None aber region bekannt: admin1-Zentroid als Fallback-Anker
           city_radius: dynamisch nach Population (Flerden 2 km / Zürich 15 km / Tokyo 25 km)
           geo_feature: Pässe, Schluchten, Berge etc. → 4–6× grösserer Radius
           Alle Treffer geprüft gegen Länder-Bounding-Box (_COUNTRY_BBOX, ±1°)
           Wenn country unbekannt: COLLECTION_BBOX als Fallback-Schranke
           ↓
Step 2   GEO DB precise (GeoNames)
           find_precise(location, country_code, near, admin1_hint)
           Typen: S (Spots), T (Terrain), H (Hydrography), V (Vegetation), L (Locality)
           Radius: max(city_radius × 4, 20 km) — geo_feature: max(city_radius × 6, 15 km)
           Bbox-Check: Treffer ausserhalb Landesgrenzen → verwerfen
           Disambiguierung: admin1_hint + Alt-Name-Anzahl als Fame-Proxy
           → Score 5 (kein Fallback)
           ↓ miss
Step 2.5 TGN (Getty Thesaurus of Geographic Names)
           Nur wenn Phase 1c eine tgn_id gesetzt hat
           get_by_id(tgn_id) → direkter ID-Lookup, kein Netzwerk
           Radius + Bbox-Check wie Step 2
           → Score 5 (kein Fallback)
           ↓ miss
Step 3   Nominatim (OpenStreetMap)
           Nur wenn city ODER location bekannt (kein Aufruf ohne Anker)
           Generische Titel ohne location_type → überspringen (→ Step 4)
           Query: location + region + country wenn kein Stadtanker (verhindert
             falsche Homonym-Treffer, z.B. "Flüelastrasse" in Zürich statt GR)
           Proximity-Check mit city_radius (geo_feature: 4× erweitert):
             - Stadtanker vorhanden: Treffer > city_radius → verwerfen, retry
             - Nur admin1-Zentroid: Treffer > 100 km → verwerfen, retry
           Bbox-Check: Treffer ausserhalb Landesgrenzen → verwerfen
           Précis-Treffer → Score 4
           ↓ miss oder nicht präzis
Step 4   GEO DB city (Stadtzentrum)
           find_city(city, country_code, admin1_hint)
           Bbox-Check: Treffer ausserhalb Landesgrenzen → verwerfen
           admin1_hint aus region-Feld → disambiguiert gleichnamige Orte
           Typ P + ADM3/ADM4-Fallback
           → Score 3 (Fallback = True)
           ↓ miss
Step 5   Nominatim city-only (für Länder mit schlechter GEO-DB-Abdeckung)
           Nicht-präziser Nominatim-Treffer aus Step 3 → Score 2 (Fallback = True)
           oder neuer city+country-Query → Score 2
           ↓ miss
→          Score 0 — nicht gefunden
```

### Geographic Feature Detection

`_is_geo_feature(location)` erkennt natürliche/geografische Features anhand von Keywords in `record.location` (Deutsch/Französisch/Italienisch/Englisch/Romanisch):

- **Pässe:** pass, passo, joch, col, sattel, forcella
- **Gipfel:** horn, gipfel, spitze, pic, piz, pizzo, monte, peak, summit, kulm
- **Schluchten:** schlucht, tobel, klamm, klus, gorge, canyon, gola
- **Täler:** tal, val, valle, vallée, valley
- **Gewässer:** see, lac, lago, lake, bach, fluss, torrent
- **Gletscher:** gletscher, glacier, ghiacciaio, firn
- u.a. alp, grat, fels, moos, fjord

Erkannte geo_features erhalten 4–6× grössere Proximity-Radien als Gebäude der gleichen Stadt.

### Generische Titel (Skip-Logik)

Wenn `location` leer ist, kein `location_type` gesetzt wurde, und der Titel ausschliesslich aus generischen Wörtern besteht, wird Nominatim übersprungen. Beispiele: `ortsteilansicht`, `panorama`, `portrait`, `landschaft`, `dorfbild`. Der Record landet direkt bei Step 4. Ist `location_type` gesetzt (z.B. `"Kirche"`), wird Nominatim trotzdem aufgerufen — `location_type + city` ergibt einen brauchbaren Query.

### Dynamischer City-Radius

Der Proximity-Radius richtet sich nach der Stadtgrösse (aus GeoNames `population`):

| Stadttyp | Beispiel | Radius | GeoNames-Radius |
|---|---|---|---|
| Weiler | Flerden (~150) | 2 km | 20 km |
| Kleinstadt | Thusis (~3000) | 3 km | 20 km |
| Regionalstadt | Chur (~36k) | 8 km | 32 km |
| Grossstadt | Zürich (~415k) | 15 km | 60 km |
| Metropole | Paris (~2.1M) | 25 km | 100 km |

Geografische Features (Schluchten, Pässe, Berge, Seen — erkannt durch Keyword-Matching auf `location`) erhalten 4–6× mehr Radius, weil sie naturgemäss ausserhalb des Siedlungsgebiets liegen. Gebäude (Hotels, Kirchen, Brücken) bleiben beim engen Stadtradius.

### Cache-Validierung

Jeder Cache-Hit wird vor der Rückgabe gegen die aktuellen Checks validiert:
1. **Bbox-Check** (Country oder Collection) — verwirft Pakistan/Vietnam-Altlasten
2. **Proximity-Check** für Nominatim-Precise-Hits — verwirft Altlasten mit alten Thresholds
3. **Fallback-Bypass** — wenn `location` bekannt ist und der Cache nur einen Fallback (Score 3) enthält, wird neu geocodiert damit Nominatim/GeoNames einen präzisen Treffer versuchen kann

### Region-Disambiguierung

`region` (aus Phase 1a) wird via `region_to_admin1()` in einen GeoNames `admin1`-Code aufgelöst (`admin1_hint`). Dieser wird an drei Stellen verwendet:

1. `find_city()` — Tiebreaker bei gleichnamigen Orten in verschiedenen Kantonen (z.B. `city="Lohn"`, `region="Graubünden"` → GR-Lohn statt SH-Lohn)
2. `find_precise()` — bevorzugt Kandidaten im richtigen Kanton + mehr Alt-Namen (Fame-Proxy), z.B. Piz Nair GR statt Piz Nair UR
3. Nominatim-Query — wenn kein Stadtanker, wird Region in den Query aufgenommen (z.B. `"Flüelastrasse Graubünden Switzerland"`)

Haiku inferiert Region auch aus geographischem Kontext, wenn sie nicht explizit genannt ist (z.B. `"Berninagruppe"` oder `"Corvatsch"` → `"Graubünden"`).

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

### Override-Liste und Validierung

Overrides werden ohne automatische Validierung zurückgegeben (kein Country-Check, kein Proximity-Check). Das ist bewusst — sie existieren genau für Fälle die die automatischen Checks brechen (z.B. Grenzgipfel, historische Namen). Die QA-Karte ist die Kontrollinstanz: ein falsch gesetzter Override fällt durch falsche Koordinaten oder Δ-Abweichung sofort auf.

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

Die `.env`-Datei liegt im Projektverzeichnis neben `requirements.txt`. Hier werden alle Laufzeit-Parameter definiert — insbesondere auch die inputfile-spezifischen Anpassungen wie `EXTRA_DESC_COLS`.

```
ANTHROPIC_API_KEY=sk-ant-...          # Pflicht
GEO_DB_PATH=/Volumes/.../locationer_geo_global.sqlite  # Pflicht (4.5 GB)
TGN_DB_PATH=cache/tgn.sqlite          # Optional (534 MB, lokal oder Volume)
CACHE_PATH=cache/locationer.sqlite    # Default
OVERRIDES_PATH=explicit_list/explicit.sqlite  # Default
NOMINATIM_URL=https://nominatim.openstreetmap.org  # Default
NOMINATIM_USER_AGENT=locationer/4.0 (email)  # Pflicht für public Nominatim
EXTRA_DESC_COLS=PeriodeRAW            # Optional, siehe unten
COLLECTION_BBOX=43.0,62.0,-5.0,20.0  # Optional, siehe unten
```

Alle Pfade können relativ (zum Arbeitsverzeichnis) oder absolut angegeben werden.

### EXTRA_DESC_COLS

Kommagetrennte Liste von Inputspalten, deren Inhalt an die Description angehängt wird — ausschliesslich für **Phase 1a (Location-Extraktion)**.

**Wann nötig:** Phase 1b (Metadaten: Periode, Urheber, Technik) schickt automatisch alle Spalten des Inputfiles an Haiku — ein Feld wie `PeriodeRAW` wird dort also immer gesehen, ohne dass `EXTRA_DESC_COLS` nötig ist. `EXTRA_DESC_COLS` ist nur relevant wenn eine zusätzliche Spalte auch die **Ortsbestimmung** beeinflussen soll (z.B. ein Feld "Aufnahmeort" das nicht in der Description steht).

```
# nur nötig wenn das Feld Ortsinformationen enthält
EXTRA_DESC_COLS=Aufnahmeort
```

Bleibt leer wenn nicht gesetzt (Standardfall).

### COLLECTION_BBOX

Der wichtigste sammlungsspezifische Parameter. Er erfüllt **zwei unabhängige Rollen**, beide greifen nur wenn `country=""` (Haiku hat kein Land erkannt):

**Rolle 1 — Harter Filter:** Jeder Geocoding-Treffer (GeoNames, TGN, Nominatim) wird am Ende gegen die Bbox geprüft. Liegt er ausserhalb → verworfen. Verhindert z.B. dass "Badus" in den Pyrenäen landet wenn kein Land bekannt ist.

**Rolle 2 — Geografischer Prior:** Das Bbox-Zentrum wird als Proximity-Hint an GeoNames übergeben. Bei global mehrdeutigen Namen (z.B. "Matterhorn" existiert in CH und in NZ) wird der Eintrag bevorzugt der dem Bbox-Zentrum am nächsten liegt — ohne dass ein Override nötig ist.

Wenn `country` bekannt ist, greifen die länderspezifischen Bounding Boxes — `COLLECTION_BBOX` ist dann vollständig inaktiv. Bilder aus Ägypten, Südafrika oder Kanada werden korrekt geocodiert solange Haiku das Land erkennt.

**Wie definiere ich die richtige Bbox?**

Faustregel: die Bbox soll den **geografischen Schwerpunkt** der Sammlung abdecken — grosszügig genug für den Randbereich, eng genug um falsche Weltregionen auszuschliessen. Für eine Schweizer Sammlung mit etwas Nachbarländer-Abdeckung:

```
min_lat = südlichster legitimer Breitengrad (Sizilien? Nordafrika?)
max_lat = nördlichster (Skandinavien?)
min_lon = westlichster (Atlantik?)
max_lon = östlichster (Türkei? Russland?)
```

Praktisch: QA-Karte öffnen, alle Score-0-Treffer ausserhalb Europa identifizieren. Die Bbox so eng setzen, dass diese ausgeschlossen sind, aber alle legitimen Treffer drin bleiben.

| Sammlung | Empfohlener Wert | Abdeckung |
|---|---|---|
| ZIN (Schweiz + Europa) | `43.0,62.0,-5.0,20.0` | CH/DE/AT/FR/IT/NO + Westeuropa |
| Nordafrika | `18.0,38.0,-5.0,40.0` | Maghreb + Ägypten |
| Global | *(leer lassen)* | kein Filter, nur Country-Bbox greift |

```
# ZIN-Konfiguration:
COLLECTION_BBOX=43.0,62.0,-5.0,20.0
```

Ablauf bei Treffer ausserhalb der Box (wenn country=""):
→ Treffer verworfen → nächster GeoStack-Step → ggf. Score 0

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

## 12. QA-Karte

Die Karte wird **automatisch nach jedem Lauf** generiert und im Browser geöffnet. Manueller Aufruf:

```bash
python -m v4.map v4/ZIN_complete_geo.csv
```

**Popup-Inhalt:** Titel, City, Country, Score, Periode, Urheber, Δ-Abweichung, CSV-Zeilennummer, klickbare Links (URL-Spalten automatisch erkannt).

**QA-Annotationen:** Jedes Popup enthält:
- ❌ **Fehler** — falsche Koordinaten, muss korrigiert werden
- ⭐ **Wow** — explizit als korrekt verifiziert
- Kommentarfeld — freier Text

Button **⬇ CSV herunterladen** (unten rechts) exportiert alle Annotationen als `annotations_[Name]_[Datum].csv`:

```
csv_zeile, title, lat, lon, fehler, wow, kommentar, reviewer, timestamp
```

Mehrere Personen annotieren lokal je eine eigene CSV, die danach zusammengeführt werden.

**Marker-Farben:** grün=Score 5, hellgrün=4, orange=3, hellrot=2, rot=0

---

## 13. Dateistruktur

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
│   ├── map.py               QA-Karte (Leaflet via folium)
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
