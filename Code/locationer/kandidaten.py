"""Schritt 1.5 — Kandidaten pruefen, wo der Geostack ein Objekt nicht genau fand.

Der Geostack verlangt exakte Namen.  „Refugio di Torino" findet die Hütte
nicht, „Rifugio Torino" schon; „The Washington Memorial Chapel" nichts,
ohne „The" alles.  Eine Liste von Abwandlungen (Artikel, Schreibweisen) waere
sprachabhaengig und wackelig (Dec, 2026-10-25).  Stattdessen wie ein Mensch:

1. Kandidaten sammeln, ohne Modell: Wikidata-Volltextsuche mit dem Objekt,
   nur Dinge mit Koordinate, im Umkreis des Ortsankers, bei leerem Ergebnis
   unscharf (wikidata.Wikidata.kandidaten).  Was ausserhalb von Land/Region
   liegt oder — bei Gebaeuden — ausserhalb der Gemeinde, faellt vorher raus.
2. Haiku prueft kritisch: Titel, Beschreibung, Ort und je Kandidat
   Bezeichnung, Beschreibung, Abstand.  „Welcher ist genau gemeint? Wenn
   keiner sicher passt: keiner."
3. Uebernommen wird nur ein Kandidat mit Sicherheit „high", mit der
   Koordinate aus Wikidata — nie vom Modell.

Nur fuer Nicht-Portraets mit genanntem Objekt, die auf die Ortsmitte
zurueckgefallen sind oder gar nichts haben.  Was danach noch ohne Koordinate
ist, faengt Schritt 2 (nachfassen.py) auf.  Abschaltbar mit KANDIDATEN=0.
"""
import hashlib
import json
import string

from .geo_db import gemeinde_von
from .geostack import _city_radius_km, _dist_km, _natur, _norm_vergleich, _within_collection_bbox, _wikidata_lang
from .input_normalizer import MODEL, _MAX_TOKENS, _antworttext, _parse_array, _zuordnen
from .models import GeoResult, NormalizedRecord

_KW = {"output_config": {"effort": "medium"}}
_BATCH = 10
_MAX_KANDIDATEN = 10

PROMPT = """\
You check candidate places for entries of a historical photo archive. Another system extracted the place name ("object") but found no exact match. A search found the candidates listed under each entry by name similarity; many of them are wrong.
For each numbered entry return one JSON object in an array:
{"n": <the entry number>, "ref": "<the first five words of the entry, copied exactly>", "choice": "<the letter of the candidate that is exactly the place the entry means, or ''>", "certainty": "high" | "medium" | "low", "shape": "point" | "area" | "line", "reason": "<one short sentence>"}
Rules:
- Choose a candidate only if it is the same place the entry means: the same kind of object (a hut is not a peak, a church is not a street), a compatible location (town, region, country), and nothing in its description contradicts the entry.
- A similar name alone is not enough. Spelling variants, translations, historical names and a leading article count as the same name.
- If the entry names a town and the candidate lies far from it, do not choose it.
- A candidate "in a neighbouring municipality" fits only if the entry names that specific, uniquely named place (e.g. a named castle near the town). A generic word (town hall, station, market, church, café) means the one in the named town itself — then do not choose a candidate from a neighbouring municipality.
- If no candidate fits, or several fit equally well, choice = ''.
- certainty 'high' only if you are sure this candidate is meant.
- shape of the chosen place: "line" for things that stretch along a route (wall, railway line, road, canal, river as a whole, border); "area" for valleys, regions, lakes, glaciers; else "point".
Return ONLY the JSON array.\
"""


def _key(rec: NormalizedRecord, kand: list[dict]) -> str:
    raw = "|".join([rec.input_title or rec.title, rec.description or "", rec.country or "",
                    rec.region or "", rec.city or "", rec.location or "",
                    ",".join(k["qid"] for k in kand)])
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


class Kandidatenpruefer:
    def __init__(self, normalizer, geostack, cache, debug: bool = False):
        self.normalizer = normalizer      # _create: Frist, Wiederholung, Kasse
        self.geostack = geostack
        self.geo_db = geostack.geo_db
        self.wikidata = geostack.wikidata
        self.conn = cache.conn
        self.debug = debug
        self.geprueft = self.gefunden = 0
        self.conn.execute("""CREATE TABLE IF NOT EXISTS kandidat_cache (
            key TEXT PRIMARY KEY, antwort TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        self.conn.commit()

    # ── Kandidaten sammeln ───────────────────────────────────────────────────
    def _sammeln(self, rec: NormalizedRecord) -> list[dict]:
        gs, db = self.geostack, self.geo_db
        region = gs._region(rec)
        cc, a1, mitte, r_region = region
        city_row = db.find_city(rec.city, cc, a1)[0] if (db and rec.city) else None
        if city_row is not None and cc and city_row["country_code"] != cc:
            city_row = None
        if city_row is not None:
            # Weit suchen; fuer Gebaeude entscheidet danach die Gemeinde
            # (_ausserhalb_gemeinde), nicht der Kreis — das Hospiz auf dem
            # Grossen St. Bernhard liegt 9 km vom Dorf, aber in der Gemeinde.
            near = (city_row["lat"], city_row["lon"])
            city_r = _city_radius_km(city_row)
            radius = max(city_r * 6, 15)
        elif mitte and r_region:
            near, city_r, radius = mitte, 5.0, r_region
        else:
            near, city_r, radius = None, 5.0, 0.0
        liste, fehler = self.wikidata.kandidaten(rec.location, near, radius,
                                                 lang=_wikidata_lang(cc))
        if fehler:
            return []
        # Naturobjekt nur aus der Beschreibung, Ort aus dem Text: die Kamera
        # stand im Ort.  Dieselbe Regel wie geostack._naturobjekt_beim_ort —
        # ohne sie bekam „Zermatt, Ortsgesamtansicht … im Hintergrund das
        # Matterhorn" den Gipfel (Kontrolllauf 2026-10-25, rund 30 ZIN-Bilder).
        if (city_row is not None and _natur(rec) and rec.city_source == "text" and rec.input_title
                and _norm_vergleich(rec.location) not in _norm_vergleich(rec.input_title)):
            radius = city_r
        # Der Kreis gilt, ausser fuer ein Gebaeude, dessen Gemeinde sich pruefen
        # laesst — dort entscheidet die Gemeinde.
        gemeinde = (city_row is not None and rec.location_kind == "building"
                    and gemeinde_von(city_row) is not None)
        out = []
        for k in liste:
            if cc and not gs._in_region(k["lat"], k["lon"], region):
                continue
            if not cc and not _within_collection_bbox(k["lat"], k["lon"], gs.collection_bbox):
                continue
            if near and not gemeinde and _dist_km(k["lat"], k["lon"], near[0], near[1]) > radius:
                continue
            nachbar = False
            if city_row is not None and gs._ausserhalb_gemeinde(k["lat"], k["lon"], rec, city_row,
                                                                 near, city_r):
                # Nachbargemeinde im Stadtradius: nicht verwerfen,
                # sondern dem Modell mit Hinweis vorlegen.  Alte Titel nennen
                # oft die naechste Stadt („Bingen, Château de Rheinstein" —
                # die Burg steht in Trechtingshausen, 4 km).  Ob ein eindeutiger
                # Name oder ein Gattungswort („Stadthaus") gemeint ist, kann
                # nur das Modell beurteilen; eine Wortliste waere sprachabhaengig.
                if _dist_km(k["lat"], k["lon"], near[0], near[1]) > max(city_r, 5):
                    continue          # Winterthur → Stadthaus Zuerich (20 km) bleibt draussen
                nachbar = True
            k = dict(k)
            if city_row is not None:
                k["abstand"] = (f"{_dist_km(k['lat'], k['lon'], near[0], near[1]):.1f} km from {city_row['name']}"
                                + (", in a neighbouring municipality" if nachbar else ""))
            out.append(k)
        return out[:_MAX_KANDIDATEN]

    # ── Modell ───────────────────────────────────────────────────────────────
    @staticmethod
    def _eintrag(n: int, rec: NormalizedRecord, kand: list[dict]) -> str:
        e = [f"{n}."]
        for feld, wert in (("title", rec.input_title or rec.title), ("description", rec.description),
                           ("country", rec.country), ("region", rec.region), ("city", rec.city),
                           ("object", rec.location)):
            if wert:
                e.append(f'  {feld}="{str(wert)[:300]}"')
        e.append("  candidates:")
        for b, k in zip(string.ascii_uppercase, kand):
            teile = [k["label"] or k["qid"], k.get("beschreibung") or "", k.get("abstand") or ""]
            e.append(f"    {b}) " + " — ".join(t for t in teile if t))
        return "\n".join(e)

    def _frage(self, faelle: list[tuple[NormalizedRecord, list[dict]]]) -> list[dict]:
        texte = [self._eintrag(n + 1, rec, kand) for n, (rec, kand) in enumerate(faelle)]
        r = self.normalizer._create(model=MODEL, max_tokens=_MAX_TOKENS, system=PROMPT,
                                    messages=[{"role": "user", "content": "Entries:\n" + "\n".join(texte)}],
                                    **_KW)
        data = _parse_array(_antworttext(r))
        geordnet = _zuordnen(data, texte)
        if geordnet is None and len(faelle) > 1:
            m = len(faelle) // 2          # gleiche Absicherung wie Phase 1a
            return self._frage(faelle[:m]) + self._frage(faelle[m:])
        data = geordnet or []
        return data[:len(faelle)] + [{}] * (len(faelle) - len(data))

    # ── Einstieg ─────────────────────────────────────────────────────────────
    def pruefen(self, records: list[NormalizedRecord], geos: list[GeoResult],
                unbekannt: list[bool]) -> None:
        if not self.wikidata:
            return
        faelle = []          # (index, rec, kandidaten, key)
        for k, (rec, g, u) in enumerate(zip(records, geos, unbekannt)):
            if u or not rec.location or rec.image_type in ("portrait", "group"):
                continue
            if g.lat is not None and not g.fallback:
                continue                  # genauer Treffer: nichts zu pruefen
            kand = self._sammeln(rec)
            if kand:
                faelle.append((k, rec, kand, _key(rec, kand)))
        if not faelle:
            return
        self.geprueft += len(faelle)

        antworten: dict[str, dict] = {}
        offen = []
        for f in faelle:
            row = self.conn.execute("SELECT antwort FROM kandidat_cache WHERE key=?", (f[3],)).fetchone()
            if row is not None:
                antworten[f[3]] = json.loads(row[0])
            elif f[3] not in {o[3] for o in offen}:
                offen.append(f)
        if self.debug:
            for _, rec, kand, _ in faelle:
                print(f"  [kandidaten] {rec.title[:50]!r} — {len(kand)} Kandidaten aus Wikidata:")
                for b, k in zip(string.ascii_uppercase, kand):
                    print(f"      {b}) {k['label']} ({k['qid']}) — {k.get('beschreibung', '')[:60]}"
                          f"{' — ' + k['abstand'] if k.get('abstand') else ''}")
        for s in range(0, len(offen), _BATCH):
            teil = offen[s:s + _BATCH]
            for f, ant in zip(teil, self._frage([(rec, kand) for _, rec, kand, _ in teil])):
                if ant:                   # keine Antwort nie cachen
                    self.conn.execute("INSERT OR REPLACE INTO kandidat_cache (key, antwort) VALUES (?, ?)",
                                      (f[3], json.dumps(ant, ensure_ascii=False)))
                    self.conn.commit()
                    antworten[f[3]] = ant

        for k, rec, kand, key in faelle:
            ant = antworten.get(key) or {}
            wahl = str(ant.get("choice") or "").strip().upper()[:1]
            idx = string.ascii_uppercase.find(wahl) if wahl else -1
            if ant.get("shape") in ("line", "area") and 0 <= idx < len(kand):
                # Nur Punkte.  Eine Mauer oder Bahnlinie hat keinen (wie
                # Schritt 2), und der Mittelpunkt von Ostsee oder Java sagt
                # nichts ueber den Aufnahmeort.  Es bleibt beim Ort.
                form = "Linie" if ant["shape"] == "line" else "Fläche"
                rec.nachgefasst = f"Kandidat: {kand[idx]['label']} — {form}, kein Punkt"
                if self.debug:
                    print(f"  [kandidaten] {rec.title[:50]!r}: {kand[idx]['label']} gewählt, aber {form} — kein Punkt")
                continue
            if not (0 <= idx < len(kand)) or ant.get("certainty") != "high":
                if self.debug:
                    print(f"  [kandidaten] {rec.title[:50]!r}: keiner — Wahl {wahl or '–'}, "
                          f"Sicherheit {ant.get('certainty', '?')} ({ant.get('reason', '—')})")
                continue
            c = kand[idx]
            alt = geos[k]
            geos[k] = GeoResult(lat=c["lat"], lon=c["lon"], quality_score=4, fallback=False,
                                source="wikidata", match_name=c["label"] or rec.location,
                                ambiguous=alt.ambiguous,
                                debug_info=alt.debug_info + [
                                    f"Kandidat {c['label']!r} ({c['qid']}) bestaetigt: {ant.get('reason', '')}"])
            rec.nachgefasst = f"Kandidat: {c['label']} ({c['qid']})"
            self.gefunden += 1
            if self.debug:
                print(f"  [kandidaten] {rec.title[:50]!r}: {c['label']} ({c['qid']})")
