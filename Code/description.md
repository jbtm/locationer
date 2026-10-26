# Locationer — Beschreibung

Geocoding-Pipeline für historische Fotoarchive. Liest CSV/XLSX, extrahiert Ortsinformationen mit Claude Haiku und löst diese zu GPS-Koordinaten auf — ohne Google Places API.

Quellen: GeoNames (CC BY 4.0), Getty TGN (ODC-By), OpenStreetMap/Nominatim (ODbL), länderspezifische Country-DBs (GeoNames) — alle erlauben persistentes Caching und kommerzielle Nutzung.

---

# SO WIRD DER LOCATIONER GESTARTET

**Immer aus `Code/` heraus.** Die Software verankert `cache/`, `explicit_list/`,
`.env`, `locationer/TestFile.csv` und `TESTLOG.md` am Arbeitsverzeichnis — aus einem
anderen Ordner aufgerufen findet sie weder Cache noch Overrides.

```bash
cd /Users/dec/Documents/PROGRAMMIEREN/python/Locationer/Code
```

### Rauchprobe — laeuft alles?

```bash
venv/bin/python -m locationer locationer/TestFile.csv --limit 10
```

Fragt einmal nach der Collection-Bbox, geokodiert zehn Testdatensaetze und
haengt eine Zeile an `TESTLOG.md`. Die Collection-Bbox ist ein grober
geografischer Rahmen der ganzen Sammlung (aus `COLLECTION_BBOX` in `.env`,
derzeit `43.0,62.0,-5.0,20.0` = Schweiz + Europa) und wirkt im Geostack als
Plausibilitätsfilter, sobald kein Land ermittelbar ist.

Die Frage hat zwei Formen, je nachdem ob `.env` einen Wert hat:

- **Wert gesetzt** — `Passt das für diese Sammlung? [Y/n/-=ohne Filter/neuer Wert]`
  - Enter / `y` / `j`: Wert aus `.env` übernehmen
  - `min_lat,max_lat,min_lon,max_lon`: anderer Rahmen, nur für diesen Lauf
  - `-`: ohne Filter, nur für diesen Lauf (`.env` bleibt unverändert)
  - `n`: **bricht den Lauf ab** (danach `.env` anpassen). `n` heisst nicht
    „ohne Filter weiter" — dafür `-`.
- **Kein Wert gesetzt** — `COLLECTION_BBOX setzen? [Enter=weiter / …]`
  - Enter: ohne diesen Filter weiter
  - `min_lat,max_lat,min_lon,max_lon`: Rahmen nur für diesen Lauf

Sammlungen ausserhalb Europas (z. B. DISCO, Persien/Iran) brauchen einen
eigenen Rahmen, etwa `25.0,40.0,44.0,64.0` für Iran — sonst verwirft der
Filter jeden Treffer, bei dem Haiku kein Land erkennt.

### Voller Lauf

```bash
caffeinate -i venv/bin/python -m locationer ../ZIN_complete_allFields.csv \
    --output ../zin_2026-09-25.csv
```

`caffeinate -i` verhindert den Ruhezustand — ein ZIN-Lauf dauert Stunden.
Eingabe und Ausgabe liegen eine Ebene hoeher, daher `../`.

**Jeder Lauf ist ein Auftrag** mit eigenem Verzeichnis `<ausgabe>.auftrag/` neben
der Ausgabe (Kopie der Eingabe, `auftrag.json`, Teildateien je Paket à 20 Zeilen).
**Bricht der Lauf ab, setzt derselbe Befehl genau diesen Auftrag fort** — nach
Pruefung, dass die Eingabe dieselbe ist und jede Teildatei Zeile fuer Zeile zu
ihr passt. Eine Ausgabedatei ohne Auftrag wird nie ueberschrieben. Wer NEU rechnen
will, nimmt einen anderen `--output`-Pfad.

- `--workers 6` (Vorgabe): so viele Pakete gleichzeitig; `--mode debug` immer 1.
- `--grenze-chf 20` (Vorgabe aus `LOCATIONER_GRENZE_CHF`): Kostengrenze des
  Auftrags. Vor dem Start wird geschaetzt; liegt die Schaetzung darueber, startet
  nichts. Waehrend des Laufs haelt der Auftrag an, sobald die Kosten die Grenze
  erreichen oder die Hochrechnung (ab 1000 Zeilen) darueber liegt. Fortsetzen mit
  demselben Befehl und hoeherem `--grenze-chf`.
- Die Rauchprobe mit `locationer/TestFile.csv` (ohne `--output`) faengt immer neu
  an — sie soll den aktuellen Code messen.

### Einzelnen Fall anschauen

```bash
venv/bin/python -m locationer locationer/TestFile.csv --mode debug --limit 5
```

`--mode debug` zeigt den vollen Entscheidungsweg durch den Geostack statt der
kompakten Tabelle.

### Overrides setzen, wenn ein Ort falsch landet

```bash
venv/bin/python -m locationer.overrides list
venv/bin/python -m locationer.overrides add "Cresta||Switzerland" 46.453581 9.542744 "Avers GR"
venv/bin/python -m locationer.overrides norm-add "Avers" --city "Avers" --country "Switzerland"
venv/bin/python -m locationer.overrides remove "Cresta||Switzerland"
```

### Karte zu einem fertigen Ergebnis

```bash
venv/bin/python -m locationer.map ../zin_2026-09-06.csv      # -> ../zin_2026-09-06_map.html
```

Der Hauptlauf schreibt die Karte ohnehin mit; das hier ist fuer nachtraeglich
bearbeitete CSV.

### Cache leeren vor einem Neulauf mit geaenderter Logik

```bash
venv/bin/python -m locationer.clean_cache --help
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

COLLECTION_BBOX=25,72,-15,45           # Europa mit Mittelmeer, Nordafrika, Nahost (Server seit 2026-10-26)
# COLLECTION_BBOX=43.0,62.0,-5.0,20.0  # Schweiz + Mitteleuropa (bis 2026-10-26)
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
python -m locationer meine_sammlung.csv --limit 20 --mode debug

# Karte anschauen — sind die Treffer plausibel?
# Dann vollen Lauf starten mit caffeinate (verhindert Sleep bei Nachtläufen):
caffeinate -i python -m locationer meine_sammlung.csv
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
python -m locationer locationer/TestFile.csv

# Nachtlauf ohne Sleep-Unterbruch:
caffeinate -i python -m locationer meine_sammlung.csv
```

---

## 3. Hauptpipeline — Aufruf und Parameter

```
python -m locationer <input> [--mode human|debug] [--output PATH] [--limit N]
                     [--chunk-size N] [--geo-db PATH]
```

| Parameter | Default | Beschreibung |
|---|---|---|
| `input` | — | CSV oder XLSX Eingabedatei (Pflicht) |
| `--mode` | `human` | `human` = kompakte Tabellenausgabe, `debug` = vollständige Entscheidungspfade |
| `--output` / `-o` | `<input>_loc-<datum>.csv`* | Pfad der Ausgabedatei |
| `--limit N` | — | Nur die ersten N Zeilen verarbeiten |
| `--chunk-size N` | `20` | Verarbeitung in Blöcken à N Zeilen; setzt bei Unterbrechung automatisch fort |
| `--geo-db PATH` | aus `.env` | Pfad zur GeoNames-SQLite (überschreibt `GEO_DB_PATH`) |

\* Ausnahme `locationer/TestFile.csv`: fester Name `locationer/TestFile_geo.csv`,
kein Datum -- die Rauchprobe baut sich über viele kleine `--limit`-Läufe
(Chunk-Resume) zu einer vollen Messung auf, siehe "Falle Testdatei" unten.

### Beispiele

```bash
# Testlauf, erste 10 Zeilen
venv/bin/python -m locationer locationer/TestFile.csv --limit 10

# Voller Lauf mit Chunk-Resuming (sicher bei grossen Dateien)
caffeinate -i venv/bin/python -m locationer ../ZIN_complete_allFields.csv --output ../zin_geo.csv

# Detaillierte Entscheidungsausgabe für Debugging eines einzelnen Records
venv/bin/python -m locationer locationer/TestFile.csv --mode debug --limit 5

# Andere GEO DB (z.B. Europa-only)
venv/bin/python -m locationer meinedaten.csv --geo-db /Volumes/LCMT_JBTM/LocationerGeo/locationer_geo_EUROPA.sqlite
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
       Ambiguous, Bildtyp, Widerspruch, Ext-Calls[, Deviation_km], Map
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
> `locationer/map.py` legt diese Namen beim Einlesen als Alias auf die Vorlagenspalten,
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

**Modell: Haiku 5.5, Denkaufwand `low`** (Phase 1a und 1b, seit 2026-10-24). Gemessen gegen Haiku 4.5 auf zwei Zufallsstichproben à 500 Bilder aus DISCO, mit TestFile und ZIN als Kontrolle: gleich viele Koordinaten, bei Abweichungen öfter richtig, rund ein Viertel der Kosten (DISCO 705224 Bilder: ~140 statt ~560 USD). Haiku 5.5 denkt vor der Antwort; gelesen wird darum der Textblock, nicht `content[0]`.

Claude Haiku extrahiert aus Titel und Beschreibung:

| Feld | Beschreibung |
|---|---|
| `title` | Bereinigter Titel (max. 60 Zeichen, Originalsprache) |
| `image_type` | `portrait`, `group`, `event`, `view`, `landscape` oder `other` → Output-Spalte `Bildtyp` |
| `country` | Heutiger englischer Landesname des Aufnahmeorts. Historische Namen werden übersetzt (`Persia` → `Iran`); bei historischen Gebieten mit anderen Grenzen gilt das heutige Land des genannten Orts |
| `region` | Kanton, Bundesland, Provinz — aus der Eingabe, sonst aus geographischen Hinweisen weltweit inferiert (z.B. `"Berninagruppe"` → `"Graubünden"`) |
| `city` | Stadt mit heutigem Standardnamen inkl. Disambiguierungssuffix. Aus dem Text (`city_source="text"`), sonst aus Wissen, **nur** wenn das genannte Objekt in genau einer Stadt steht (`"knowledge"`). Nie aus Person, Fotograf oder Sammlungsthema |
| `location` | Eigenname des Objekts in Originalsprache (z.B. `"Schloss Vaduz"`), auch Täler, Seen, Berge, Pässe — leer bei Beschreibungen wie „une porte de la ville" |
| `location_type` | Generischer Gebäudetyp im Originalwort wenn `location` leer (z.B. `"Bahnhof"`, `"Kirche"`) — kombiniert mit city zu Nominatim-Query |
| `geocoding_queries` | 1–3 fertige Nominatim-Suchstrings in Lokalsprache; bei `location_type` + Stadt der Typ mit korrekt geschriebener Stadt |
| `conflict` | Widerspruch zwischen Eingabe und Wissen (z.B. „Löwendenkmal steht in Luzern, Eingabe sagt Uri") → Output-Spalte `Widerspruch` |

**Rangordnung:** Eingabe (Spalten, Text) > Ableitung aus Hinweisen > Wissen. Wissen füllt nur Lücken und korrigiert nie; ein Widerspruch wird vermerkt, nicht aufgelöst. **Koordinaten kommen nie von der KI**, immer aus den Datenbanken des Geostacks. Eine Stadt aus Wissen prüft der Geostack zusätzlich gegen GeoNames: liegt sie nicht im Anker-Land und in der Anker-Region, wird sie gestrichen und als Widerspruch vermerkt.

**Batch-Zuordnung:** Antwortet Haiku mit falscher Anzahl Einträgen, wird der Batch halbiert und neu gefragt (vorher rutschten die Einträge nach einer Lücke um eine Zeile).

**Doppelte Zeilen** innerhalb eines Laufs werden nur einmal gefragt. Der Phase-1b-Cache ignoriert Links (Bild-URLs), der Geo-Cache berücksichtigt die Region.

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

Umgesetzt in `locationer/objekttyp.py`. **Kein neues Feld auf `GeoResult`, keine
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
python -m locationer.import_country CH --output /path/to/ch_country.sqlite

# Mehrere Länder auf einmal
python -m locationer.import_country CH DE FR IT AT NO --output-dir /path/to/dir/

# Statistik anzeigen
python -m locationer.import_country CH --check

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
| `nachfass_cache` | Schritt 2: Triage und Opus-Identifikation je Eingabe | `cache/locationer.sqlite` |
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
| `nachfass_cache` | Wenn TRIAGE_PROMPT oder IDENT_PROMPT (`nachfassen.py`) geändert wurde — Spalte `ident` bzw. `triage` auf NULL setzen |

### Auto-Resume

`--chunk-size 20` ist der Default — d.h. jeder Lauf schreibt alle 20 Zeilen auf Disk und kann nach einem Crash oder Unterbruch nahtlos fortgesetzt werden. Einfach **denselben Befehl nochmals ausführen** — die Pipeline erkennt die bestehende Output-Datei und macht ab der nächsten unverarbeiteten Zeile weiter.

```bash
# Erster Lauf (oder Resume nach Unterbruch — gleicher Befehl):
python -m locationer locationer/ZIN_complete.csv
```

**Fortgesetzt wird nur, was nachweislich zu dieser Eingabe gehört**
(`_fortsetzen_ab`, seit 2026-10-25). Die erste und die letzte geschriebene
Zeile müssen in jeder durchgereichten Eingabespalte mit der Eingabe
übereinstimmen, und die Ausgabe darf nicht mehr Zeilen haben als die Eingabe.
Sonst bricht der Lauf mit einer klaren Meldung ab, statt anzuhängen oder zu
überschreiben. Anlass: Ein Lauf hing still an eine fremde Teil-Ausgabe an
(„Resuming: 259 Zeilen"), und die Datei war gemischt.

---

## 8. Overrides — manuelle Korrekturen

Overrides haben höchste Priorität (Step 0, vor allen Datenbanken). Zwei Typen:

### Geo-Overrides (Phase 2 — Koordinaten)

Fixer Koordinatenpunkt für ein Pattern `location|city|country`. `*` = Wildcard.

```bash
# Hinzufügen
python -m locationer.overrides add "Cresta||Switzerland" 46.453581 9.542744 "Avers GR" "Bemerkung"
python -m locationer.overrides add "*|Avers|Switzerland" 46.453581 9.542744 "Avers GR"
python -m locationer.overrides add "Cresta|*|Switzerland" 46.453581 9.542744 "Avers GR" --score 5

# Alle anzeigen
python -m locationer.overrides list

# Entfernen
python -m locationer.overrides remove "Cresta||Switzerland"
```

Patternformat:
- `"Schloss Vaduz||Liechtenstein"` — exakt: location=Schloss Vaduz, kein city, country=Liechtenstein
- `"*|Avers|Switzerland"` — jeder Record mit city=Avers in Switzerland
- `"Cresta|*|Switzerland"` — jeder Record mit location=Cresta in Switzerland, egal welche city

### Norm-Overrides (Phase 1 — Normalisierungskorrektur)

Korrigiert city/country wenn Haiku systematisch falsch liegt. Substring-Match auf dem rohen Eingabetext.

```bash
# Hinzufügen
python -m locationer.overrides norm-add "Avers" --city "Avers" --country "Switzerland"
python -m locationer.overrides norm-add "Calancatal" --country "Switzerland"

# Alle anzeigen
python -m locationer.overrides norm-list

# Entfernen
python -m locationer.overrides norm-remove "Avers"
```

Norm-Overrides greifen nur wenn das entsprechende Feld im Haiku-Output leer ist (sie ergänzen, überschreiben nicht).

### Override-Liste und Validierung

Overrides werden ohne automatische Validierung zurückgegeben (kein Country-Check, kein Proximity-Check). Das ist bewusst — sie existieren genau für Fälle die die automatischen Checks brechen (z.B. Grenzgipfel, historische Namen). Die QA-Karte ist die Kontrollinstanz: ein falsch gesetzter Override fällt durch falsche Koordinaten oder Δ-Abweichung sofort auf.

---

## 9. TGN-Datenbank — Import

Einmalig ausführen. Liest direkt aus dem lokalen ZIP, kein vollständiges Entpacken nötig (~10–15 Minuten).

```bash
# Empfohlen: aus lokalem ZIP
python -m locationer.import_tgn --from-zip cache/tgn_xml_0126.zip --output cache/tgn.sqlite

# Alternativ: aus entpacktem Verzeichnis
python -m locationer.import_tgn --from-dir cache/tgn --output cache/tgn.sqlite

# Direkt von Getty herunterladen (~600 MB)
python -m locationer.import_tgn --download --output cache/tgn.sqlite

# Statistik anzeigen
python -m locationer.import_tgn --check --output cache/tgn.sqlite

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
NACHFASSEN=0                          # Optional: Schritt 2 (Opus) abschalten
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

Auf der Webseite heisst er seit v6.2.2 **Suchraum (Bbox)**; der Knopf „Auf Karte“
zeigt ihn als Rechteck, zwei Klicks setzen einen neuen, „-“ = ohne. Er ist ein
Zaun, kein Schwerpunkt: Treffer knapp ausserhalb werden genauso verworfen wie
weit entfernte.

Der wichtigste sammlungsspezifische Parameter. Er erfüllt **zwei unabhängige Rollen**, beide greifen nur wenn `country=""` (Haiku hat kein Land erkannt):

**Rolle 1 — Harter Filter:** Jeder Geocoding-Treffer (GeoNames, TGN, Nominatim) wird am Ende gegen die Bbox geprüft. Liegt er ausserhalb → verworfen. Verhindert z.B. dass "Badus" in den Pyrenäen landet wenn kein Land bekannt ist.

**Rolle 2 — Geografischer Prior:** Das Bbox-Zentrum wird als Proximity-Hint an GeoNames übergeben — nur wenn der Ort kein Siedlungsname ist und als Berg, See, Pass usw. gesucht wird. Bei global mehrdeutigen Namen (z.B. "Matterhorn" existiert in CH und in NZ) wird der Eintrag bevorzugt der dem Bbox-Zentrum am nächsten liegt — ohne dass ein Override nötig ist.

Wenn `country` bekannt ist, greifen die länderspezifischen Bounding Boxes — `COLLECTION_BBOX` ist dann vollständig inaktiv. Bilder aus Ägypten, Südafrika oder Kanada werden korrekt geocodiert solange Haiku das Land erkennt.

| Sammlung | Empfohlener Wert | Abdeckung |
|---|---|---|
| Vorgabe Server (seit 2026-10-26), ZIN | `25,72,-15,45` | Europa mit ganz Italien, Mittelmeer, Nordafrika, Nahost |
| früher ZIN | `43.0,62.0,-5.0,20.0` | Mitteleuropa; Süditalien (unter 43° N) lag ausserhalb |
| Nordafrika | `18.0,38.0,-5.0,40.0` | Maghreb + Ägypten |
| Global | *(leer lassen)* | kein Filter, nur Country-Bbox greift |

---

## 11. Testmetrik

Wenn `input` identisch mit `locationer/TestFile.csv` ist, berechnet die Pipeline automatisch eine Qualitätsmetrik und schreibt sie in `TESTLOG.md`.

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
python -m locationer.map locationer/ZIN_complete_geo.csv
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
`explicit_list/`, `.env`, `locationer/TestFile.csv` und `TESTLOG.md` am
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
│   └── TestFile_geo.csv     Letzter Testlauf-Output (fester Name, siehe "Falle Testdatei")
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

venv/bin/python -m locationer locationer/TestFile.csv --limit 10          # Rauchprobe
caffeinate -i venv/bin/python -m locationer ../ZIN_complete_allFields.csv \
    --output ../zin_2026-09-25.csv                        # voller Lauf
```

`venv/` wurde neu erzeugt statt verschoben: in `bin/activate` und den
pip-Startzeilen stehen absolute Pfade, ein verschobenes venv ist halb kaputt.

Die `.gitignore` in der Wurzel musste mit: die alten Muster `**/out/*.csv` und
`**/locationer/ZIN_..._geo*` zeigten nach dem Umzug ins Leere, und `zin_2026-09-06.csv`
stand damit ungeschuetzt da. Jetzt an der Wurzel verankert (`/zin_*.csv`).

---

## 2026-10-16/17 — Rückfluss aus Pictomap, harte Region, Override-Regel

### Rückfluss (Stufe 0.5)

`locationer/pictomap_ref.py`.  Bilder, die in Pictomap per Timeslide oder von Hand
**präzise** verortet wurden, geben ihren Standort an neue Bilder mit demselben
Motiv (`Objekt | Stadt | Land`) weiter.  Stufe 0.5: nach den Overrides, vor dem
Cache.  Nie `Exakte Koordinaten`; `Gitter` = Streuung der Referenzpunkte,
mindestens 100 m; Streuung > 1 km → verworfen.  Ortsansichten (Objekt = Stadt)
und Bilder ohne Objekt nehmen nicht teil.

```bash
venv/bin/python -m locationer.pictomap_ref sync     # vor einem Lauf; nur lesend
venv/bin/python -m locationer.pictomap_ref stats
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

### Nachtrag 2026-10-17: Suche ohne Stadt, genauere Landesprüfung

**Ohne Stadt nicht aufgeben** (Dec: „Schloss Chillon gehört zu den
bekanntesten Gebäuden Europas").  Fehlt die Stadt, suchen Wikidata und
Nominatim im Kanton (Kantonsradius), fehlt auch der, im Land.  Bei einem
Treffer ergänzt `__main__` die Gemeinde (`geo_db.naechster_ort`: nächster Ort →
dessen ADM3; in FR/DE ist ADM3 nicht die Gemeinde, dort der Ortsname).
Alte „nicht gefunden"-Cache-Einträge gelten für Bilder mit Objekt nicht mehr.

**Land/Region für Wikidata, Nominatim, TGN über die Nachbarorte**
(`geo_db.lage`): die 5 nächsten Orte plus alle bis zur doppelten Entfernung
des nächsten.  Mindestens einer muss im Land liegen; für die Region einer im
Kanton oder der Punkt im Kantonskreis.  Rechteck und Kreis allein liessen den
Ortler (IT) als Bündner Gipfel durch.  Grenzlagen (Genfersee, Basodino,
Stilfserjoch) gehen durch.

Gemessen: Testdatei 27/9/3/4/2 (bester Juni-Lauf 25/11/3/4/2).  ZIN-Stichprobe
1419 Zeilen (alle Grenzorte + 500 zufällig) gegen September: 1306 gleich, 53
> 1 km verschoben — alles Korrekturen (Münster-Fälle, Lenzburg 131 km via
„Lenz", Lunapark via Rückfluss) —, 2 verloren, beide zu Recht (San Salvatore
lag bei Varese; „Genève" mit Beschreibung „Frankreich").

**Falle Testdatei:** liegt `locationer/TestFile_geo.csv` schon vollständig vor, setzt
der Lauf fort, rechnet nichts und die Metrik misst das alte Ergebnis. Vor
einem Messlauf die Ausgabe löschen. Dieser Name bleibt bewusst fest (ohne
Datum, seit 2026-10-21 Ausnahme von der sonst datumsabhängigen Ausgabe-CSV,
siehe oben) — genau das Aufbauen über viele kleine `--limit`-Läufe ist der
Zweck der Rauchprobe.

### Nachtrag 2026-10-17 abends: ZIN-Lauf, Haiku-Frist, bekannte Lücke

**Haiku-Frist.**  `timeout=60` am Client misst nur die Pause zwischen zwei
Datenpaketen — ein Aufruf hing trotzdem 30 Minuten.  Jeder Aufruf läuft jetzt
in einem Daemon-Thread mit harter Frist (`_FRIST_S` = 120 s, 3 Versuche,
danach Abbruch; Lauf lässt sich fortsetzen).  `input_normalizer._create`.

**ZIN-Lauf gegen September** (`zin_2026-10-17.csv` vs. `zin_2026-09-06-BE Kopie.csv`):
20562 → 20600 Koordinaten, 19806 unverändert, 225 über 10 km verschoben —
fast alles Korrekturen (Münster/Müstair 148, Isola Bella, Lenzburg, Pilatus,
Rigi).  61 verloren, überwiegend zu Recht (vorher in Apulien, Bregenz, falsche
Eingaberegion); echte Verluste ≈ 15 (Mont-Blanc-Grenzpunkte mit Region Aosta,
Lac Léman mit Region Genf).

**Bekannte Lücke, bewusst offen gelassen.**  `_in_region` lässt einen Punkt
gelten, der im Kreis um den Regionsmittelpunkt liegt (Piemont 197 km, Wallis
≈ 90 km).  So kamen Grimselstrasse → Bern, Monte Calvario → Brescia,
Oria → Biella durch — rund 20 Zeilen.  Ersatz „nächster Ort der Region
≤ 15 km" gemessen (fängt sie, kostet Mönchsjoch und Bietschhorn), aber bei
dieser Grössenordnung nicht eingebaut.

---

### Nachtrag 2026-10-21: Paket umbenannt, Ausgabe-CSV mit Datum im Namen

**`v4/` → `locationer/`** (Commit 68e7e35). Die v1–v4-Aufteilung stammte aus
den ersten Anläufen und war nicht mehr nötig — es gibt nur noch die aktuelle
Version. Alle Aufrufe `python -m v4...` → `python -m locationer...`,
`CLAUDE_V4.md` → `CLAUDE.md`. Mit echtem Lauf verifiziert.

**Ausgabe-CSV trägt jetzt das Datum im Namen:** `<input>_loc-<datum>.csv`
statt `<input>_geo.csv` (Dec: "inputname_loc-datum", 2026-10-23; vorher kurz `_loc_`). Grund: nachvollziehbar,
mit welchem Lauf welche Datei entstand, wenn mehrfach gegen denselben Input
gerechnet wird — analog zur selben Umstellung bei Framer, 2026-10-21.

**Ausnahme `locationer/TestFile.csv`, gefunden beim Testen:** ein
datumsabhängiger Name hätte die Rauchprobe kaputtgemacht. Der feste alte
Name (`TestFile_geo.csv`) hatte sich über viele `--limit 10`-Läufe per
Chunk-Resume zu einer vollen 45-Zeilen-Messung aufgebaut (27/9/3/4/2 Treffer);
mit täglich wechselndem Namen hätte jeder Tag bei 0 angefangen und eine
`--limit 10`-Probe nur noch 10 von 45 Zeilen gemessen (gemessen: 5/1/0/2/37 —
deutlich schlechter aussehend, aber nur weniger Daten, kein echter
Qualitätsverlust). Deshalb: `locationer/TestFile.csv` behält weiterhin
`locationer/TestFile_geo.csv`, alle anderen Inputs bekommen den neuen,
datierten Namen. Siehe Abschnitt 11 (Testmetrik) für die "Falle Testdatei".

**Spaltenname `titre` wird als Titel erkannt (2026-10-23).** Anlass: DISCO-Export
(`DISCO_output_section.csv`, 2589 Bilder, französische Spaltennamen). Ohne den
Alias fehlte der Titel ganz, und gerade dort steht bei DISCO der Ort.

---

### Nachtrag 2026-10-24: Haiku 5.5, Bugfixes, Geostack-Sicherungen

Gemessen auf zwei Zufallsstichproben à 500 aus DISCO (705224 Bilder; der
Ausschnitt `DISCO_output_section.csv` war mit 74 % Porträts nicht
repräsentativ), zwei ZIN-Teilmengen à 500 gegen `zin_2026-10-17.csv` und
dem TestFile. Ergebnis gegenüber dem bisherigen Stand: Abdeckung gleich oder
besser (DISCO Nicht-Porträts 303 statt 298 von 376; ZIN 938 statt 939 von
1000), weniger Treffer über 10 km (ZIN 114 statt 121), Haiku-Kosten rund
ein Viertel. Einzelheiten zu Phase 1a/1b siehe Abschnitt Phase 1a.

**Bugfixes**
- *Verrutschte Zeilen:* liess Haiku einen Eintrag aus, rutschten alle
  folgenden des Batches um eine Zeile (Como bekam die Koordinate von
  Honolulu). Jetzt: falsche Anzahl → Batch halbieren und neu fragen.
- *Unbekanntes Land:* ein genanntes, aber nicht auflösbares Land hob die
  Landesgrenze auf („Palestine, Samarie" → Bosnien). Jetzt: keine Koordinate.
  Dazu Aliase für Landesteile, eindeutige historische Namen und englische
  Schreibweisen, die GeoNames anders führt; „Palestine" = heutiger Staat (PS).
- *Externe Stufe ohne Regionsprüfung:* ein Wikidata-/Nominatim-Treffer
  ausserhalb von Land/Region wurde erst in der Schlussprüfung verworfen — ohne
  Rückfall auf die Stadt („Palais d'Hérode, Jerusalem" → Herodium → nichts).
  Jetzt prüft die Stufe selbst, die Stadt fängt auf.
- *Caches:* Phase-1b-Schlüssel ohne Links (vorher war jede Zeile einmalig),
  Geo-Schlüssel mit Region, doppelte Zeilen eines Laufs nur einmal gefragt.

**Geostack-Sicherungen (alle in `geocode()`)**
- *Stadt aus Wissen* (`city_source="knowledge"`) muss in GeoNames im
  Anker-Land und der Anker-Region liegen, sonst gestrichen und als
  Widerspruch vermerkt.
- *Land als Stadt* („Bodensee, Dampfer" → city="Schweiz") zählt nur, wenn es
  im Land einen Ort dieses Namens mit ≥ 1000 Einwohnern gibt (Monaco,
  Luxembourg, Singapore bleiben).
- *Naturobjekt beim Ort:* Haiku 5.5 nimmt bei „Ort, Landschaft" öfter einen
  Berg aus der Beschreibung („Zermatt … Blick auf das Matterhorn", 8.6 km).
  Steht der Ort in der Eingabe und ist das Objekt ein Naturobjekt, gilt es nur
  im Ortsradius — sonst der Ortsmittelpunkt wie früher. Der Originaltitel
  *schützt* nur: steht das Objekt dort („Saas Fee, Feegletscher"), bleibt es;
  ohne Titelspalte greift die Sicherung nicht (was ein Titel ist, ist je
  Sammlung verschieden — Dec, 2026-10-24). Eine Prompt-Regel dafür wurde
  versucht und verworfen: Haiku befolgte sie nur teilweise.

**Bewusst nicht gemacht:** weitere Prompt-Regeln (Täler als Objekt,
Kamerastandort). Beide brachten neue Nebenwirkungen; Grundsatz Dec: nicht zu
weit vom bewährten Verhalten weg. Bekannt offen: „Val Medel" im TestFile
(Haiku 5.5 lässt das Tal als Objekt weg), einzelne Berg-Fälle über TGN.

**Nächster Schritt (nicht eingebaut):** Triage in Phase 1a
(`locatable` = specific/area/none, im Test 17 von 19 Fällen wie meine
Einschätzung) und ein Schritt 2 mit einem stärkeren Modell nur für die
bestimmbaren Fälle ohne Koordinate. Koordinaten auch dort nie vom Modell.

### Nachtrag 2026-10-25: Schritt 2 (Nachfassen), Zuordnung abgesichert

**Schritt 2 — `locationer/nachfassen.py`.** Für Nicht-Porträts, die nach dem
Geostack ohne Koordinate sind. Die Triage (Haiku 5.5) fragt, ob der Text einen
bestimmten Ort nennt (`specific` | `area` | `none`). Nur `specific` geht an
Opus 5.5, das Name, Wikidata-ID, Stadt, Region, Land, Form
(`point`/`area`/`line`) und Sicherheit nennt, **nie Koordinaten**. Die
Koordinate kommt aus Wikidata P625 der ID (gegen Land/Region und Abstand zur
Stadt im Land des Punkts geprüft) oder aus dem Geostack mit Opus' Namen.
- Eine verworfene ID wird nicht durch eine schwächere Namenssuche ersetzt
  („UK, Gibraltar" → sonst das Dorf Gibraltar in England).
- Ein Rückfall auf den Ortsmittelpunkt bleibt als Rückfall markiert (Score 3,
  `Gitter` nach Ortsgrösse).
- Linien (Bahnlinie, Strasse, Grenze) bekommen keine Koordinate („Ligne
  Pékin-Hankou" landete sonst bei Tangshan).
- `_UEBERSEE` in `geostack.py`: Überseegebiete zählen zum Mutterland
  (GI→GB, HK→CN, PR→US, GL→DK …).
- Ausgabespalte `Nachgefasst`: „Opus: Name (Sicherheit) → woher".
- Cache `nachfass_cache`; abschaltbar mit `NACHFASSEN=0`.

Gemessen (je 500 Zeilen, Nicht-Porträts mit Koordinate von 376):
DISCO 2026 299 → 312–314, DISCO 42 (unabhängig) 297 → 306–309; ZIN ohne
Rückschritt (ZIN 1: 476 → 477, über 10 km 58 → 57; ZIN 2: 463 → 473, 63 → 60);
TestFile findet jetzt Val Medel. Opus antwortet nicht ganz gleich von Lauf zu
Lauf (Spannweite oben). Kosten Schritt 2: 0.13–0.25 USD pro 500 Zeilen.

**Zuordnung Antwort ↔ Zeile.** Haiku 5.5 hat in einem Batch zwei Antworten
vertauscht (Anzahl stimmte, die Zeilenzahl-Prüfung sah nichts). Jetzt in allen
vier Modellaufrufen (1a, 1b, Triage, Opus): jede Antwort trägt die Nummer `n`
und das Echo `ref` (erste fünf Wörter wörtlich); `_zuordnen` prüft beides
gegen den gesendeten Text. Passt eine nicht: Batch halbieren, bis zu einem
einzelnen Eintrag. Danach eine Schlusskontrolle im Lauf (Titel ohne gemeinsames
Wort oder Teilwort mit der Eingabe → Warnung mit Zeilennummern; Übersetzungen
lösen sie als Fehlalarm aus). In fünf Testdateien (2045 Zeilen): keine
Vertauschung.

**Fortsetzen abgesichert** — siehe Abschnitt Auto-Resume.

**Akzent-Lücke geschlossen (2026-10-25):** Alternativnamen mit Akzent wurden in
GeoNames nie gefunden — `alt_name_norm` behält die Akzente („castellón de la
plana"), die Suche fragte nur ASCII-Formen. Rund 530000 von 3 Mio.
Alternativnamen waren so unerreichbar (Bâle, Gênes, Séville, Pékin, Castellón).
`_ascii_norm_variants` fragt jetzt zusätzlich die Form mit Akzent und die Form mit
Apostroph als Leerzeichen. Kontrolle ohne Rückschritt (DISCO 313/306, ZIN 478/473).

### Nachtrag 2026-10-25 (v5.0.0): Server-Betrieb, Parallelität, Kostengrenze

**Aufteilung des Codes.** `pipeline.py` enthält die Verarbeitung eines Pakets
(vorher `__main__.py`) und `Umgebung` — alle Datenbanken und Dienste für genau
einen Thread. `auftrag.py` rechnet einen Auftrag, `__main__.py` ist nur noch das
Terminal, `worker.py` und `web.py` sind der Server-Betrieb, `register.py` die
Auftragsliste, `kasse.py` die Kostenkasse.

**Auftrag (auftrag.py).** Eigenes Verzeichnis, Kopie der Eingabe mit
Fingerabdruck (SHA-256), Teildatei je Paket, atomar geschrieben (erst
vollständig, dann umbenannt). Fortsetzen prüft Fingerabdruck, Spalten und jede
Teildatei Zeile für Zeile gegen die Eingabe; jede Abweichung bricht ab.
Gerechnet werden genau die fehlenden Pakete. `ausgabe.csv` entsteht am Schluss,
Paket für Paket gestreamt, mit Zählprobe. Getestet: harter Abbruch (kill -9)
mitten im Lauf → keine halbe Datei, Fortsetzen rechnet nur den Rest; geänderte
Zelle in einer Teildatei → Abbruch mit Stelle; andere Eingabe auf bestehendem
Auftrag → Abbruch; vorhandene Ausgabe ohne Auftrag → wird nicht überschrieben.

**Parallelität.** Mehrere Pakete gleichzeitig, jedes mit eigener `Umgebung`
(eigene SQLite-Verbindungen; Cache im WAL-Modus mit Wartezeit). Gemeinsam sind
nur die Kasse und der Takt von Nominatim und Wikidata (prozessweit 1 Anfrage/s).
Kontrolle: ZIN 1 mit 6 parallel = seriell, alle 500 Zeilen in allen Spalten gleich.

**Kostengrenze (kasse.py).** Gebucht wird die `usage` jeder Antwort, auch einer,
die nach Ablauf der Frist noch eintrifft. Unbekanntes Modell = Opus-Preis.
Angehalten wird vor dem Start (Schätzung), ab 1000 Zeilen (Hochrechnung) und
sobald die Kosten die Grenze erreichen; dazu eine Tagesgrenze über alle
Aufträge (auf der Seite einstellbar). Der Stand wird höchstens alle 5 s
gesichert, auch mitten im Paket. Überschreitung höchstens um die gerade
laufenden Pakete (6 × 20 Zeilen, Rappenbeträge).

**Server (tools.cronica.ch/locationer).** Zwei Dienste: `locationer-web`
(gunicorn, Port 5006) und `locationer-worker`. Ein Neustart des Arbeiters setzt
den laufenden Auftrag fort; vorübergehende Störungen (Netz, Überlastung) werden
nach 5 Minuten bis zu 6-mal neu versucht. Dateien in `server/`.
Zustände eines Auftrags: prüfen → bereit → wartet → läuft → fertig, dazu
angehalten / abgebrochen / fehler. Angehaltene und fehlerhafte Aufträge stehen
rot oben auf der Seite, der Tab-Titel zeigt „⏸".

**API** (Schlüssel im Kopf `Authorization: Bearer …`):

| Aufruf | Wozu |
|---|---|
| `POST api/auftraege` | Datei (`datei`) oder JSON `{"zeilen": […]}`; Pflicht `grenze_chf`; optional `name`, `bbox`, `starten=1` |
| `GET api/auftraege`, `GET api/auftraege/{id}` | Liste, Zustand, Fortschritt, Kosten, Hochrechnung, Protokoll |
| `POST api/auftraege/{id}/starten` · `/weiter` · `/anhalten` · `/abbrechen` | Steuerung; `weiter` nur mit Grenze über den bisherigen Kosten |
| `GET api/auftraege/{id}/ergebnis` | CSV als `<Datei>_loc-<Abschlussdatum>.csv` (ohne Datei: Auftragsname); `?format=json`; `?teilweise=1` für die fertigen Pakete (`<Datei>_teil_loc-<heute>.csv`) |
| `POST api/orte` | bis 50 Zeilen sofort, z. B. ein Bild aus Pictomap |
| `GET api/status` · `POST api/tagesgrenze` | heutige Kosten, Tagesgrenze, Version |

**Fehler im bestehenden Code, bei der Durchsicht gefunden und behoben:**
- Fehlte die Antwort des Modells für eine Zeile, wurde der leere Datensatz
  gecacht — die Zeile wäre nie mehr gefragt worden (Phase 1a, 1b und Schritt 2).
- Ein `null` des Modells wurde zum Text „None" — als Land hiess das „unbekanntes
  Land, keine Koordinate".
- Von mehreren möglichen Titelspalten galt die erste vorhandene, auch wenn sie
  leer war; jetzt die erste gefüllte.

---

### Nachtrag 2026-10-25 (v6.0.0): Gemeinde, Objektart, Kandidaten-Prüfung

Auslöser: ZIN 25425 „Winterthur, Ortsteilansicht" (Bahnhofplatz) lag mit
Score 5 am Zürcher Bahnhofplatz, dazu ein Google-Vergleich (100 Objekte ZIN
und DISCO) und ein zweiter auf 228 neuen DISCO-Objekten. Alle Änderungen sind
allgemeine Regeln; eine Regel, die bei 2524 Ortsnamen nur einen Fall (Au)
geändert hätte, wurde wieder entfernt (kein Overfitting).

**Gemeinde-Prüfung (geostack `_ausserhalb_gemeinde`).** Ein Gebäude muss in
der Gemeinde des Ortsankers liegen — gemessen an der Gemeinde eines der 5
nächsten GeoNames-Orte (mit Stadtteilen, PPLX). Die Gemeinde steht je Land auf
einer anderen Ebene (`geo_db.GEMEINDE_EBENE`: CH/LI/AT/IT/ES admin3,
FR/DE/BE admin4, NL/PT admin2); sonst gilt der einfache Stadtradius. Gelände
(Berg, See, Pass …) bleibt beim grossen Radius. Gilt für GeoNames, Länder-DB,
TGN, Wikidata und Nominatim.

**Objektart von Haiku (`location_kind`).** Phase 1a sagt, ob das Objekt
`building`, `street` oder `nature` ist — in jeder Sprache. Die Wortliste
(`_GEO_FEATURE_TOKENS`, de/fr/it/en) gilt nur noch für alte Cache-Einträge.
Kennt die Quelle die Art (GeoNames-Klasse T/H/V/U, TGN-Typ), gilt die Quelle.

**Phase 1a: effort medium, Fassung im Cache.** Auf 200 Zeilen je zweimal
gefragt sprang die Koordinate bei 7 statt 12 Zeilen (> 25 km: 2 statt 5),
+8 % Kosten. Jeder `norm_cache`-Eintrag trägt die Fassung
(`input_normalizer.NORM_FASSUNG`); passt sie nicht, wird neu gefragt. Bei jeder
Änderung an Anweisung, Modell oder Einstellung die Fassung erhöhen.
Haiku-Streuung: zweimal gefragt wechselt das Objekt in 15–18 % der Zeilen
(alte und neue Anweisung gleich), nicht vorhersagbar.

**Region.** `region_to_admin1` erkennt auch Départements/Provinzen (ADM2 →
admin1) und alle Schreibvarianten. Bisher fiel die Regionsgrenze bei
„Hautes-Pyrénées" stillschweigend weg (Route de Gavarnie → Dordogne, 310 km).
`_in_region` ohne grossen Kantonskreis: liegt der Punkt nicht bei Orten der
Region, muss ein Ort der Region höchstens 25 km entfernt sein (Seen,
Grenzberge); den Kreis gibt es nur noch, wo 20 km um den Punkt kein Ort liegt.

**Naturobjekt beim Ort — alle Quellen, Gewässer auch aus dem Titel.** Die
Regel „Naturobjekt nur aus der Beschreibung → Ortsmittelpunkt, wenn ausserhalb
des Ortsradius" gilt jetzt auch für Wikidata und Nominatim. Flüsse, Seen,
Kanäle (`_gewaesser`, laut GeoNames/TGN) fallen auch dann auf den Ort zurück,
wenn sie im Titel stehen („Besançon … le Doubs" lag 84 km flussabwärts).

**Schritt 1.5: Kandidaten-Prüfung (`kandidaten.py`).** Für Nicht-Porträts mit
genanntem Objekt ohne genauen Treffer: Wikidata-Volltextsuche (genau und
unscharf, nur mit Koordinate, im Umkreis), Filter Land/Region/Gemeinde, dann
prüft Haiku (effort medium) kritisch, welcher Kandidat gemeint ist. Nur
„high" und nur Punkte (keine Linien, keine Flächen); Koordinate aus Wikidata.
Treffer in der Nachbargemeinde (im Stadtradius) werden dem Modell mit Hinweis
vorgelegt statt verworfen („Bingen, Château de Rheinstein" → Trechtingshausen),
Gattungswörter lehnt es dort ab. Cache: `wikidata_kandidaten`,
`kandidat_cache`. Abschaltbar mit `KANDIDATEN=0`.

**Kosten (gemessen auf 300 neuen DISCO-Zeilen):** 0.00096 USD pro neue Zeile
(1a 0.00023, 1b 0.00012, Kandidaten < 0.00003, Schritt 2 ≈ 0.0006); Durchsatz
0.7 Zeilen/s mit 6 Paketen. DISCO (562515 neue Zeilen): ≈ 540 USD ≈ 490 CHF,
≈ 9–10 Tage. `USD_PRO_NEUE_ZEILE` auf 0.0011.

**Wikidata-Kennung** ohne persönliche Mailadresse
(`Locationer/6.0 (https://pictomap.ch; https://locomot.ch)`).

**Oberfläche:** Einzelabfrage (Titel + Beschreibung → dieselbe Pipeline wie
eine CSV-Zeile, mit Karte), Aufträge einklappbar, Dateiname und Zeitstempel
(erstellt, gestartet, beendet, Rechenzeit).

### Nachtrag 2026-10-26 (v6.1.0): Gattungswort nur, wenn im Ort eindeutig

Auslöser: „Katholische Kirche Almens" landete an der reformierten Kirche.
Haiku macht aus „Kath. Kirche" das Gattungswort „Kirche", der Code baut
„Kirche Almens", Nominatim nimmt die erste von zwei Kirchen, und der Cache gibt
sie jeder „Kirche Almens" weiter. In Städten mit Dutzenden Kirchen, Plätzen,
Häfen ist der erste Treffer Zufall — ein scheinbar genauer Punkt.

Zwei Versuche über die Haiku-Anweisung („unterscheidende Wörter behalten")
wurden verworfen: Haiku übernahm dann den Wortlaut („Le Château Blois" statt
„Château de Blois"), und bei DISCO gingen 15 genaue Treffer verloren.

**Regel (geostack `_gattung`, `_gattung_eindeutig`):** Hat das Objekt die
Form „<Wort> <Ort>" (so baut der Code ein Gattungswort), fragt Nominatim bis
10 Treffer ab (`nominatim.alle_bounded`, ersetzt die Einzelanfrage). Gibt es
in der Gemeinde mehrere gleicher Art (OSM-Klasse und -Typ, Haltestellen
ausgenommen), gilt die Ortsmitte. Ausnahme: einer ist weit bedeutender
(Nominatim-`importance` ≥ 0.3 und doppelt so hoch wie der nächste) — „Château
Chantilly" meint das Schloss. Ist er eindeutig, bleibt alles wie vorher. Die
Kandidaten-Prüfung rät bei solchen Zeilen nicht nach. Schalter: `GATTUNG=0`,
`BEDEUTEND=0`. Cache: `nominatim_alle`.

**Gemessen (gleiche Haiku-Antworten, mit und ohne Regel, 2300 Zeilen):** 230
Gattungswort-Objekte; 17 Zufallstreffer → Ortsmitte (Kirchen St. Moritz,
Wädenswil, Mainz, Jerusalem; Cathédrale Alger vorher 8.6 km daneben; Hafen
Bordeaux, Museum Neapel, Teatro Siracusa …), 4 genauer (Hafen Rorschach,
Bahnhof Messina, Château Vichy), ein Verlust (Grande Mosquée Srinagar → Ortsmitte).

### Nachtrag 2026-10-26: Suchraum-Vorgabe auf dem Server

`COLLECTION_BBOX=25,72,-15,45` (vorher 43,62,-5,20): Kanaren bis Nordkap, Atlantik
bis Kaspisches Meer. Wirkt nur auf neue Aufträge und Einzelabfragen bei Zeilen
ohne erkanntes Land; bestehende Aufträge behalten ihren gespeicherten Suchraum
(DISCO-Auftrag 4: ohne). Nur `.env`, kein neuer Code, darum keine neue Version.

### Nachtrag 2026-10-26 (v6.2.1): tools.cronica.ch/loc

Schlanke Seite nur mit der Einzelabfrage und grosser Karte, im selben Dienst
wie /locationer (dieselbe Pipeline, derselbe Cache, dieselbe Kasse — keine
Datenkopie). nginx leitet `/loc/` unverändert an Port 5006 weiter
(`server/nginx-locationer.conf`).

- Zwei Zugänge, eigenes Cookie `loc` (Pfad `/loc/`, 30 Tage):
  `LOC_PASSWORT` mit Knopf „Logik“ und Kostenanzeige, `LOC_GAST_PASSWORT`
  ohne beides. Das Cookie trägt einen Abdruck des Passworts: Passwort in der
  `.env` ändern meldet alle ab.
- Gäste zusammen höchstens `LOC_GAST_TAG_CHF` (Vorgabe 1) pro Tag, Tabelle
  `gast_ausgaben` im Register; die Tagesgrenze aller Aufträge gilt zusätzlich.
- Immer die Bbox aus `COLLECTION_BBOX`; eine Zeile pro Anfrage (`/loc/api/ort`).
- Logo (`/static/logo.png`, dieselbe Datei wie /layer) jetzt auch auf /locationer.

### Nachtrag 2026-10-26 (v6.2.0): Wikidata-Kennungen für Motiv und Ort

Statt einer eigenen Motiv-Datenbank speichert der Locationer die feste
Wikidata-Kennung (QID) — gleiche QID = gleiches Motiv bzw. gleicher Ort,
unabhängig von Sprache und Schreibweise. Zwei neue Ausgabespalten:

- `Wikidata`: QID des gefundenen Objekts (nur Score 4–5, nicht bei Ortsmitte),
  z.B. Martinskirche Chur = Q1421539.
- `Wikidata-Ort`: QID der endgültigen Stadt/Gemeinde (auch einer ergänzten),
  z.B. Chur = Q69007.

**Herkunft — immer aus der Quelle des Treffers, nie aus einer Namenssuche
danach:** Wikidata selbst (Suche, Kandidaten-Prüfung, Schritt 2); OSM über
Nominatim `extratags` (Tag `wikidata`, gleiche Anfrage); GeoNames über die
eigene `wkdt`-Angabe, sonst über Wikidata `haswbstatement:P1566=<Nummer>`;
TGN über `P1667`. Einträge ohne „ist ein" (P31) zählen nicht (leere
Bot-Einträge), bei mehreren gilt der mit den meisten Wikipedia-Artikeln.

**Ort:** Wikidata führt bei einer Stadt mal die GeoNames-Nummer des
Ortspunkts (Chur), mal die der Gemeinde (Siena, Paris, Wädenswil); am anderen
hängt oft nichts oder ein Bot-Eintrag. Darum werden beide gefragt
(`geo_db.gemeinde_id`, Ebene nach `GEMEINDE_EBENE`), der bekanntere gilt. Ein
Dorf bekommt so die QID seiner Gemeinde (Dangio → Blenio).

**Alte Cache-Einträge:** Genaue Treffer im `geo_cache` ohne QID werden neu
bestimmt (Quellen sind gecacht). Nominatim-Treffer von vor v6.2 holen die QID
mit einer zweiten Anfrage nach (`wikidata_nachholen`) — übernommen nur, wenn
derselbe Punkt (< 50 m) zurückkommt; die Koordinate bleibt. Ein alter
Wikidata-Treffer wird neu gefragt; scheitert das, gilt er weiter (QID
unbekannt, beim nächsten Mal erneut).

**Gemessen:** 2300 bekannte Zeilen mit denselben Haiku-Antworten: Koordinaten
unverändert bis auf 5 Rundungsverschiebungen < 100 m. 1000 neue Zeilen (je 500
ZIN/DISCO): QID beim Objekt 54 % (ZIN) bzw. 73 % (DISCO), beim Ort 84 % bzw.
97 %. Stichprobe 40 + 40: alle QIDs gehören zum gefundenen Treffer. Neue
Zeilen werden rund 20 % langsamer (Wikidata 1 Anfrage/s).

Im selben Test v5.0.1 gegen v6.2 auf den 1000 neuen Zeilen (alle Abweichungen
> 1 km einzeln beurteilt): 34 besser, 17 schlechter, davon 6 durch v6-Regeln —
Pozzuoli Anfiteatro und „Musée de Naples" (Gattungswort), Niagara und
Giessbach (Wasserfall heisst wie der Fluss → Fluss-Regel), Portaceli
(Gemeinde-Prüfung), Obersee Näfels (Naturobjekt 3.4 km > 3 km). Beobachten,
nicht einzeln korrigieren.

### Nachtrag 2026-10-26 (v6.3.0): Strassen in der Gemeinde, Ort als Gelände

ZIN-Lauf mit v6.2.5 (Auftrag 5, 21684 Zeilen, 8.03 CHF) gegen v4: Abdeckung
95.0 → 96.0 %, Score 4+5 9036 → 10469. Zwei Fehlermuster:

**Strassen ausserhalb der Gemeinde.** Von 169 Strassentreffern mit Score 4–5
im Cache lagen 8 nicht in der Gemeinde des Orts, 6 davon falsch (Rennweg und
Hirschengraben Zürich 20/29 km, Weinbergstrasse Kilchberg, Vittorio Emanuele
Genova, Calandaweg Chur, Sonnenbergstrasse Rüti). Seit v6.3.0 gilt die
Gemeinde-Prüfung auch für Strassen (`location_kind = street`). Frei bleiben sie,
wenn der Ort kein Dorf ist (Pass, Berg: kein `city_row`) oder die Quelle den
Treffer als Strecke führt (GeoNames-Klasse R, TGN `tunnel`, `road`, `route`,
`railroad` — Simplontunnel bei Brig). Preis: eine Strasse über die
Gemeindegrenze fällt aufs Ortszentrum (Nidelbadstrasse Rüschlikon).

**Kein Land, der Ort ist Gelände.** „Bodensee" ohne Land landete im Dorf
Bodensee (Niedersachsen), „Massif du Mont-Blanc" in Saint-Just-Malmont. Ist
das Land leer und stuft Haiku das Objekt als Natur ein, sucht
`geo_db.bedeutendes_gelaende` zuerst ein Gelände (GeoNames T/H) dieses Namens.
Bedeutung = Zahl der Namen in anderen Sprachen (sprachunabhängig): gemeint ist
der Kandidat mit mindestens 10 und doppelt so vielen wie der nächste, weltweit
verglichen, innerhalb des Suchraums. Bodensee 76 gegen 0, Mont Blanc 122 gegen
3, Genfersee 47 gegen 18. Schwelle vor dem Test festgelegt. Die
Naturobjekt-Regel lässt einen solchen Ort stehen. Gleichnamige Dörfer ohne
Land (Schwendi, Schöneck) bleiben ungelöst — ohne Anker keine saubere Regel.

Test (geo_cache leer, Normalisierung vom Server): 528 Zeilen (Stichprobe 400
+ betroffene) → 12 geändert; gezielt 426 Zeilen (334 Strassenzeilen, 92 ohne
Land) → 16 geändert, alle besser oder gleichwertig (Lago Maggiore: Seemitte
statt Nordende). Keine Verschlechterung.

## Versionen

Regel: MAJOR = Ergebnisse ändern sich grundlegend (Modell, Pipeline-Schritt),
MINOR = neue Funktion oder Korrektur mit Einfluss auf Ergebnisse, PATCH = Korrektur
ohne Einfluss auf Ergebnisse. Jede Version steht in `locationer/__init__.py`
(`VERSION`, `VERSION_DATUM`), auf der Webseite, in `api/status` und in jedem
Auftrag (`auftrag.json`: `version`, `versionen`). Jedes Deployment bekommt eine
neue Nummer und den Git-Tag `vX.Y.Z`.

| Version | Datum | Was |
|---|---|---|
| 6.3.0 | 2026-10-26 | Strassen müssen in der Gemeinde des Orts liegen (ausser Pass/Berg als Ort, Tunnel/Strecken); ohne Land wird ein bekanntes Gelände (Bodensee, Mont Blanc) zum Ortsanker statt eines gleichnamigen Dorfs |
| 6.2.6 | 2026-10-26 | GUI: Spalte „Fertig ca.“ neben der Hochrechnung; Zeiten in Ortszeit des Betrachters (Register und Protokoll sind Server-UTC), Rechenzeit stimmt; Protokoll bleibt unten |
| 6.2.5 | 2026-10-26 | GUI: getippte Grenze springt nicht mehr zurück; Meldung nach dem Hochladen zeigt das Prüfergebnis; „Zeilen“ mit neuen/verschiedenen, „Schätzung vorher“ nur der Betrag; Einzelabfrage aufklappbar |
| 6.2.4 | 2026-10-26 | Feld „Name“ heisst „Auftragsname“; Download nach dem Dateinamen: `<Datei>_loc-<Datum>.csv` |
| 6.2.3 | 2026-10-26 | Download heisst wie bei der Kommandozeile `<Name>_loc-<Datum>.csv` (Datum des Abschlusses; Teilergebnis `<Name>_teil_loc-<heute>.csv`) |
| 6.2.2 | 2026-10-26 | „Schwerpunkt (Bbox)“ heisst jetzt „Suchraum (Bbox)“ (er ist ein Zaun für Zeilen ohne Land, kein Schwerpunkt), mit ⓘ-Erklärung; Knopf „Auf Karte“ zeigt den Suchraum als Rechteck, zwei Klicks setzen einen neuen |
| 6.2.1 | 2026-10-26 | tools.cronica.ch/loc: nur Einzelabfrage mit grosser Karte, Zugang mit Logik und Gastzugang mit Tageskontingent; Logo auf beiden Seiten |
| 6.2.0 | 2026-10-26 | Neue Spalten `Wikidata` (Objekt) und `Wikidata-Ort` (Stadt/Gemeinde): feste QID aus der Quelle des Treffers (Wikidata, OSM, GeoNames, TGN); Koordinaten unverändert |
| 6.1.0 | 2026-10-26 | Gattungswort-Objekte („Kirche Almens“) nur mit eindeutigem Treffer im Ort, sonst Ortsmitte; Ausnahme bei weit bedeutenderem Objekt |
| 6.0.2 | 2026-10-25 | Einzelabfrage: Knopf „Logik“ zeigt das Debug-Protokoll (Haiku-Felder, jede Geostack-Station, Wikidata-Kandidaten mit Urteil, Triage/Opus); `api/orte` mit `"logik": true` |
| 6.0.1 | 2026-10-25 | Einzelabfrage: Karte erscheint (Kreis erst nach dem Ausschnitt), ⓘ-Erklärungen zu Gitter, Score und Bildtyp |
| 6.0.0 | 2026-10-25 | Gemeinde-Prüfung (10 Länder); Objektart von Haiku statt Wortliste; Phase 1a effort medium mit Fassung im Cache; Region aus Département/Provinz, ohne grossen Kreis; Naturobjekt-Regel für alle Quellen, Gewässer auch aus dem Titel; Schritt 1.5 Kandidaten-Prüfung; Einzelabfrage und Zeitstempel im GUI |
| 5.0.1 | 2026-10-25 | CSV-Felder über 128 KB lesbar (DISCO); gescheiterte Prüfung eines Auftrags wiederholbar |
| 5.0.0 | 2026-10-25 | Haiku 5.5 mit Prompt v4; Schritt 2 (Triage + Opus 5.5); Zuordnung über Nummer + Echo; Akzent-Lücke GeoNames; Aufträge mit Teildateien; Parallelität; Kostengrenze; Server-Betrieb mit Webseite und API |
| 4.x | bis 2026-10-23 | Haiku 4.5, Geostack mit Wikidata, Pictomap-Rückfluss, harte Region (ohne Versionsnummer geführt) |
| 3 | 2026 | erste Fassung mit GEO DB, TGN, Nominatim |
