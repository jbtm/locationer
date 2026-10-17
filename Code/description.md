# Locationer V4 — Beschreibung

Geocoding-Pipeline für historische Fotoarchive. Liest CSV/XLSX, extrahiert Ortsinformationen mit Claude Haiku und löst diese zu GPS-Koordinaten auf — ohne Google Places API.

Quellen: GeoNames (CC BY 4.0), Getty TGN (ODC-By), OpenStreetMap/Nominatim (ODbL), länderspezifische Country-DBs (GeoNames) — alle erlauben persistentes Caching und kommerzielle Nutzung.

---

# SO WIRD DER LOCATIONER GESTARTET

**Immer aus `Code/` heraus.** Die Software verankert `cache/`, `explicit_list/`,
`.env`, `v4/TestFile.csv` und `TESTLOG.md` am Arbeitsverzeichnis — aus einem
anderen Ordner aufgerufen findet sie weder Cache noch Overrides.

```bash
cd /Users/dec/Documents/PROGRAMMIEREN/python/Locationer/Code
```

### Rauchprobe — laeuft alles?

```bash
venv/bin/python -m v4 v4/TestFile.csv --limit 10
```

Fragt einmal nach der Collection-Bbox (Enter = uebernehmen), geokodiert zehn
Testdatensaetze und haengt eine Zeile an `TESTLOG.md`.

### Voller Lauf

```bash
caffeinate -i venv/bin/python -m v4 ../ZIN_complete_allFields.csv \
    --output ../zin_2026-09-25.csv
```

`caffeinate -i` verhindert den Ruhezustand — ein ZIN-Lauf dauert Stunden.
Eingabe und Ausgabe liegen eine Ebene hoeher, daher `../`.

**Bricht der Lauf ab, setzt derselbe Befehl an der Abbruchstelle an** (Chunks à
20 Zeilen, `--chunk-size` aendert das). Wer einen Lauf NEU rechnen will, muss die
Ausgabedatei vorher wegraeumen oder einen anderen `--output`-Pfad nehmen.

### Einzelnen Fall anschauen

```bash
venv/bin/python -m v4 v4/TestFile.csv --mode debug --limit 5
```

`--mode debug` zeigt den vollen Entscheidungsweg durch den Geostack statt der
kompakten Tabelle.

### Overrides setzen, wenn ein Ort falsch landet

```bash
venv/bin/python -m v4.overrides list
venv/bin/python -m v4.overrides add "Cresta||Switzerland" 46.453581 9.542744 "Avers GR"
venv/bin/python -m v4.overrides norm-add "Avers" --city "Avers" --country "Switzerland"
venv/bin/python -m v4.overrides remove "Cresta||Switzerland"
```

### Karte zu einem fertigen Ergebnis

```bash
venv/bin/python -m v4.map ../zin_2026-09-06.csv      # -> ../zin_2026-09-06_map.html
```

Der Hauptlauf schreibt die Karte ohnehin mit; das hier ist fuer nachtraeglich
bearbeitete CSV.

### Cache leeren vor einem Neulauf mit geaenderter Logik

```bash
venv/bin/python -m v4.clean_cache --help
```

Ohne das rechnet der Lauf mit alten Zwischenergebnissen weiter und die Aenderung
wirkt nicht.

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
- [ ] **Country-DBs** — optional, aber empfohlen (CH, DE, FR, IT, AT, NO verfügbar). Fehlende Länder werden automatisch übersprungen.

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
- [ ] `[?ambig]`-Treffer prüfen → Score korrekt, aber Stadtzuordnung unsicher
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
venv/bin/python -m v4 v4/TestFile.csv --limit 10

# Voller Lauf mit Chunk-Resuming (sicher bei grossen Dateien)
caffeinate -i venv/bin/python -m v4 ../ZIN_complete_allFields.csv --output ../zin_geo.csv

# Detaillierte Entscheidungsausgabe für Debugging eines einzelnen Records
venv/bin/python -m v4 v4/TestFile.csv --mode debug --limit 5

# Andere GEO DB (z.B. Europa-only)
venv/bin/python -m v4 meinedaten.csv --geo-db /Volumes/LCMT_JBTM/LocationerGeo/locationer_geo_EUROPA.sqlite
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

**Seit 2026-08-23 schreibt der Locationer in der Struktur des Pictomap-Importfiles**, damit die Ausgabe ohne Nacharbeit importiert werden kann. Vorlage ist `Massenupload/Zinggeler-Wehrli/ZIN-TI-SG-LU-ZH_UPLOAD_2026-07-19.csv`.

```
 1–14  Name, Beschreibung, Exakte Koordinaten, Datei, Periode, Quelle,
       Referenz, Urheber:in, Land, Region, Stadt/Ort, Breitengrad,
       Längengrad, Bemerkungen          ← Vorlage, deren Reihenfolge und Schreibweise
   15  Gitter
  16+  übrige Locationer-Spalten: Technik, Coord-Quality-Score, Fallback,
       Ambiguous, Ext-Calls[, Deviation_km], Map
       danach die durchgereichten Eingabespalten
```

### Woher die Werte kommen

| Vorlagenspalte | Quelle |
|---|---|
| `Name` | `Title` — der von Haiku bereinigte Titel |
| `Beschreibung` | `Description` |
| `Periode` | `Periode` (PCTM-normalisiert) |
| `Urheber:in` | `Urheber` |
| **`Land`, `Region`, `Stadt/Ort`, `Breitengrad`, `Längengrad`** | **das Geocoding-Ergebnis** |
| `Exakte Koordinaten`, `Datei`, `Quelle`, `Referenz`, `Bemerkungen` | die Eingabe, unverändert |

Die Ortsangaben kommen bewusst aus dem Geocoding und nicht aus der Eingabe — sonst wäre die Datei beim Reimport wirkungslos. Die Originalwerte gehen nicht verloren: sie werden hinten durchgereicht.

**Namenskonflikte bekommen ein Suffix.** Eine durchgereichte Eingabespalte, deren Name schon vergeben ist, heisst `<Name>_original` (bei weiterer Kollision `_original2`). Bei den ZIN-Eingaben betrifft das `Beschreibung`, `Breitengrad`, `Längengrad` und `Urheber:in`. Ohne das Suffix stünden zwei gleichnamige Spalten in der Datei, und der Import könnte die falsche greifen — also die alte statt der geokodierten Koordinate.

Umgesetzt in `_als_importstruktur()`; die Zuordnung steht in `_UMBENENNEN`, die Vorlagenreihenfolge in `PICTOMAP_SPALTEN`. **Es wird ausschliesslich umbenannt und umsortiert**, keine Werte werden neu berechnet.

> **Die QA-Karte liest intern weiter `Lat`, `Lon`, `Title`, `City`, `Country`.**
> `v4/map.py` legt diese Namen beim Einlesen als Alias auf die Vorlagenspalten,
> damit sowohl neue als auch alte Ausgabedateien funktionieren.

### Zwei offene Punkte vor dem ersten Massenimport — erledigt am 2026-09-06

1. ~~**`Land` ist englisch**~~ → wird jetzt übersetzt, siehe *Ländernamen auf Deutsch*.
2. ~~**`Exakte Koordinaten` wird durchgereicht**~~ → wird jetzt berechnet, siehe
   *Exakte Koordinaten — Punkt oder Fläche*.

**Zu Punkt 2 gehört ein umgekehrter Entscheid.** `write_locationer_results.py`
hält fest, `localization_is_exact` solle bei Locationer-Ergebnissen **nie** wahr
sein — Begründung dort: „Der Locationer liefert den Fotografenstandort, nicht
das abgebildete Objekt."

Dec hat das am 2026-09-06 umgedreht, und die Begründung trägt: bei einem
Objekttreffer liefert der Locationer gerade **nicht** den Fotografenstandort,
sondern die Koordinate des abgebildeten Objekts. Für die Abstandsanzeige in
Pictomap („300 m entfernt") ist das die nützlichere Angabe — man sucht Bilder
von dem, was in der Nähe steht.

> **Folge, die man kennen muss:** `is_exact = 1` löst in Pictomap auf **Prio 2**
> auf (`localization_exact`), und Prio 4 (`localization_virtual`, das Gestreute)
> wird nie erreicht. Präzise heisst damit zwingend: **auf dem Punkt,
> unverschoben, gestapelt.** Mehrere Aufnahmen desselben Bauwerks liegen
> übereinander; im Public Layer wählt der Curator eine aus.
>
> Die saubere Lösung läge darin, die Abstandsanzeige an `gitter` (eine Zahl in
> Metern) zu hängen statt an ein Ja/Nein — „±100 m, 300 m entfernt" wäre auch
> für Ortsmittelpunkte ehrlich. Das ist ein Eingriff in den Directus-Flow und
> ins Frontend, nicht in den Locationer.

`Ambiguous` — `True` wenn der City-Anker mehrdeutig war (mehrere geografisch weit auseinanderliegende Kandidaten). Die Koordinaten sind trotzdem vorhanden (bester Kandidat), aber die Zuordnung ist unsicher. Im human-Output erscheint `[?ambig]`.

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
│  → geocoding_queries (Lokalsprache)   │
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
│  (siehe Abschnitt 5)                  │
│  → Lat, Lon, Quality-Score            │
│  → Ambiguous-Flag                     │
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
| `city` | Stadt oder Ortschaft inkl. Disambiguierungssuffix (z.B. `"Bingen am Rhein"` statt `"Bingen"`) — **nur wenn der Stadtname explizit im Input steht**; nie aus Region oder Geografie inferiert |
| `location` | Spezifischer benannter Ort in Originalsprache (z.B. `"Schloss Vaduz"`, `"Montalin Schulhaus"`) — leer wenn nur Stadt, Portrait oder generische Szene |
| `location_type` | Generischer Gebäudetyp im Originalwort wenn `location` leer (z.B. `"Bahnhof"`, `"Kirche"`) — kombiniert mit city zu Nominatim-Query |
| `geocoding_queries` | 1–3 fertige Nominatim-Suchstrings in Lokalsprache (z.B. `"Kathedrale Genève"` → `["cathédrale Genève", "Kathedrale Genf"]`) — leer wenn `location` leer |

**geocoding_queries** sind der Schlüssel für Sprachflexibilität: Haiku übersetzt den generischen Gebäudetyp in die Lokalsprache und hängt die City an. Damit findet Nominatim POIs auch wenn der Originaltitel auf Deutsch steht aber der OSM-Eintrag auf Französisch/Italienisch/Romanisch ist.

**Wichtige Einschränkung:** Haiku darf in `geocoding_queries` **keine spezifischen Eigennamen erfinden** die nicht im Input stehen. Erlaubt: `"chiesa Trafoi"` (Typ + City in Lokalsprache). Nicht erlaubt: `"Kapelle Unsere Liebe Frau Trafoi"` (erfundener Eigenname).

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

## ZIN-Lauf vom 2026-09-06

`../zin_2026-09-06.csv` (frueher `out/zin_2026-09-06.csv`) — **der erste Lauf mit Importformat, Gitter und
berechneter Exaktheit.** 21.684 Zeilen, 95,7 % mit Koordinate.

```
Score 5  GEO DB / TGN präzis      4.637   22,8 %
Score 4  Nominatim präzis         3.669   18,1 %
Score 3  Stadtzentrum GEO DB     10.805   53,2 %
Score 2  Stadtzentrum Nominatim     329    1,6 %
Score 0  nicht gefunden             884    4,3 %
```

```
Exakte Koordinaten   4.356 von 21.684   (20 %)
Gitter    0 →  4.356    Punkttreffer, nicht verschieben
        100 →  4.487    Flächentreffer (See, Tal, Gebirge)
        400 →  4.830    Ortsmittelpunkt, Dorf
        600 →  3.576
      1.000 →  1.922
      1.600 →    791
      3.000 →    521
      5.000 →     79
       leer →  1.122    ohne Koordinate
```

**`Gitter = 0` steht exakt 4.356-mal — dieselbe Zahl wie
`Exakte Koordinaten = true`.** Die beiden Felder widersprechen sich nirgends.

Stichprobe von Hand:

```
PRÄZISE  0    Pontresina Bahnhof · Schloss Vaduz · Passo dello Stelvio · Matterhorn
unpräzis 100  Zürichsee · Viamala
unpräzis 400  Puria, Ortschaft
unpräzis 600  Bormio · Grindelwald Bahnhofplatz
```

### Zwei Fehler, die dieser Lauf aufgedeckt hat

**`_gitter_m()` hätte 20 Stunden gebraucht.** Die Abfrage suchte über `name`
statt über die indizierte Normalform — 60 Sekunden je Ortsname, bei 1.172
verschiedenen Orten. Die Funktion war geschrieben, aber nie gelaufen, deshalb
hat es niemand gemerkt. Nach der Umstellung auf `name_norm`: 0,0016 s.

**`nominatim.py` stürzte nach 1.371 Zeilen ab.** Nominatim antwortet bei Fehlern
und Drosselung mit einem *Objekt* (`{"error": …}`) statt einer Liste; `not data`
ist dann falsch und das folgende `data[0]` sucht den Schlüssel `0` in einem
dict — `KeyError: 0`. Ein latenter Fehler, der auf diesen Moment gewartet hat.
Beide Fundstellen sind jetzt abgesichert, der Fehler landet in `_last_error`
statt den Lauf zu beenden.

> **Auto-Resume hat getragen:** derselbe Befehl setzte bei Zeile 1.361 an.
> Gesamtdauer rund zwei Stunden, netzgebunden durch Nominatim.

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
           [City-Anker bestimmen]
           find_city(city, country_code, admin1_hint) → (near=(lat,lon), ambiguous)
             ambiguous=True wenn mehrere Kandidaten > 30 km voneinander
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
           Radius: max(city_radius × 2, 5 km) — geo_feature: max(city_radius × 6, 15 km)
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
Step 2.7 Country DB precise (länderspezifische GeoNames-DB)
           Nur wenn Country DB für das erkannte Land vorhanden (CH, DE, FR, IT, AT, NO)
           Vollständige Alternativnamen inkl. Regionalsprachen (Romanisch, Ladinisch,
           Nynorsk, Low German…) — präziser als globale GeoNames-DB
           Radius: max(city_radius × 2, 5 km)
           Graceful skip wenn keine DB für das Land vorhanden
           → Score 5 (kein Fallback)
           ↓ miss
Step 3a  Wikidata (seit 2026-08-16) — VOR Nominatim
           Nur wenn Ortsanker UND location vorhanden
           Suche über `wbsearchentities` — kennt Aliasnamen, die weder GeoNames
           noch Nominatim führen: „Stadttheater Zürich" → Opernhaus,
           „House of Parliament" → Palace of Westminster
           Trifft nur bei exakter Namensgleichheit (Bezeichnung oder Aliasname)
           Dieselben Schranken wie Nominatim: Länder-Rahmen, Regionsabstand
           Bei Treffer entfällt Nominatim
           Abschaltbar mit WIKIDATA=0
           → Score 4 (kein Fallback)
           ↓ miss

           WARUM VOR NOMINATIM: gemessen an 1.009 Bildern mit menschlich
           gesetzter Koordinate — 12 besser / 4 schlechter bei 561 eigenständigen
           Entscheidungen. Als blosser Rückfall wirkungslos (Median 0,26 statt
           0,27 km), weil Nominatim fast immer IRGENDETWAS liefert, auch wenn es
           7 km danebenliegt. Genau solche Fälle soll Wikidata abfangen.
           ↓
Step 3   Nominatim (OpenStreetMap) — bounded wenn City-Anker vorhanden
           Nur wenn city ODER location bekannt (kein Aufruf ohne Anker)
           Generische Titel ohne location_type → überspringen (→ Step 4)

           MIT City-Anker (near bekannt):
             search_bounded(location, near, city_radius) — Ergebnis innerhalb Radius garantiert
             → Retry: search_bounded(location + city, near, city_radius)
             → Retry: search_bounded(geocoding_queries[i], near, city_radius) — Lokalsprache!
             Bounded-Ergebnisse brauchen keinen Proximity-Check (bereits eingegrenzt)

           OHNE City-Anker:
             Nominatim wird nicht aufgerufen — kein Anker, kein zuverlässiges Ergebnis.
             Ausnahme: Geo-Features (Berg, Pass, Gletscher…) → GEO DB / TGN in Step 2/2.5
             (Score 5 wenn Treffer, Score 0 wenn nicht)

           Präzis-Treffer → Score 4 | Nicht-präzis → weiter zu Step 4
           (Wikidata und Nominatim teilen sich diesen Pfad, geostack.py:631)
           ↓ miss oder nicht präzis
Step 4   GEO DB city (Stadtzentrum)
           find_city(city, country_code, admin1_hint)
           Gibt (city_row, ambiguous) zurück — ambiguous propagiert in GeoResult
           Bbox-Check: Treffer ausserhalb Landesgrenzen → verwerfen
           admin1_hint aus region-Feld → disambiguiert gleichnamige Orte
           Typ P + ADM3/ADM4-Fallback
           → Score 3 (Fallback = True)
           ↓ miss
Step 4.5 Country DB city (Stadtzentrum via Country DB)
           Fallback wenn GEO DB Stadt nicht kennt
           → Score 3 (Fallback = True)
           ↓ miss
Step 5   Nominatim city-only
           Nicht-präziser Nominatim-Treffer aus Step 3 → Score 2 (Fallback = True)
           oder früh geholter city-Anker via Nominatim → Score 2
           ↓ miss
→          Score 0 — nicht gefunden
```

### Bounded Nominatim

Wenn ein City-Anker bekannt ist (`near=(lat, lon)`), wird Nominatim mit `viewbox` + `bounded=1` aufgerufen. Das bedeutet: Nominatim liefert **nur Ergebnisse innerhalb des Radius**, egal wie unklar der Query-String ist.

Praktische Konsequenz: `"Kathedrale"` (Deutsch) gebounded auf Genève → findet `"Cathédrale Saint-Pierre"`. Ohne Bounded würde Nominatim `"Cathédrale Notre-Dame de Paris"` zurückgeben.

Die Bounded-Queries haben eigene Cache-Keys (inkl. Koordinaten und Radius) — sie stören bestehende unbounded Cache-Einträge nicht.

### Ambiguous City

`find_city()` gibt neu `(row, is_ambiguous)` zurück. `is_ambiguous=True` wenn der zweitbeste Kandidat > 30 km vom besten Kandidaten liegt — das deutet auf zwei verschiedene Orte mit demselben Namen hin (z.B. `"Gardone"` → Gardone Riviera oder Gardone Val Trompia).

**Verhalten:** Die Pipeline nimmt trotzdem den besten Kandidaten (höchste Population + region-Hint), setzt aber `GeoResult.ambiguous=True`. Im Output: `[?ambig]` im human-Modus, `Ambiguous=True` im CSV. Score und Koordinaten bleiben unverändert.

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

| Stadttyp | Beispiel | Radius |
|---|---|---|
| Weiler | Flerden (~150) | 2 km |
| Kleinstadt | Thusis (~3000) | 3 km |
| Regionalstadt | Chur (~36k) | 8 km |
| Grossstadt | Zürich (~415k) | 15 km |
| Metropole | Paris (~2.1M) | 25 km |

Geografische Features (Schluchten, Pässe, Berge, Seen — erkannt durch Keyword-Matching auf `location`) erhalten 4–6× mehr Radius, weil sie naturgemäss ausserhalb des Siedlungsgebiets liegen. Gebäude (Hotels, Kirchen, Brücken) bleiben beim engen Stadtradius.

### Cache-Validierung

Jeder Cache-Hit wird vor der Rückgabe gegen die aktuellen Checks validiert:
1. **Bbox-Check** (Country oder Collection) — verwirft Pakistan/Vietnam-Altlasten
2. **Proximity-Check** für alle Quellen (GEO DB, TGN, Country DB, Nominatim) — verwirft Altlasten wo der Treffer zu weit von der bekannten City entfernt liegt. Radius: `city_radius × 6` für Geo-Features, `city_radius × 2` für normale Orte.
3. **Fallback-Bypass** — wenn `location` bekannt ist und der Cache nur einen Fallback (Score 3) enthält, wird neu geocodiert damit Nominatim/GeoNames einen präzisen Treffer versuchen kann

**Audit-Fähigkeit:** `geo_cache` speichert neben den Koordinaten auch `location_hint`, `city_hint`, `country_hint` — die Rohkomponenten des Cache-Keys. Damit kann ein zukünftiges Audit-Tool Einträge auf Plausibilität prüfen (Distanz zwischen Koordinaten und bekannter City) ohne den Hash rückwärts auflösen zu müssen.

### Region-Disambiguierung

`region` (aus Phase 1a) wird via `region_to_admin1()` in einen GeoNames `admin1`-Code aufgelöst (`admin1_hint`). Dieser wird an drei Stellen verwendet:

1. `find_city()` — Tiebreaker bei gleichnamigen Orten in verschiedenen Kantonen (z.B. `city="Lohn"`, `region="Graubünden"` → GR-Lohn statt SH-Lohn)
2. `find_precise()` — bevorzugt Kandidaten im richtigen Kanton + mehr Alt-Namen (Fame-Proxy), z.B. Piz Nair GR statt Piz Nair UR
3. Nominatim-Query — wenn kein Stadtanker, wird Region in den Query aufgenommen (z.B. `"Flüelastrasse Graubünden Switzerland"`)

Haiku inferiert Region auch aus geographischem Kontext, wenn sie nicht explizit genannt ist (z.B. `"Berninagruppe"` oder `"Corvatsch"` → `"Graubünden"`).

### Quality-Score

| Score | Quelle | Fallback | Bedeutung |
|---|---|---|---|
| 5 | GEO DB / TGN / Country DB | Nein | Präziser Named Place |
| 4 | Nominatim | Nein | Präziser externer Treffer |
| 3 | GEO DB / Country DB / Override | Ja | Stadtzentrum |
| 2 | Nominatim | Ja | Stadtzentrum via Nominatim |
| 0 | — | — | Nicht gefunden |

`Ambiguous=True` kann bei jedem Score auftreten — es beschreibt die Qualität des City-Ankers, nicht des Treffers selbst.

### Exakte Koordinaten — Punkt oder Fläche

`Exakte Koordinaten` sagt, ob ein **punktförmiges Objekt** gefunden wurde. Nur
dann ist die Koordinate eine Verortung; sonst ist sie der Schwerpunkt einer
Fläche, und dafür gibt es `gitter`.

```
Exakte Koordinaten = kein Fallback  UND  Score ≥ 4  UND  Typ ist punktförmig
```

| | |
|---|---|
| **Punkt** | Bauwerke — Schloss, Bahnhof, Kirche, Hotel, Brücke · Gipfel und Pässe |
| **Fläche** | **alle** Siedlungen · Gewässer · Täler, Schluchten, Gebirgszüge · Inseln · Parks, Regionen, Verwaltungseinheiten |

Siedlungen sind ausnahmslos Fläche (Decs Entscheid 2026-09-06). Das kommt ohne
Grössenschwelle aus: der Unterschied zwischen Fuldera und Zürich steckt im
`gitter`-Radius, nicht in einer willkürlichen Grenze zwischen Weiler und Dorf.

**Der Typ kommt aus der Quelle selbst, nicht aus dem Namen.** Eine Wortliste
(„see", „tal", „berg") hinge an der Sprache und scheiterte schon an deutschen
Zusammensetzungen — „Zürichsee" ist ein Token, nicht „Zürich" + „see". Die
Vokabulare der Quellen sind dagegen endlich und sprachunabhängig: „Lago di
Como" und „Zürichsee" sind beide `H.LK`.

```
GeoNames     feature_class + feature_code   Klasse S, dazu PK MT HLL VLC PASS
Nominatim    place_type                     aus nominatim_cache, zu 100 % gefüllt
TGN          place_type                     aus tgn_place
Wikidata     —                              nur label gespeichert
```

Umgesetzt in `v4/objekttyp.py`. **Kein neues Feld auf `GeoResult`, keine
Neu-Geokodierung**: der Typ wird beim Schreiben der Zeile nachgeschlagen, aus
denselben persistenten Speichern, aus denen die Koordinate kam. Schlüssel ist,
was `GeoResult` ohnehin trägt — `source`, `match_name`, `lat`, `lon`.

> **Falle bei den Abfragen.** Es gibt drei Schreibvarianten, und keine einzelne
> trifft alle Spalten:
>
> ```
> name              Zürichsee
> name_norm         zürichsee     klein, Umlaute BLEIBEN
> ascii_name_norm   zuerichsee    ascii_norm_de, ü→ue
> ascii_norm()      zurichsee     trifft KEINE der beiden
> ```
>
> Und: nur `name_norm` und `ascii_name_norm` sind indiziert. Eine Abfrage auf
> `name` läuft als voller Durchlauf über 13,4 Mio. Zeilen — **11,6 s statt
> 0,001 s**, gemessen am 2026-09-06. Dasselbe bei TGN: dort gibt es keinen
> Koordinatenindex, wohl aber `idx_tgn_place_norm`.

**Letzter Rückfall: der nächstgelegene GeoNames-Eintrag im Umkreis von 60 m.**
Für Quellen ohne eigenen Typ — Wikidata und die Länder-DBs. Der Gedanke ist
Decs: Wikidata findet den Ort über einen *Aliasnamen*, unter dem GeoNames ihn
nicht kennt. Der Name geht also fehl, die **Koordinate** nicht — beide zeigen
auf dasselbe reale Objekt.

```
Bahnhof Pontresina  →  Pontresina station           S.RSTN   12 m
Bahnhof St. Moritz  →  St. Moritz Railway Station   S.RSTN   58 m
```

Zwei Regeln machen das belastbar. **Der nächstgelegene zählt, nicht der
erstbeste** — ein erster Entwurf ging die Klassen in fester Reihenfolge durch
und bevorzugte damit systematisch „Punkt". Und **der Radius ist eng**; gemessen
über 852 Wikidata-Treffer, wie oft das Urteil kippte, hätte man den
zweitnächsten genommen:

```
 15 m    11 % Abdeckung     5 % wackelig
 60 m    39 %               8 %      ← gewählt
100 m    55 %              15 %
150 m    68 %              21 %
```

Über 60 m trifft die Abfrage, was zufällig danebensteht: „Maggiadelta" fand ein
Bauernhaus in 119 m, „rifugio Tabaretta" einen Gipfel in 101 m. Bei 60 m fallen
beide heraus, die Bahnhöfe bleiben.

**Ergebnis, gemessen an 4.215 präzisen Treffern im Cache** (1,6 s):

```
                PUNKT   Fläche
nominatim        1.028      518
geo_db             690      258
wikidata           257      595      ← vorher 0, nur über den Rückfall
country_db         115      438
tgn                 70      246
                 -----
als präzise      2.160   von 4.215   (51 %)
```

„Fläche" fasst zweierlei zusammen: einen erkannten Flächentyp und *nichts
gefunden*. Beides führt zu `false` — **ein unbekannter Typ darf keine
Genauigkeit behaupten, die niemand geprüft hat.**

Ein konservativer Fehlschlag zum Kennen: „Rathaus Konstanz" gilt als Fläche,
weil in 28 m die Altstadt (`P.PPLX`) liegt und näher ist als das Rathaus selbst.

**Offen:** Wikidata hätte mit `P31` die beste Typinformation von allen. Sie zu
holen und in `wikidata_cache` abzulegen würde diesen Rückfall überflüssig machen
und die 595 Wikidata-Flächen prüfbar.

### Ländernamen auf Deutsch

`Land` wird über `LAND_DE` übersetzt (`__main__.py`). Grund: in Pictomap wurde
`country` am 2026-08-22 auf deutsche Namen vereinheitlicht — ein Reimport mit
englischen Werten machte das rückgängig. Unbekanntes bleibt unverändert stehen,
statt zu verschwinden: eine Lücke fällt beim Sichten auf, ein stilles Weglassen
nicht.

> **Nicht gelöst: die Ortsnamen.** `City` hat 1.172 verschiedene Werte —
> „Firenze" oder „Florenz", „Praha" oder „Prag" ist offen (Dec, 2026-09-06:
> „kein Sprachkonzept für fremde Objekte"). Die Ländertabelle löst 17 Werte,
> nicht das Problem dahinter.

### Gitter — der Unschärferadius

`Gitter` sagt in Metern, wie sicher die gefundene Koordinate ist. Der Wert wandert über den Import in das Feld `gitter` auf `images` und ersetzt dort die Schätzung, die der LayerCurator früher bei jedem Lauf neu aus der Einwohnerzahl zog. **Wer die Koordinate gefunden hat, weiss am besten, wie sicher sie ist** — deshalb gehört die Angabe hierher und nicht in den Curator.

| Fall | `Gitter` |
|---|---|
| Objekttreffer (Score ≥ 4, kein Fallback) | `100` — ein benanntes Bauwerk, ein Platz, eine Brücke |
| Ortsmittelpunkt (Fallback) | CityRadius aus der Einwohnerzahl: >500k → 5000 m, >100k → 3000, >20k → 1600, >5k → 1000, >1k → 600, sonst 400 |
| nichts gefunden (Score 0) oder keine Koordinate | *(leer)* — der Curator nimmt dann CityRadius/2 |

Die 100 m sind eine **Festlegung, keine Messung**. Ein Versuch, sie aus 1016 menschlich gesetzten Koordinaten abzuleiten, scheiterte am 2026-08-21 daran, dass 70 % dieser Referenzen selbst geteilte Sammelpunkte sind: der Prüfsatz misst dort, wie gut ein Verfahren einen Sammelpunkt reproduziert, und belohnt ausgerechnet den Ortsmittelpunkt.

Für die Einwohnerzahl zählt der **Ort, auf den zurückgefallen wurde** — nicht der, der der Koordinate zufällig am nächsten liegt. Er steht in `geo.match_name` und wird über den Namen nachgeschlagen; da die Koordinate sein Mittelpunkt IST, ist er über die Nähe eindeutig bestimmt, Homonyme können nicht greifen. Stadtquartiere (`PPLL`/`PPLX`) bleiben aussen vor.

> **Korrektur 2026-09-06.** Hier stand, es werde der nächstgelegene Ort genommen. Das war ein Fehlgedanke und ist seit dem 2026-08-25 auch im Code nicht mehr so. Gemessen: in derselben 0,1-Grad-Zelle (~11 km) liegen Zürich mit 415'367 Einwohnern und Weiler mit 18; in 60 von 350 Zellen unterschieden sich die Orte um Faktor 5. Zwei Bilder wenige Kilometer auseinander bekamen so 400 und 3'000 Meter.

Umgesetzt in `_gitter_m()` und `_city_radius_m()`.

> **Der Wert ist eine Obergrenze, keine Distanz.** Der Curator startet am wahren
> Punkt und rückt nur aus, wenn es eng ist. Bei rund der Hälfte aller Punkte liegt
> ohnehin nur ein Bild — dort bleibt es exakt liegen.

---

## 6. Country-DBs — länderspezifische Datenbanken

Ergänzung zur globalen GeoNames-DB mit vollständigen Alternativnamen pro Land (Romanisch, Ladinisch, Friulanisch, Nynorsk, Low German…). Schneller lokaler Ersatz für viele Nominatim-Calls.

**Graceful degradation:** Fehlt eine Country-DB für ein Land, wird Step 2.7 still übersprungen — die Pipeline läuft ohne Unterbruch weiter.

### Import

```bash
# Einzelnes Land
python -m v4.import_country CH --output /path/to/ch_country.sqlite

# Mehrere Länder auf einmal
python -m v4.import_country CH DE FR IT AT NO --output-dir /path/to/dir/

# Statistik anzeigen
python -m v4.import_country CH --check

# .env eintragen (nicht vorhandene Einträge werden ignoriert)
COUNTRY_DB_CH=/path/to/ch_country.sqlite
COUNTRY_DB_DE=/path/to/de_country.sqlite
...
```

### Unterstützte Länder

| Code | Quellsprachen | Grösse |
|---|---|---|
| CH | Romanisch, Französisch, Italienisch, Deutsch | ~11 MB |
| DE | Low German, Kölnisch, Bairisch | ~28 MB |
| FR | Okzitanisch, Bretonisch, Korsisch, Elsässisch | ~23 MB |
| IT | Deutsch (Südtirol), Ladinisch, Friulanisch | ~16 MB |
| AT | Bairisch | ~6.6 MB |
| NO | Nynorsk, Bokmål, Sami | ~78 MB |

---

## 7. Cache-Architektur

Alle Caches sind persistente SQLite-Dateien. Ergebnisse werden einmalig berechnet und danach direkt abgerufen.

| Cache-Tabelle | Inhalt | Datei |
|---|---|---|
| `norm_cache` | Phase 1a Ergebnisse (title/country/region/city/location/geocoding_queries) | `cache/locationer.sqlite` |
| `meta_cache` | Phase 1b Ergebnisse (periode/urheber/technik) | `cache/locationer.sqlite` |
| `geo_cache` | Phase 2 Ergebnisse (Koordinaten + Score + ambiguous) | `cache/locationer.sqlite` |
| `nominatim_cache` | Nominatim-Antworten (inkl. bounded-Queries mit eigenem Key) | `cache/locationer.sqlite` |
| TGN-Datenbank | 2.99 Mio. Orte aus Getty TGN | `cache/tgn.sqlite` |
| Country-DBs | Länderspezifische Alternativnamen | `<dir>/{cc}_country.sqlite` |
| Overrides | Manuelle Koordinaten + Normalisierungen | `explicit_list/explicit.sqlite` |

### Bounded Nominatim Cache

Bounded-Suchanfragen werden mit einem eigenen Cache-Key gespeichert (`bounded|lat,lon,radius|query`), damit sie unabhängig von unbounded-Einträgen verwaltet werden können. Bounded Misses werden nicht gecacht — erlaubt Retry bei geändertem Radius.

### Cache leeren — wann was

| Cache | Wann leeren |
|---|---|
| `norm_cache` | Wenn Haiku-Prompt geändert wurde |
| `geo_cache` | Wenn GeoStack-Logik geändert wurde (Radii, bounded search) |
| `nominatim_cache` | Selten — nur bei fundamentalen API-Änderungen |
| `meta_cache` | Fast nie — Periode/Urheber/Technik ändern sich nicht |

### Auto-Resume

`--chunk-size 20` ist der Default — d.h. jeder Lauf schreibt alle 20 Zeilen auf Disk und kann nach einem Crash oder Unterbruch nahtlos fortgesetzt werden. Einfach **denselben Befehl nochmals ausführen** — die Pipeline erkennt die bestehende Output-Datei und macht ab der nächsten unverarbeiteten Zeile weiter.

```bash
# Erster Lauf (oder Resume nach Unterbruch — gleicher Befehl):
python -m v4 v4/ZIN_complete.csv
```

---

## 8. Overrides — manuelle Korrekturen

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

## 9. TGN-Datenbank — Import

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

## 10. Konfiguration (.env)

Die `.env`-Datei liegt im Projektverzeichnis neben `requirements.txt`. Hier werden alle Laufzeit-Parameter definiert — insbesondere auch die inputfile-spezifischen Anpassungen wie `EXTRA_DESC_COLS`.

```
ANTHROPIC_API_KEY=sk-ant-...          # Pflicht
GEO_DB_PATH=/Volumes/.../locationer_geo_global.sqlite  # Pflicht (4.5 GB)
TGN_DB_PATH=cache/tgn.sqlite          # Optional (534 MB, lokal oder Volume)
CACHE_PATH=cache/locationer.sqlite    # Default
OVERRIDES_PATH=explicit_list/explicit.sqlite  # Default
NOMINATIM_URL=https://nominatim.openstreetmap.org  # Default
NOMINATIM_USER_AGENT=locationer/4.0 (email)  # Pflicht für public Nominatim

# Country-spezifische DBs (optional — fehlende werden ignoriert)
COUNTRY_DB_CH=/path/to/ch_country.sqlite
COUNTRY_DB_DE=/path/to/de_country.sqlite
COUNTRY_DB_FR=/path/to/fr_country.sqlite
COUNTRY_DB_IT=/path/to/it_country.sqlite
COUNTRY_DB_AT=/path/to/at_country.sqlite
COUNTRY_DB_NO=/path/to/no_country.sqlite

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

| Sammlung | Empfohlener Wert | Abdeckung |
|---|---|---|
| ZIN (Schweiz + Europa) | `43.0,62.0,-5.0,20.0` | CH/DE/AT/FR/IT/NO + Westeuropa |
| Nordafrika | `18.0,38.0,-5.0,40.0` | Maghreb + Ägypten |
| Global | *(leer lassen)* | kein Filter, nur Country-Bbox greift |

---

## 11. Testmetrik

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
| S5%              | Anteil Score-5-Treffer (GEO DB / TGN / Country DB precise)
| Ext              | Anzahl Nominatim-Calls (nicht aus Cache)
| n/a→∅            | Zeilen ohne Referenzkoordinaten (ok/bad)
```

---

## 12. Kostenabschätzung

| Datenmenge | Claude Haiku (Phase 1a+1b) | TGN | Nominatim | Total |
|---|---|---|---|---|
| 1'000 Zeilen | ~$0.10 | $0 | $0 | ~$0.10 |
| 700'000 Zeilen | ~$70 | $0 | $0* | ~$70 |

Haiku-Calls: ceil(N/20) pro Phase × 2 Phasen. Bei 1000 Zeilen ~100 Calls. Gemessene Kosten ZIN_1000: ~$0.10.

*Nominatim public: 1 req/s → bei 700k unique Queries ~194h. Self-hosted empfohlen für grosse Volumen. Cache hält alle Ergebnisse — Wiederholungsläufe kosten nichts. Bounded-Queries haben eigene Cache-Keys und werden separat gecacht.

### Output-Anzeige

Pro Zeile: `[N:n]` zeigt kumulierte Nominatim-Calls, `[?ambig]` zeigt mehrdeutige City-Zuordnung. Am Ende:
```
Haiku calls (Phase 1a+1b): 2
Nominatim calls:           14
```

---

## 13. QA-Karte

Die Karte wird **automatisch nach jedem Lauf** generiert und im Browser geöffnet. Manueller Aufruf:

```bash
python -m v4.map v4/ZIN_complete_geo.csv
```

**Popup-Inhalt:** Titel, City, Country, Score, Periode, Urheber, Δ-Abweichung, CSV-Zeilennummer, klickbare Links (URL-Spalten automatisch erkannt). `[?]`-Badge bei ambiguous.

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

## 14. Dateistruktur

**Umbau 2026-09-24:** `locationer-v4/` ist aufgeloest. Code und Metadaten liegen
in `Locationer/Code/`, Eingabe und Ergebnisse eine Ebene hoeher in `Locationer/`.
Aufgerufen wird IMMER aus `Code/` heraus — die Software verankert `cache/`,
`explicit_list/`, `.env`, `v4/TestFile.csv` und `TESTLOG.md` am
Arbeitsverzeichnis, nicht am Ort des Codes. Darum war am Programm selbst keine
Zeile zu aendern; nur die Pfade auf der Kommandozeile sind andere.

```
Locationer/
├── ZIN_complete_allFields.csv   Eingabe (versioniert — ohne sie laeuft nichts)
├── zin_2026-09-06.csv           Ergebnisse (nicht versioniert)
├── zin_2026-09-06_wikidata.csv
├── zin_2026-09-06_map.html
├── lauf_2026-09-06.log
├── Archiv/                      aeltere Laeufe, Kantone.xlsx, Mappe2.csv
├── LocationerGeo/               Geo-Rohdaten und lokale Indizes
└── Code/
│   ├── __main__.py          Einstiegspunkt, CLI, Ausgabe
│   ├── input_normalizer.py  Phase 1a/1b/1c (Haiku + TGN-Lookup)
│   ├── geostack.py          Phase 2: Entscheidungsbaum
│   ├── geo_db.py            GeoNames SQLite Interface
│   ├── country_db.py        Country-DB Interface (CH/DE/FR/IT/AT/NO)
│   ├── import_country.py    Country-DB Import (GeoNames → SQLite)
│   ├── tgn_db.py            TGN SQLite Interface
│   ├── import_tgn.py        TGN Import (ZIP → SQLite)
│   ├── nominatim.py         Nominatim REST API + Cache (inkl. search_bounded)
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
├── venv/                    Python 3.11, 7 Pakete (requirements.txt)
├── requirements.txt
└── TESTLOG.md               Messhistorie
```

### Aufruf nach dem Umbau

```bash
cd /Users/dec/Documents/PROGRAMMIEREN/python/Locationer/Code

venv/bin/python -m v4 v4/TestFile.csv --limit 10          # Rauchprobe
caffeinate -i venv/bin/python -m v4 ../ZIN_complete_allFields.csv \
    --output ../zin_2026-09-25.csv                        # voller Lauf
```

`venv/` wurde neu erzeugt statt verschoben: in `bin/activate` und den
pip-Startzeilen stehen absolute Pfade, ein verschobenes venv ist halb kaputt.

Die `.gitignore` in der Wurzel musste mit: die alten Muster `**/out/*.csv` und
`**/v4/ZIN_..._geo*` zeigten nach dem Umzug ins Leere, und `zin_2026-09-06.csv`
stand damit ungeschuetzt da. Jetzt an der Wurzel verankert (`/zin_*.csv`).

---

## 2026-10-16/17 — Rückfluss aus Pictomap, harte Region, Override-Regel

### Rückfluss (Stufe 0.5)

`v4/pictomap_ref.py`.  Bilder, die in Pictomap per Timeslide oder von Hand
**präzise** verortet wurden, geben ihren Standort an neue Bilder mit demselben
Motiv (`Objekt | Stadt | Land`) weiter.  Stufe 0.5: nach den Overrides, vor dem
Cache.  Nie `Exakte Koordinaten`; `Gitter` = Streuung der Referenzpunkte,
mindestens 100 m; Streuung > 1 km → verworfen.  Ortsansichten (Objekt = Stadt)
und Bilder ohne Objekt nehmen nicht teil.

```bash
venv/bin/python -m v4.pictomap_ref sync     # vor einem Lauf; nur lesend
venv/bin/python -m v4.pictomap_ref stats
PICTOMAP_REF=0                              # abschalten
```

**Nur präzise Punkte (Variante A).**  Importierte Koordinaten fliessen nicht
zurück, auch präzise nicht: bei 1726 von 1831 „manuellen" Verortungen steht
schlicht die Import-Koordinate, und auch präzise Importe tragen Sammelpunkte
(Zürich 47.37289/8.52903 mit 52 Bildern und 20 Motiven, als präzise markiert —
Rest der Müstair-Reparatur vom August).  Gemessen an ZIN (eigene ID
ausgeschlossen, n = 60 mit Wahrheit): Median-Fehler 169 m → 69 m, 34 besser,
9 schlechter; 303 Treffer.

### Region ist eine harte Grenze — wie das Land

Vorher war die Region in den Länder-DBs nur ein Wunsch (`country_db.py`
suchte ohne Region weiter) und im Stadt-Rückfall wirkungslos: „Münster, GR"
landete in Münster VS, „Müstair" mit Region Zürich 161 km ausserhalb.  Jetzt
prüft jede Stufe (GeoNames und Länder-DBs exakt über `admin1`, sonst Abstand
zum Kantonsmittelpunkt ≤ Kantonsradius), dazu eine letzte Sicherung in
`geocode()`.  Gilt für Region aus der Eingabe und von Haiku gleichermassen.
**Findet sich in der Region nichts, gibt es keine Koordinate** — kein
Kantonsmittelpunkt (Entscheid Dec).

### Norm-Overrides: ganze Wörter, keine fremde Stadt ersetzen

`explicit_store.match_norm`.  Der Override `Münster → Müstair` (seit 2026-05-09)
traf als Teilstring Gross-, Frau-, Berner, Basler Münster und Münster VS: im
ZIN-Lauf 2026-09-06 lagen 266 Zeilen auf Müstair, davon 23 zu Recht.
Gelöscht; neu `Münstertal → Val Müstair`.  Neue Regel: nur ganze Wörter, und
eine Stadt-Korrektur greift nur, wenn das Muster ein Wort mit der schon
vorhandenen Stadt teilt.  Gegen ZIN geprüft: 26 Treffergruppen ändern sich,
alle waren Fehlgriffe („Lenz" traf Koblenz, „Uors" Trais FlUors, „Mühlen"
Mühlengebäude, Sur/Tiefencastel/Mon wegen eines Nachbarorts in der
Beschreibung).  Alle 234 ZIN-Zeilen mit „Münster" landen jetzt richtig.

### Offen

- **Testmetrik misst nicht mehr.**  `metrics.py` liest `Lat`/`Lon`; seit dem
  Importformat (2026-08-23) heissen sie `Breitengrad`/`Längengrad` → jede
  Zeile „kein".  Von Hand nachgerechnet gegenüber Juni: 3 besser, 3 ohne
  Treffer (Schloss Chillon, Bahnhof „Lzern", Flüelastrasse) — **nicht** durch
  die Region verursacht (Gegenversuch ohne Regionsprüfung: gleich), sondern
  weil Haiku dort heute keine Stadt liefert und ohne Stadtanker Wikidata und
  Nominatim übersprungen werden.
- Zeilen mit „Lokalisierung unsicher" bekommen weiterhin eine Stadt aus
  Norm-Overrides (z. B. „Puschlav oder Münstertal?" → Val Müstair).
- Pictomap trägt die alten Fehler noch: 53 Bilder `city = Müstair` ohne Bezug
  (41 aus Bern), 31 Zürich/Bern/Basel-Bilder mit Koordinate bei Müstair, 52
  Bilder am Zürcher Sammelpunkt mit `localization_is_exact = True`.
