import hashlib
import html
import json
import re
import threading
import unicodedata
from typing import Optional

import anthropic

from .cache import Cache
from .models import NormalizedRecord

# Maps common column name variants → standard field
COLUMN_MAP: dict[str, list[str]] = {
    "title":       ["title", "titel", "titre", "name", "subject"],
    "description": ["description", "beschreibung", "desc", "text", "caption", "legende"],
    "country":     ["country", "land", "pays", "pais", "country_name"],
    "city":        ["city", "stadt", "ville", "adresse", "address", "ort", "place"],
    "region":      ["region", "kanton", "canton", "bundesland", "state", "province",
                    "provincia", "département", "departement", "county"],
}

META_SYSTEM_PROMPT = """\
Extract metadata from historical photo archive entries.
For each numbered entry return one JSON object in an array:
{
  "n": <the entry number>,
  "ref": "<the first five words of the entry, copied exactly>",
  "periode": "yyyy" or "yyyy-yyyy" or null,
  "urheber": "photographer/artist name" or null,
  "technik": "photography technique (e.g. Albumin, Photochrome, Tirage vintage, Cyanotype, …)" or null
}
Only extract what is EXPLICITLY stated in the text. Never guess or infer. Return null for any field not clearly present.
Return ONLY the JSON array, no prose.\
"""

# Phase 1a.  Fassung vom 2026-10-24, gemessen auf zwei Zufallsstichproben à
# 500 Bilder aus DISCO (TestFile als Kontrolle).  Grundsaetze:
#   - Rangordnung Eingabe > Ableitung aus Hinweisen > Wissen; Wissen fuellt
#     nur Luecken und widerspricht nie (sonst `conflict`).
#   - Stadt aus Wissen nur bei einem Ort, den es in genau EINER Stadt gibt;
#     geprueft wird sie zusaetzlich im Geostack (geocode).
#   - Heutige Namen fuer Land und Stadt, Objektnamen im Original.
# Beispiele bewusst ausserhalb der Mess-Stichproben (Kein Overfitting).
SYSTEM_PROMPT = """\
You extract structured location metadata for a historical photo archive.

For each numbered entry, return one JSON object in an array:
{
  "n":             <the entry number>,
  "ref":           "<the first five words of the entry, copied exactly>",
  "title":         "<max 60 chars, human-readable, same language as the input>",
  "image_type":    "<one of: portrait | group | event | view | landscape | other>",
  "country":       "<English country name where the photo was TAKEN, or empty string>",
  "region":        "<canton, state, province or county — e.g. 'Graubünden', 'Lombardia', 'Bavaria' — infer from geographic context clues even when not explicitly named: a well-known mountain range, valley, lake or peak can strongly imply a region (e.g. 'Berninagruppe', 'Corvatsch', 'Piz Roseg', 'Engadin' → 'Graubünden'; 'Vierwaldstättersee', 'Rigi' → 'Luzern'; 'Jungfrau', 'Eiger' → 'Bern'; 'Mont Blanc' → 'Aosta Valley') — empty string only if genuinely unclear>",
  "city":          "<city or town where the photo was taken, see rules — empty string if unknown>",
  "city_source":   "<'text' if the city name appears in the input, 'knowledge' if you added it from the named place (see rules), '' if city is empty>",
  "location":      "<the PROPER NAME of the most specific named place – e.g. 'Schloss Vaduz', 'Montalin Schulhaus', 'Viamala-Schlucht', 'Sagrada Família', 'Hallgrímskirkja' – empty string if only a city, a person, or a generic scene>",
  "location_type": "<the generic building/place word as it appears in the input text, when location is empty but a recognizable generic feature is present — e.g. 'Bahnhof', 'Kirche', 'église', 'stazione' — null otherwise>",
  "geocoding_queries": ["<1–3 Nominatim search strings for a NAMED location, in the official local language, with the city appended: 'Kathedrale Genève' → ['cathédrale Genève', 'Kathedrale Genf']; NEVER invent a building name not present in the input; when location is empty but location_type and city are set, give the generic word with the correctly spelled city ('stazione', 'Belinzona' → ['stazione Bellinzona']); empty array [] otherwise>"],
  "conflict":      "<one short English sentence if what you know about a named place contradicts the country, region or city stated in the input — e.g. 'Hallgrímskirkja is in Reykjavík, input says Akureyri' — empty string otherwise>"
}

Ranking of sources — higher always wins, lower never overrides higher:
1. Country, region and city stated in the input (fields or text).
2. Region inferred from geographic clues in the input (mountain, valley, lake).
3. Your own knowledge about a named place.
Knowledge may only FILL an empty field, and only if it is consistent with 1 and 2. It never replaces or corrects them. If it contradicts them, keep the input's values, leave the knowledge-based field empty, and describe the contradiction in `conflict`.

Rules for `image_type`:
- portrait: one or more people are the subject (studio, posed, carte de visite), even if a place is named.
- group: a posed group of people (delegations, soldiers, students, family) — the people are the subject.
- event: something happening at a place (ceremony, procession, celebration, market scene, execution).
- view: a building, monument, street, square, interior or city view is the subject.
- landscape: nature, mountains, rivers, coast, countryside.
- other: aircraft, vehicles, documents, reproductions of artworks, anything else.

Rules for `city`:
- If a city or town is named in the input, use it ('text'). It stays even if several towns share the name, and also for portraits and events: 'Italie, Procida' → 'Procida'; 'Nach dem Erdbeben von Skopje' → 'Skopje'.
- Write the city in its CURRENT standard name, not a historical or foreign-language form: 'Pressburg' → 'Bratislava', 'Varsovie' → 'Warszawa', 'Mailand' → 'Milano'. This is still 'text'.
- Otherwise, if `location` names a specific, well-known place that exists in exactly ONE town, give that town ('knowledge'): 'Hallgrímskirkja' → 'Reykjavík', 'Taj Mahal' → 'Agra', 'Sagrada Família' → 'Barcelona'.
- A knowledge city must lie inside the stated country and region. If it does not, city = '' and fill `conflict`.
- Never infer a city from a person, a photographer, an event without a named place, a region, or the general theme of the collection. If in doubt, leave it empty.
- Include a disambiguation suffix if present, e.g. 'Bingen am Rhein' not 'Bingen'.

Rules for `country`:
- The country where the photo was taken, from the input or implied by a named place ('Acropolis' → 'Greece').
- Always use the CURRENT English country name, also for historical names in the input: 'Persia' → 'Iran', 'Ceylon' → 'Sri Lanka', 'Bohemia' → 'Czechia', 'Siam' → 'Thailand'. Translating a name is not a contradiction.
- If the input's country is a historical state or area whose borders differ from today's (e.g. Ottoman Empire, Prussia, Austria-Hungary, Indochine, Palestine before 1948, Syria before 1920), give the CURRENT country in which the named place lies: 'Prusse, Königsberg' → 'Russia'; 'Autriche-Hongrie, Cracovie' → 'Poland'. This is not a contradiction. Without a named place, leave country empty.
- If the input names a country that contradicts a named place you are sure about, keep the input's country — do not correct it — and fill `conflict`.
- For portraits and groups without any place in the input, give the country only if the input states it. Do not guess from the collection's theme.

Rules for `region`:
- If the input states a region, keep it exactly, even if a named place suggests another one.
- Infer a region from geographic clues only when the input states none. This applies worldwide, not only to mountains: a river, island, coast, park or landmark that lies in one region implies it ('Loreley' → 'Rhineland-Palatinate', 'Grand Canyon' → 'Arizona'). Never infer it from your knowledge of where a named building is when that contradicts the input.

Rules for `location`:
- Must be a proper name. Descriptive phrases are NOT names: 'Old Town School for Girls', 'a city gate', 'the royal palace', 'view of the harbour' → location = '' (use location_type for the generic word).
- Never translate. Use the original spelling of the input.
- Split compound nouns when the prefix is a proper noun: 'Montalinschulhaus' → 'Montalin Schulhaus', 'Quaderschulhaus' → 'Quader Schulhaus'.
- If the title itself is a specific named place, use it as the location.
- Generic words alone (Kirche, Bahnhof, Schulhaus, Hotel, church, market) → empty string.

Rules for `location_type`:
- Only set when `location` is empty and a generic building type is clearly present.
- Use the word exactly as it appears in the input: 'Bahnhof' not 'railway station', 'Kirche' not 'church'.
- Examples: 'Göschenen, Bahnhof, Gotthardbahn' → location='', location_type='Bahnhof'

HTML entities are already decoded. Return ONLY the JSON array, no prose.\
"""


def _map_columns(row: dict, extra_desc_cols: list[str] | None = None) -> dict[str, str]:
    low = {str(k).lower().strip(): v for k, v in row.items()}
    out = {f: "" for f in COLUMN_MAP}
    for field, aliases in COLUMN_MAP.items():
        for alias in aliases:
            if alias in low:
                v = low[alias]
                out[field] = "" if v is None or (isinstance(v, float) and v != v) else str(v).strip()
                break
    # Append extra columns to description so Phase 1a Haiku sees them
    if extra_desc_cols:
        extras = []
        for col in extra_desc_cols:
            v = low.get(col.lower().strip())
            v_str = "" if v is None or (isinstance(v, float) and v != v) else str(v).strip()
            if v_str:
                extras.append(v_str)
        if extras:
            sep = " | " if out["description"] else ""
            out["description"] = out["description"] + sep + " | ".join(extras)
    return out


def _clean_wikimedia(text: str) -> str:
    """Strip Wikimedia multilingual markup: 'de|1=German text|1=English text' → 'German text English text'."""
    if '|' not in text or '=' not in text:
        return text
    parts = text.split('|')
    texts = [p.split('=', 1)[1].strip() for p in parts if '=' in p and p.split('=', 1)[1].strip()]
    return ' '.join(texts) if texts else text


def _clean(text: str) -> str:
    return _clean_wikimedia(html.unescape(text or "").strip())


def _raw_key(mapped: dict[str, str]) -> str:
    parts = "|".join(f"{k}:{mapped[k]}" for k in sorted(mapped) if mapped[k])
    return hashlib.sha256(parts.encode()).hexdigest()[:20]


# Swiss/Austrian/German canton and state codes to strip from city names
_REGION_CODES = {
    "AG","AI","AR","BE","BL","BS","FR","GE","GL","GR","JU","LU",
    "NE","NW","OW","SG","SH","SO","SZ","TG","TI","UR","VD","VS",
    "ZG","ZH","LI",  # CH + LI
    "NW","OW",       # NW/OW duplicates — explicit
}


_TITLE_GENERIC = {
    "ortsteilansicht", "gesamtansicht", "ortsgesamtansicht", "teilansicht",
    "gesamtaussenansicht", "teilaussenansicht", "gesamtinnenansicht", "teilinnenansicht",
    "dorfteilansicht", "ortsansicht", "dorfansicht", "stadtansicht", "dorfbild",
    "ansicht", "aussenansicht", "innenansicht", "gesamtbild", "ortsgesamtbild", "ortsbild",
    "landschaft", "panorama", "berglandschaft", "seelandschaft", "winterlandschaft",
    "portrait", "portrat",
}


def _extract_location_from_title(title: str, city: str) -> str:
    """Extract location from a Haiku-enriched title when the location field is empty.

    "Genève, Pont des Bergues, Teilansicht" + city "Genève" → "Pont des Bergues"
    "Genève, Ortsteilansicht"              + city "Genève" → "" (generic, skip)
    """
    if not city or not title:
        return ""
    prefix = city + ","
    if not title.lower().startswith(prefix.lower()):
        return ""
    rest = title[len(prefix):].strip()
    candidate = rest.split(",")[0].strip()
    if not candidate:
        return ""
    # Reject if the candidate itself or its first word is a generic view/scene word
    first_word = candidate.lower().split()[0].rstrip(".,")
    if candidate.lower() in _TITLE_GENERIC or first_word in _TITLE_GENERIC:
        return ""
    return candidate


def _normalize_city(city: str) -> str:
    """Strip postal codes, canton codes and similar clutter from city strings.

    Examples: 'Chur GR' → 'Chur', '7000 Chur' → 'Chur', 'Kilchberg (ZH)' → 'Kilchberg',
              'Kanton Luzern' → 'Luzern', 'Kt. Bern' → 'Bern'
    """
    if not city:
        return city
    city = city.strip()
    # Remove leading postal code: "7000 Chur" or "CH-7000 Chur"
    city = re.sub(r"^(?:[A-Z]{1,3}-?)?\d{4,5}\s+", "", city)
    # Remove trailing "(ZH)" style
    city = re.sub(r"\s*\([A-Z]{2}\)\s*$", "", city)
    # Strip "Kanton " / "Kt. " / "Kt " prefix: "Kanton Luzern" → "Luzern"
    city = re.sub(r"^Kt\.?\s+", "", city, flags=re.IGNORECASE)
    city = re.sub(r"^Kanton\s+", "", city, flags=re.IGNORECASE)
    city = re.sub(r"^Canton\s+", "", city, flags=re.IGNORECASE)
    # Remove trailing canton/state code: "Chur GR"
    parts = city.split()
    if len(parts) >= 2 and parts[-1].upper() in _REGION_CODES:
        city = " ".join(parts[:-1])
    return city.strip()


_IST_URL = re.compile(r"https?://", re.IGNORECASE)

# Haiku 5.5 mit wenig Denkaufwand, fuer Phase 1a und 1b.  Gemessen 2026-10-24
# gegen Haiku 4.5: gleich viele Koordinaten, bei Abweichungen oefter richtig,
# rund ein Viertel der Kosten.  „medium" war nicht besser, aber langsamer.
# Haiku 5.5 denkt vor der Antwort; darum der grosse max_tokens-Rahmen (es
# wird nur verrechnet, was gebraucht wird) und der Text aus dem Textblock,
# nicht aus content[0] — das kann ein Denkblock sein.
MODEL = "claude-haiku-5-5"
_MODEL_KW = {"output_config": {"effort": "low"}}
_MAX_TOKENS = 16000


def _antworttext(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text")


def _parse_array(raw: str) -> "list | None":
    """JSON-Array aus Haikus Antwort, None wenn keines lesbar ist."""
    raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        try:
            data = json.loads(m.group()) if m else None
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, list):
        return None
    return [d if isinstance(d, dict) else {} for d in data]


def _ordne(data: "list | None", n: int) -> "list | None":
    """Antworten ueber ihre Nummer `n` zuordnen — None, wenn das nicht aufgeht.

    Die Reihenfolge allein reicht nicht: Haiku 5.5 vertauschte am 2026-10-24
    zwei Nachbarn bei richtiger Anzahl (DISCO, 1 von 2000 Zeilen) — die
    Koordinaten der beiden Bilder waren damit vertauscht, ohne Meldung.
    """
    if data is None:
        return None
    try:
        nummern = [int(d.get("n")) for d in data]
    except (TypeError, ValueError):
        return None
    if sorted(nummern) != list(range(1, n + 1)):
        return None
    return [d for _, d in sorted(zip(nummern, data), key=lambda t: t[0])]


# Feldnamen stehen in jedem Eintrag — sie duerfen keine Gemeinsamkeit stiften.
_FELDNAMEN = {"title", "description", "country", "city", "region"}


def _woerter(s) -> set:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return set(re.findall(r"[^\W_]{2,}", s))


def _zuordnen(data: "list | None", texte: list[str]) -> "list | None":
    """Antworten den Eintraegen zuordnen — None, wenn auch nur einer nicht passt.

    Zwei unabhaengige Pruefungen, beide muessen halten:
      1. Nummer: jede Antwort traegt die Nummer ihres Eintrags (`n`), jede
         Nummer genau einmal (_ordne).
      2. Echo: jede Antwort gibt die ersten Woerter ihres Eintrags zurueck
         (`ref`); mindestens die Haelfte davon muss im Eintrag stehen, den
         der Code selbst geschickt hat.  Vertauscht das Modell zwei Antworten
         samt Nummern, traegt die eine die Woerter des anderen Bildes.
    Bei None halbiert der Aufrufer den Batch und fragt neu, bis hinunter zu
    einem einzelnen Eintrag — dort ist keine Verwechslung moeglich.
    """
    geordnet = _ordne(data, len(texte))
    if geordnet is None:
        return None
    for d, text in zip(geordnet, texte):
        ref = _woerter(d.get("ref")) - _FELDNAMEN
        ein = _woerter(text) - _FELDNAMEN
        if not ein:
            continue                      # Eintrag ohne Woerter: nichts zu pruefen
        if not ref or len(ref & ein) < max(1, (len(ref) + 1) // 2):
            return None
    return geordnet


def _verrutscht(data: "list | None", batch: list) -> bool:
    """Passt die Antwort nicht Eintrag fuer Eintrag zum Batch?

    Haiku laesst gelegentlich einen Eintrag aus oder vertauscht zwei.  Die Antwort wird nur ueber
    die Position zugeordnet — fehlt Nr. 15 von 20, gehoeren 16–20 zu den
    falschen Bildern.  Beobachtet am 2026-10-24 (500er-Stichprobe DISCO):
    das Volta-Denkmal in Como bekam die Koordinate von Honolulu, ohne jede
    Fehlermeldung.  Fruehere Fassung fuellte nur am Ende auf, was genau
    diese Verschiebung stehen liess.

    Dann wird der Batch halbiert und neu gefragt, bis die Anzahl stimmt.
    Ein einzelner Eintrag ohne brauchbare Antwort bleibt leer.
    """
    return len(batch) > 1 and _ordne(data, len(batch)) is None


def _meta_raw_key(row: dict) -> str:
    """Cache key for metadata extraction: hash of all non-null raw text values.

    Ohne Links: Bild-URLs (recto/verso) machten jede Zeile einmalig — im
    DISCO-Bestand 705224 Anfragen statt 578245.  Haiku sieht sie trotzdem
    (_call_meta_ai), sie aendern Periode/Urheber/Technik aber nicht.
    """
    raw = " ".join(
        html.unescape(str(v)).strip()
        for v in row.values()
        if v is not None and str(v).strip() and not _IST_URL.match(str(v).strip())
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


class InputNormalizer:
    BATCH_SIZE = 20

    def __init__(self, cache: Cache, debug: bool = False, tgn_db=None, geo_db=None,
                 extra_desc_cols: list[str] | None = None):
        self.cache = cache
        self.debug = debug
        self.tgn_db = tgn_db
        self.geo_db = geo_db
        self.extra_desc_cols = extra_desc_cols or []  # extra cols merged into description
        self.haiku_count = 0
        self._client: Optional[anthropic.Anthropic] = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            # Zeitgrenze und Wiederholungen ausdruecklich setzen.
            #
            # Ohne sie blockiert eine Verbindung, die zwar steht, aber nichts
            # mehr liefert, den GESAMTEN Lauf — beobachtet am 2026-08-19: der
            # Prozess hing 32 Minuten an einem einzigen Aufruf, ohne Fehler und
            # ohne Fortschritt.  Bei 6000 Bildern ist das kein Randfall, sondern
            # eine Frage der Zeit.
            #
            # 60 s ist grosszuegig fuer eine Normalisierung von wenigen hundert
            # Zeichen; was laenger braucht, ist haengengeblieben und gehoert
            # abgebrochen und wiederholt.
            self._client = anthropic.Anthropic(timeout=60.0, max_retries=3)
        return self._client

    # Harte Wanduhr-Grenze pro Aufruf.  `timeout=60` oben misst nur die Pause
    # zwischen zwei Datenpaketen; am 2026-10-17 hing ein Aufruf trotzdem 30
    # Minuten an einer stehenden Verbindung.  Darum laeuft jeder Aufruf in
    # einem Thread mit Frist: nach _FRIST_S wird er aufgegeben und neu
    # gestellt; nach _VERSUCHE Fehlschlaegen bricht der Lauf ab (er laesst
    # sich danach fortsetzen).  Ein aufgegebener Thread laeuft im Hintergrund
    # aus — sein Ergebnis wird verworfen.  API-Fehler kommen unveraendert
    # durch (die Wiederholung dafuer macht der Client selbst).
    _FRIST_S = 120
    _VERSUCHE = 3

    def _create(self, **kwargs):
        for versuch in range(1, self._VERSUCHE + 1):
            ergebnis: dict = {}

            def aufruf():
                try:
                    ergebnis["ok"] = self.client.messages.create(**kwargs)
                except Exception as e:      # im Hauptthread neu werfen
                    ergebnis["fehler"] = e

            # daemon: ein haengender Thread haelt das Programmende nicht auf
            t = threading.Thread(target=aufruf, daemon=True)
            t.start()
            t.join(self._FRIST_S)
            if "fehler" in ergebnis:
                raise ergebnis["fehler"]
            if "ok" in ergebnis:
                return ergebnis["ok"]
            print(f"  [Haiku] keine Antwort nach {self._FRIST_S} s "
                  f"(Versuch {versuch}/{self._VERSUCHE})")
        raise TimeoutError(f"Haiku: {self._VERSUCHE}x keine Antwort nach {self._FRIST_S} s")

    def normalize_batch(self, rows: list[dict]) -> list[NormalizedRecord]:
        total = len(rows)
        results: list[tuple[int, NormalizedRecord]] = []
        pending: list[tuple[int, str, dict[str, str]]] = []
        doppelt: list[tuple[int, str]] = []   # gleiche Zeile schon in pending

        offen: set[str] = set()
        for i, row in enumerate(rows):
            mapped = {k: _clean(v) for k, v in _map_columns(row, self.extra_desc_cols).items()}
            rk = _raw_key(mapped)
            cached = self.cache.get_norm(rk)
            if cached:
                results.append((i, cached))
            elif rk in offen:
                doppelt.append((i, rk))
            else:
                offen.add(rk)
                pending.append((i, rk, mapped))

        if not self.debug and pending:
            print(f"\r  Phase 1: {total - len(pending)}/{total} (cache hits)", end="", flush=True)

        for start in range(0, len(pending), self.BATCH_SIZE):
            batch = pending[start : start + self.BATCH_SIZE]
            ai_records = self._call_ai(batch)
            for (i, rk, _), rec in zip(batch, ai_records):
                self.cache.set_norm(rk, rec)
                results.append((i, rec))
            if not self.debug:
                done = total - len(pending) + start + len(batch)
                print(f"\r  Phase 1: {done}/{total}", end="", flush=True)

        # Doppelte Zeilen aus dem Cache: je ein eigenes Objekt, weil spaeter
        # pro Zeile weiter veraendert wird (Overrides, Stadt ergaenzen).
        results += [(i, self.cache.get_norm(rk)) for i, rk in doppelt]

        if not self.debug:
            print(f"\r  Phase 1: {total}/{total} done          ")

        results.sort(key=lambda x: x[0])
        normalized = [r for _, r in results]

        # Originaltitel je Zeile (nicht im Cache: gleicher Schluessel, aber
        # jede Zeile bekommt ihn aus der eigenen Eingabe).
        for rec, row in zip(normalized, rows):
            rec.input_title = _clean(_map_columns(row, self.extra_desc_cols)["title"])

        # Post-process: extract location from enriched title for stale cache entries
        # where Haiku left location='' but wrote the place name into the title.
        for i, rec in enumerate(normalized):
            if not rec.location and rec.city and rec.title:
                extracted = _extract_location_from_title(rec.title, rec.city)
                if extracted:
                    rec.location = extracted
                    # Update cache so future runs see the corrected location
                    mapped = {k: _clean(v) for k, v in _map_columns(rows[i], self.extra_desc_cols).items()}
                    self.cache.set_norm(_raw_key(mapped), rec)

        # ── Phase 1c: TGN Resolver ───────────────────────────────────────────
        # Fast local lookup — no AI call, no network.
        # Adds tgn_id + tgn_name to each record for use in Phase 2 Step 2.5.
        if self.tgn_db:
            if not self.debug:
                print(f"\r  Phase 1c: TGN lookup…", end="", flush=True)
            hits = 0
            for rec in normalized:
                if rec.location:
                    country_code = (
                        self.geo_db.country_to_code(rec.country)
                        if self.geo_db and rec.country else None
                    )
                    row = self.tgn_db.find(rec.location, country_code=country_code)
                    if row and row["lat"] is not None:
                        rec.tgn_id   = row["tgn_id"]
                        rec.tgn_name = row["pref_name"] or rec.location
                        hits += 1
            if not self.debug:
                print(f"\r  Phase 1c: {hits}/{total} TGN hits          ")

        return normalized

    def extract_metadata_batch(self, rows: list[dict]) -> list[dict]:
        """Extract periode/urheber/technik for each row. Per-row cached by input-text hash."""
        results: list[tuple[int, dict]] = []
        pending: list[tuple[int, str, dict]] = []
        doppelt: list[tuple[int, str]] = []

        offen: set[str] = set()
        for i, row in enumerate(rows):
            rk = _meta_raw_key(row)
            cached = self.cache.get_meta(rk)
            if cached is not None:
                results.append((i, cached))
            elif rk in offen:
                doppelt.append((i, rk))
            else:
                offen.add(rk)
                pending.append((i, rk, row))

        total_meta = len(rows)
        if not self.debug and pending:
            print(f"\r  Phase 1b: {total_meta - len(pending)}/{total_meta} (cache hits)", end="", flush=True)

        for start in range(0, len(pending), self.BATCH_SIZE):
            batch = pending[start : start + self.BATCH_SIZE]
            meta_results = self._call_meta_ai(batch)
            for (i, rk, _), meta in zip(batch, meta_results):
                self.cache.set_meta(rk, meta)
                results.append((i, meta))
            if not self.debug:
                done = total_meta - len(pending) + start + len(batch)
                print(f"\r  Phase 1b: {done}/{total_meta}", end="", flush=True)

        results += [(i, self.cache.get_meta(rk)) for i, rk in doppelt]

        if not self.debug:
            print(f"\r  Phase 1b: {total_meta}/{total_meta} done          ")

        results.sort(key=lambda x: x[0])
        return [r for _, r in results]

    def _call_meta_ai(self, batch: list[tuple]) -> list[dict]:
        lines = []
        for idx, (_, _, row) in enumerate(batch):
            text = " | ".join(
                html.unescape(str(v)).strip()
                for v in row.values()
                if v is not None and str(v).strip()
            )[:400]
            lines.append(f'{idx + 1}. "{text}"')

        self.haiku_count += 1
        response = self._create(
            model=MODEL,
            max_tokens=_MAX_TOKENS,
            system=META_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "Entries:\n" + "\n".join(lines)}],
            **_MODEL_KW,
        )

        data = _parse_array(_antworttext(response))
        geordnet = _zuordnen(data, lines)
        if geordnet is None and len(batch) > 1:
            mitte = len(batch) // 2
            return self._call_meta_ai(batch[:mitte]) + self._call_meta_ai(batch[mitte:])
        data = geordnet or data or []

        return [
            {
                "periode": (data[k] if k < len(data) else {}).get("periode"),
                "urheber": (data[k] if k < len(data) else {}).get("urheber"),
                "technik": (data[k] if k < len(data) else {}).get("technik"),
            }
            for k in range(len(batch))
        ]

    def _call_ai(self, batch: list[tuple[int, str, dict[str, str]]]) -> list[NormalizedRecord]:
        lines = []
        for idx, (_, _, mapped) in enumerate(batch):
            entry = [f"{idx + 1}."]
            for field in ("title", "description", "country", "city"):
                if mapped[field]:
                    entry.append(f'  {field}="{mapped[field][:300]}"')
            lines.append("\n".join(entry))

        prompt = "Entries:\n" + "\n".join(lines)

        self.haiku_count += 1
        response = self._create(
            model=MODEL,
            max_tokens=_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            **_MODEL_KW,
        )

        data = _parse_array(_antworttext(response))
        geordnet = _zuordnen(data, lines)
        if geordnet is None and len(batch) > 1:
            mitte = len(batch) // 2
            return self._call_ai(batch[:mitte]) + self._call_ai(batch[mitte:])
        data = geordnet or data or []

        records = []
        for k, (_, _, mapped) in enumerate(batch):
            item = data[k] if k < len(data) else {}
            ai_city    = str(item.get("city", "")).strip()
            input_city = str(mapped.get("city", "")).strip()
            # If AI city has no substring relation to the input value, the AI likely
            # corrupted it (e.g. "Luzern" → "Lzern"). Fall back to the original.
            city_source = str(item.get("city_source") or "").strip()
            if (input_city and ai_city
                    and input_city.lower() not in ai_city.lower()
                    and ai_city.lower() not in input_city.lower()):
                city = _normalize_city(input_city)
            else:
                city = _normalize_city(ai_city if ai_city else input_city)
            if input_city:
                city_source = "text"     # Eingabespalte, nie Wissen
            if not city:
                city_source = ""
            location = str(item.get("location", ""))
            # If Haiku found no specific location but identified a generic building
            # type, construct a search string: "Railway Station Göschenen"
            if not location and city:
                loc_type = (item.get("location_type") or "").strip()
                if loc_type:
                    location = f"{loc_type.title()} {city}"
            # If still no location, extract from Haiku-enriched title.
            # Haiku sometimes writes the place name into the title but forgets
            # to set it as location (e.g. "Genève, Pont des Bergues, Teilansicht").
            if not location and city:
                ai_title = str(item.get("title", "")).strip()
                location = _extract_location_from_title(ai_title, city)
            # Region priority: input column > Haiku extraction.
            # If the input CSV has a canton/region column, it is authoritative.
            ai_region = str(item.get("region", "")).strip()
            input_region = mapped.get("region", "").strip()
            region = input_region if input_region else ai_region

            raw_gq = item.get("geocoding_queries", [])
            if isinstance(raw_gq, list):
                geocoding_queries = [
                    str(q).strip() for q in raw_gq
                    if q and str(q).strip() and str(q).strip().lower() != location.lower()
                ]
            else:
                geocoding_queries = []

            records.append(
                NormalizedRecord(
                    title=str(item.get("title", mapped.get("title", "")))[:60],
                    description=mapped.get("description", ""),
                    country=str(item.get("country", mapped.get("country", ""))),
                    region=region,
                    city=city,
                    location=location,
                    geocoding_queries=geocoding_queries,
                    image_type=str(item.get("image_type") or "").strip(),
                    city_source=city_source,
                    conflict=str(item.get("conflict") or "").strip(),
                )
            )
        return records
