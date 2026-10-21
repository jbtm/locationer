"""
Wikidata-Geokodierung über die Such-API.

Sucht benannte Objekte mit Koordinaten (P625) — Kirchen, Bahnhöfe, Brücken,
Denkmäler, Berge, Seen. Also genau die Motive historischer Fotoarchive.

CC0-Lizenz — dauerhaftes Zwischenspeichern erlaubt, kommerzielle Nutzung
erlaubt. Keine dokumentierte Ratenbegrenzung, aber faire Nutzung gilt; der
Zwischenspeicher hält die Aufrufzahl klein.

ZWEI DINGE, DIE HIER NICHT WEGOPTIMIERT WERDEN DÜRFEN
─────────────────────────────────────────────────────
**Die Kennung braucht eine Kontaktadresse.** Ohne sie drosselt Wikimedia
härter: mit anonymer Kennung kamen selbst bei einer Sekunde Abstand HTTP 429
zurück, mit Kontakt keine einzige. Das war die Ursache, nicht die Taktrate.

**Ein Fehler ist kein „nichts gefunden".** Eine frühere Fassung fing
Ausnahmen ab und gab `[]` zurück — ununterscheidbar von einer echten
Fehlanzeige, die dann als solche im Zwischenspeicher landete. Ein einzelner
abgewiesener Aufruf hätte ein Objekt damit **dauerhaft** als unauffindbar
festgeschrieben, unsichtbar, über tausende Bilder hinweg. Deshalb trennen
`_abfrage()` und die Aufrufer sauber zwischen „leer" und „gescheitert", und
nur Ersteres wird gemerkt.

WARUM SUCH-API STATT SPARQL
───────────────────────────
Eine frühere Fassung fragte den öffentlichen SPARQL-Endpunkt mit CONTAINS()
über alle Bezeichnungen ab. Das lief in Zeitüberschreitungen, weil Blazegraph
dafür keinen Volltextindex hat und Millionen Labels sequenziell durchgeht —
deshalb wurde Wikidata abgeschaltet.

Gemessen am 2026-08-16:
    SPARQL  wikibase:around, 5 km um London     13.8 s, danach zweimal HTTP 502
    Such-API  "House of Parliament"              0.6 s  → Q62408, 40 m genau

Die Such-API (`wbsearchentities`) arbeitet auf einem serverseitigen Index und
kennt **Aliasnamen**. „House of Parliament" ist ein Alias von „Palace of
Westminster" — über reinen Textvergleich der Hauptbezeichnung wäre das nie
gefunden worden. Genau dieser Fall ging bei Nominatim 7 km daneben.

WAS SIE BEI GATTUNGSBEGRIFFEN TUT — und warum das gut ist
─────────────────────────────────────────────────────────
„Stazione Ferroviaria Napoli" liefert **null Treffer**: das ist kein Name,
sondern das italienische Wort für „Bahnhof". Wikidata sagt ehrlich, dass es
nichts kennt. Nominatim lieferte für dieselbe Anfrage irgendeinen Bahnhof
14 km entfernt und verbuchte ihn als präzisen Treffer.

Ein Nichts-gefunden ist hier also ein brauchbares Ergebnis, kein Fehlschlag.

WELTWEIT GLEICHFÖRMIG
─────────────────────
Anders als nationale Ortsverzeichnisse (SwissNAMES3D für die Schweiz, OS Open
Names für UK) deckt Wikidata alle Länder mit einer Schnittstelle ab. Für ein
Archiv, das international wächst, ist das der Zugang, der mitwächst, ohne je
Land ein eigenes Einpflegeprojekt zu verlangen.
"""

import hashlib
import math
import time
from typing import Optional

import requests

SEARCH_URL = "https://www.wikidata.org/w/api.php"
# Wikimedia verlangt in der Nutzungsrichtlinie eine erreichbare Kontaktadresse.
# Kennungen ohne Kontakt werden frueher gedrosselt — und ein Betreiber, der ein
# Problem sieht, kann sich melden, statt einfach zu sperren.
HEADERS = {"User-Agent": "Locationer/4.0 (https://pictomap.ch; meyer@locomot.ch) python-requests"}

# Wie viele Namenstreffer geholt und auf Koordinaten geprüft werden.  Mehr als
# eine Handvoll bringt nichts: die Such-API sortiert bereits nach Relevanz, und
# jeder weitere Treffer kostet nur beim Koordinaten-Nachschlagen.
SEARCH_LIMIT = 7

# Wikimedia weist zu dichte Abfragen mit HTTP 429 ab.  Ein Abstand von einer
# halben Sekunde bleibt klar im Rahmen fairer Nutzung; der Zwischenspeicher
# sorgt dafuer, dass wiederholte Namen ohnehin nicht erneut gefragt werden.
MIN_ABSTAND_S = 1.0
VERSUCHE = 3


def _key(text: str, lang: str, near, max_dist_km: float, nur_exakt: bool = True) -> str:
    """Schluessel fuer den Zwischenspeicher.

    Sprache UND Ortsanker gehoeren hinein: dieselbe Zeichenkette meint je nach
    Gegend etwas anderes.  „Bahnhof" bei Chur ist ein anderes Objekt als
    „Bahnhof" bei Davos, und ein Treffer, der fuer die eine Stadt verworfen
    wurde, darf fuer die andere nicht aus dem Speicher zurueckkommen.
    """
    anker = f"{near[0]:.2f},{near[1]:.2f},{max_dist_km:.0f}" if near else "-"
    roh = f"wikidata3|{'exakt' if nur_exakt else 'alle'}|{lang}|{anker}|{text.lower().strip()}"
    return hashlib.sha256(roh.encode()).hexdigest()[:20]


class Wikidata:
    def __init__(self, cache_conn, debug: bool = False):
        self.cache_conn = cache_conn
        self.debug = debug
        self.call_count = 0
        self.error_count = 0
        self._letzter_aufruf = 0.0
        self._last_error: str = ""
        self._init_cache()

    def _init_cache(self):
        self.cache_conn.execute("""
            CREATE TABLE IF NOT EXISTS wikidata_cache (
                key          TEXT PRIMARY KEY,
                lat          REAL,
                lon          REAL,
                label        TEXT,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.cache_conn.commit()

    def search(
        self,
        location: str,
        near: Optional[tuple[float, float]] = None,
        max_dist_km: float = 50.0,
        lang: str = "de",
        nur_exakt: bool = True,
    ) -> Optional[dict]:
        """Benanntes Objekt suchen.  Gibt {lat, lon, name, qid} oder None.

        `near` ist der Ortsanker (Stadtzentrum).  Treffer weiter als
        `max_dist_km` davon werden verworfen — das ist der wesentliche Schutz
        gegen gleichnamige Objekte in anderen Weltgegenden.

        Zwei Aufrufe: erst Namen suchen, dann fuer die Kandidaten die
        Koordinaten holen.  Beide auf indizierten Endpunkten, zusammen deutlich
        unter zwei Sekunden.
        """
        if not location or len(location.strip()) < 3:
            return None

        k = _key(location, lang, near, max_dist_km, nur_exakt)
        row = self.cache_conn.execute(
            "SELECT lat, lon, label FROM wikidata_cache WHERE key=?", (k,)).fetchone()
        if row is not None:
            return None if row[0] is None else {"lat": row[0], "lon": row[1],
                                                "name": row[2] or location, "qid": ""}

        self.call_count += 1
        treffer, fehler = self._suche_namen(location, lang)
        if fehler:
            # NICHT als "nichts gefunden" merken: ein abgewiesener Aufruf sieht
            # sonst aus wie eine echte Fehlanzeige und wuerde das Objekt dauerhaft
            # als unauffindbar festschreiben.  Lieber beim naechsten Lauf erneut
            # fragen als still eine Luecke einbrennen.
            self.error_count += 1
            return None
        if not treffer:
            # Kein Name gefunden — bei Gattungsbegriffen der Normalfall und ein
            # brauchbares Ergebnis: der Aufrufer faellt sauber auf den Ortskern
            # zurueck, statt einen Fehlgriff als Treffer zu uebernehmen.
            self._cache_miss(k)
            return None

        if nur_exakt:
            # Nur Objekte behalten, die GENAU so heissen — Bezeichnung oder
            # Aliasname, Gross-/Kleinschreibung egal.  Wortanfang-Treffer wie
            # „House of Parliament from the River Thames" auf die Anfrage
            # „House of Parliament" sind damit draussen.  Ohne diese Schranke
            # gaebe die Suche fuer fast jede Zeichenkette irgendetwas zurueck.
            ziel = location.strip().casefold()
            treffer = [t for t in treffer if (t[3] or "").strip().casefold() == ziel]
            if not treffer:
                self._cache_miss(k)
                return None

        koords, fehler = self._hole_koordinaten([q for q, _, _, _, _ in treffer])
        if fehler:
            self.error_count += 1
            return None
        if not koords:
            self._cache_miss(k)
            return None

        kandidaten = []
        for qid, label, _besch, _mtext, _mtyp in treffer:
            if qid not in koords:
                continue                      # Objekt ohne Koordinate (z.B. Person)
            la, lo = koords[qid]
            if near:
                dist = _haversine(la, lo, near[0], near[1])
                if dist > max_dist_km:
                    continue                  # falsche Weltgegend
            else:
                dist = 0.0
            kandidaten.append((dist, qid, label, la, lo))

        if not kandidaten:
            self._cache_miss(k)
            return None

        # Die Such-API sortiert nach Relevanz; unter den raeumlich zulaessigen
        # Kandidaten nehmen wir den naechstgelegenen — bei mehreren gleichnamigen
        # Objekten in derselben Stadt ist das die plausibelste Wahl.
        kandidaten.sort()
        dist, qid, label, la, lo = kandidaten[0]
        self.cache_conn.execute(
            "INSERT OR REPLACE INTO wikidata_cache (key,lat,lon,label) VALUES (?,?,?,?)",
            (k, la, lo, label))
        self.cache_conn.commit()
        if self.debug:
            print(f"      wikidata: {label!r} ({qid}) {dist:.1f} km vom Anker")
        return {"lat": la, "lon": lo, "name": label, "qid": qid}

    def finde(self, objekt: str, stadt: str, near, max_dist_km: float,
              lang: str = "de", nur_exakt: bool = True):
        """Objekt suchen — mit UND ohne angehaengten Ortsnamen, bester Treffer gewinnt.

        Die beiden Varianten ergaenzen sich, statt sich zu ueberbieten.
        `wbsearchentities` gleicht Bezeichnungen und Aliasnamen ab: „Flüelapass"
        und „Palace of Westminster" stehen so in Wikidata und werden nur ohne
        Ortszusatz gefunden.  Umgekehrt heisst das Zuercher Opernhaus dort nicht
        „Stadttheater", wohl aber „Stadttheater Zürich" — der Ortszusatz ist Teil
        des historischen Namens und damit unverzichtbar.

        Deshalb beide fragen.  Der Ortsanker entscheidet ohnehin: was zu weit weg
        liegt, faellt vorher raus, und von den verbleibenden gewinnt der naechste.
        """
        kandidaten = []
        for text in ([objekt, f"{objekt} {stadt}"] if stadt else [objekt]):
            t = self.search(text, near=near, max_dist_km=max_dist_km, lang=lang,
                            nur_exakt=nur_exakt)
            if t:
                # Abstand hier rechnen, nicht aus search() uebernehmen: bei einem
                # Treffer aus dem Zwischenspeicher gibt es keinen mitgelieferten
                # Abstand, und ein stillschweigendes 0.0 wuerde die Auswahl
                # zwischen den Varianten zur Muenzwurf-Sache machen.
                t = {**t, "dist_km": _haversine(t["lat"], t["lon"], near[0], near[1])
                          if near else 0.0}
                kandidaten.append(t)
        if not kandidaten:
            return None
        return min(kandidaten, key=lambda t: t["dist_km"])

    def _abfrage(self, params):
        """Ein Aufruf mit Drosselung und Wiederholung.

        Gibt (json, fehler) zurueck.  fehler=True heisst: die Antwort war nicht
        auswertbar — das ist ausdruecklich etwas anderes als ein leeres Ergebnis
        und darf nirgends als "nichts gefunden" durchgehen.
        """
        for versuch in range(VERSUCHE):
            wartezeit = MIN_ABSTAND_S - (time.time() - self._letzter_aufruf)
            if wartezeit > 0:
                time.sleep(wartezeit)
            self._letzter_aufruf = time.time()
            try:
                r = requests.get(SEARCH_URL, headers=HEADERS, timeout=20, params=params)
            except Exception as e:
                self._last_error = str(e)
                time.sleep(1.0 * (versuch + 1))
                continue
            if r.status_code == 429:
                self._last_error = "HTTP 429 (Ratenbegrenzung)"
                time.sleep(2.0 * (versuch + 1))
                continue
            if r.status_code != 200:
                self._last_error = f"HTTP {r.status_code}"
                return None, True
            try:
                return r.json(), False
            except ValueError:
                self._last_error = "keine JSON-Antwort"
                return None, True
        return None, True

    def _suche_namen(self, text: str, lang: str):
        """wbsearchentities — Bezeichnungen UND Aliasnamen, serverseitig indiziert."""
        j, fehler = self._abfrage({
            "action": "wbsearchentities", "search": text, "language": lang,
            "uselang": lang, "type": "item", "format": "json", "limit": SEARCH_LIMIT})
        if fehler:
            return [], True
        # `match` sagt, ob die Anfrage auf die Hauptbezeichnung, einen
        # Aliasnamen oder nur auf einen Wortanfang passte.  Das ist das einzige
        # Guetesignal, das ohne Koordinaten auskommt — und damit das einzige,
        # dem wir hier trauen duerfen.
        out = []
        for x in (j or {}).get("search", []):
            m = x.get("match") or {}
            out.append((x["id"], x.get("label", ""), x.get("description", ""),
                        m.get("text", ""), m.get("type", "")))
        return out, False

    def _hole_koordinaten(self, qids):
        """P625 fuer mehrere Objekte in EINEM Aufruf."""
        if not qids:
            return {}, False
        j, fehler = self._abfrage({
            "action": "wbgetentities", "ids": "|".join(qids[:50]),
            "props": "claims", "format": "json"})
        if fehler:
            return {}, True
        out = {}
        for qid, ent in ((j or {}).get("entities") or {}).items():
            for c in (ent.get("claims") or {}).get("P625", []):
                v = (c.get("mainsnak") or {}).get("datavalue", {}).get("value") or {}
                if "latitude" in v:
                    out[qid] = (v["latitude"], v["longitude"])
                    break
        return out, False

    def _cache_miss(self, key: str):
        self.cache_conn.execute(
            "INSERT OR IGNORE INTO wikidata_cache (key,lat,lon,label) VALUES (?,NULL,NULL,NULL)",
            (key,),
        )
        self.cache_conn.commit()


def _haversine(lat1, lon1, lat2, lon2) -> float:
    import math
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2-lat1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
