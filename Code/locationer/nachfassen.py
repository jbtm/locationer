"""Schritt 2 — Fehlschlaege nachfassen.

Fuer Bilder, die nach dem Geostack ohne Koordinate sind:

1. Triage (Haiku 5.5): nennt der Text einen bestimmten Ort, den jemand mit
   gutem Wissen festnageln koennte?  „village à identifier" → nein, Ende.
   „Die Löwenköpfe, Bornholm" → ja.  Portraets und Gruppen gar nicht erst.
2. Identifikation (Opus 5.5), nur fuer „ja": offizieller Name, Wikidata-ID,
   Stadt, Land, Sicherheit.  Ausdruecklich KEINE Koordinaten.
3. Koordinate aus den Datenbanken — nie vom Modell:
   - mit Wikidata-ID: P625 dieser ID, geprueft gegen Land/Region (und den
     Abstand zur genannten Stadt).  Faellt sie durch, gibt es KEINE
     Koordinate und einen Vermerk — eine schwaechere Namenssuche darf eine
     verworfene ID nicht ersetzen („UK, Gibraltar" → sonst das Dorf
     Gibraltar in England, Pilot 2026-10-24).
   - ohne ID: Geostack mit Opus' Namen und Stadt.  Ein Rueckfall auf den
     Ortsmittelpunkt bleibt als Rueckfall markiert (Score 3, `Gitter` nach
     Ortsgroesse) — er ist ungenau und soll so aussehen.

Pilot 2026-10-24 (DISCO, 500 Bilder): 73 Nicht-Portraets ohne Koordinate →
18 bestimmbar → 12 mit Koordinate, davon 8 genau, 3 ungenau, 1 falsch
(Gibraltar, seither behoben).  Opus ~0.0045 USD pro Fall.

Abschaltbar mit NACHFASSEN=0 in .env.
"""
import hashlib
import json

from .geostack import _city_radius_km, _dist_km
from .input_normalizer import MODEL, _MAX_TOKENS, _MODEL_KW, _antworttext, _parse_array, _zuordnen
from .models import GeoResult, NormalizedRecord

MODEL_OPUS = "claude-opus-5-5"
_OPUS_KW = {"output_config": {"effort": "medium"}}
_TRIAGE_BATCH = 20
_OPUS_BATCH = 10

TRIAGE_PROMPT = """\
For each numbered entry from a historical photo archive return one JSON object in an array: {"n": <the entry number>, "ref": "<the first five words of the entry, copied exactly>", "locatable": "specific" | "area" | "none"}
- "specific": the input names a particular place, building, monument, natural feature or town that a well-informed person could pin to one spot on a map — even if you yourself do not know where it is.
- "area": only a country, region, coast, island or general area is named.
- "none": no place is named (genre scenes, people, objects, ships, 'à identifier').
Return ONLY the JSON array.\
"""

IDENT_PROMPT = """\
You identify the place shown in entries of a historical photo archive. Another system could not locate them.
For each numbered entry return one JSON object in an array:
{
  "n": <the entry number>,
  "ref": "<the first five words of the entry, copied exactly>",
  "official_name": "<the place's current official or best-known name, in the local language; '' if you cannot identify it>",
  "wikidata_qid": "<Wikidata item ID like 'Q12345' ONLY if you are certain it is the item for exactly this place; otherwise ''>",
  "city": "<town or municipality where it lies, current local name; '' if unknown>",
  "region": "<state/province/canton, current name; '' if unknown>",
  "country": "<current English country name>",
  "shape": "point" | "area" | "line",
  "confidence": "high" | "medium" | "low",
  "note": "<one short sentence: what it is>"
}
Rules:
- Never give coordinates.
- shape: "line" for things that stretch along a route (railway line, road, canal, river as a whole, border, pass road); "area" for valleys, regions, lakes, glaciers, gorges; else "point".
- 'high' only if the input clearly names this one place. If several places share the name, pick the one the input supports and say so in note, else 'low'.
- Do not invent: if you do not know the place, official_name = '' and confidence = 'low'.
- If the entry states a country, the place must lie there; if it does not, say so in note and use confidence 'low'.
Return ONLY the JSON array.\
"""


def _key(rec: NormalizedRecord) -> str:
    raw = "|".join([rec.input_title or rec.title, rec.description or "", rec.country or "", rec.region or ""])
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _eintraege(recs: list[NormalizedRecord]) -> list[str]:
    zeilen = []
    for n, rec in enumerate(recs):
        e = [f"{n + 1}."]
        for feld, wert in (("title", rec.input_title or rec.title), ("description", rec.description),
                           ("country", rec.country), ("region", rec.region), ("city", rec.city)):
            if wert:
                e.append(f'  {feld}="{str(wert)[:300]}"')
        zeilen.append("\n".join(e))
    return zeilen


class Nachfasser:
    def __init__(self, normalizer, geostack, cache, debug: bool = False):
        self.normalizer = normalizer      # fuer _create (Frist, Wiederholung)
        self.geostack = geostack
        self.geo_db = geostack.geo_db
        self.wikidata = geostack.wikidata
        self.conn = cache.conn
        self.debug = debug
        self.kandidaten = self.bestimmbar = self.gefunden = 0
        self.tokens = {"haiku": [0, 0], "opus": [0, 0]}
        self.conn.execute("""CREATE TABLE IF NOT EXISTS nachfass_cache (
            key TEXT PRIMARY KEY, triage TEXT, ident TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        self.conn.commit()

    # ── Cache ────────────────────────────────────────────────────────────────
    def _hole(self, key: str, feld: str):
        row = self.conn.execute(f"SELECT {feld} FROM nachfass_cache WHERE key=?", (key,)).fetchone()
        return None if row is None or row[0] is None else json.loads(row[0])

    def _lege_ab(self, key: str, feld: str, wert):
        self.conn.execute("INSERT OR IGNORE INTO nachfass_cache (key) VALUES (?)", (key,))
        self.conn.execute(f"UPDATE nachfass_cache SET {feld}=? WHERE key=?", (json.dumps(wert, ensure_ascii=False), key))
        self.conn.commit()

    # ── Modellaufrufe ────────────────────────────────────────────────────────
    def _frage(self, recs, system, model, kw, max_tokens, zaehler):
        texte = _eintraege(recs)
        r = self.normalizer._create(model=model, max_tokens=max_tokens, system=system,
                                    messages=[{"role": "user", "content": "Entries:\n" + "\n".join(texte)}], **kw)
        self.tokens[zaehler][0] += r.usage.input_tokens
        self.tokens[zaehler][1] += r.usage.output_tokens
        data = _parse_array(_antworttext(r))
        geordnet = _zuordnen(data, texte)
        if geordnet is None and len(recs) > 1:
            m = len(recs) // 2          # gleiche Absicherung wie Phase 1a
            return (self._frage(recs[:m], system, model, kw, max_tokens, zaehler)
                    + self._frage(recs[m:], system, model, kw, max_tokens, zaehler))
        data = geordnet or data or []
        return data[:len(recs)] + [{}] * (len(recs) - len(data))

    def _gestaffelt(self, recs, feld, batch, frage):
        """Aus dem Cache, was schon gefragt wurde; den Rest in Batches."""
        keys = [_key(r) for r in recs]
        out = [self._hole(k, feld) for k in keys]
        offen = {}
        for i, (k, o) in enumerate(zip(keys, out)):
            if o is None:
                offen.setdefault(k, i)      # gleiche Eingabe nur einmal fragen
        idx = list(offen.values())
        for s in range(0, len(idx), batch):
            teil = idx[s:s + batch]
            for i, ant in zip(teil, frage([recs[i] for i in teil])):
                if ant:                 # keine Antwort nie cachen — sonst nie wieder gefragt
                    self._lege_ab(keys[i], feld, ant)
                out[i] = ant
        # Gleiche Eingabe weiter hinten: dieselbe Antwort wie die erste.
        return [o if o is not None else (self._hole(k, feld) or out[offen[k]] or {})
                for k, o in zip(keys, out)]

    # ── Koordinate ───────────────────────────────────────────────────────────
    def _koordinate(self, rec: NormalizedRecord, x: dict) -> "tuple[GeoResult | None, str]":
        name = (x.get("official_name") or "").strip()
        if not name or x.get("confidence") not in ("high", "medium"):
            return None, "nicht identifiziert"
        if x.get("shape") == "line":
            # Eine Bahnlinie ist kein Punkt: „Ligne Pékin-Hankou" landete
            # sonst bei Tangshan (Testlauf 2026-10-24).
            return None, "Linie, kein Punkt"
        # Anker: Land und Region aus Phase 1a (Eingabe/Text) sind hart; nur
        # wo sie fehlen, gilt Opus' Angabe.
        anker = NormalizedRecord(
            title=rec.title, description="", country=rec.country or x.get("country", ""),
            region=rec.region or x.get("region", ""), city=x.get("city", ""),
            location=name, city_source="knowledge" if x.get("city") else "")
        region = self.geostack._region(anker)
        if rec.country and not region[0]:
            return None, f"Land {rec.country!r} unbekannt"
        qid = (x.get("wikidata_qid") or "").strip()
        if qid.startswith("Q") and self.wikidata:
            k, fehler = self.wikidata._hole_koordinaten([qid])
            if qid in k:
                lat, lon = k[qid]
                if not self.geostack._in_region(lat, lon, region):
                    return None, f"Wikidata {qid} liegt ausserhalb {anker.region or anker.country} — pruefen"
                if anker.city and self.geo_db:
                    # Die Stadt im Land des Punkts suchen, nicht nur im Anker-Land:
                    # „UK, Gibraltar" — sonst das Dorf Gibraltar in England.
                    laender = {region[0]} | {c for c, _ in (self.geo_db.lage(lat, lon) or set())}
                    orte = [self.geo_db.find_city(anker.city, c, None)[0] for c in laender if c]
                    orte = [o for o in orte if o is not None and o["country_code"] in laender]
                    ort = min(orte, key=lambda o: _dist_km(lat, lon, o["lat"], o["lon"])) if orte else None
                    if ort is not None:
                        d = _dist_km(lat, lon, ort["lat"], ort["lon"])
                        if d > max(_city_radius_km(ort) * 6, 15):
                            return None, f"Wikidata {qid} {d:.0f} km von {ort['name']} — pruefen"
                return GeoResult(lat=lat, lon=lon, quality_score=4, fallback=False,
                                 source="wikidata", match_name=name,
                                 debug_info=[f"nachgefasst: Opus {name!r}, Wikidata {qid}"]), f"Wikidata {qid}"
        g = self.geostack.geocode(anker)
        if g.lat is None:
            return None, "Name nicht gefunden"
        g.debug_info = [f"nachgefasst: Opus {name!r} → {g.source}"] + g.debug_info
        return g, f"{g.source}{' (Ortsmitte)' if g.fallback else ''}"

    # ── Einstieg ─────────────────────────────────────────────────────────────
    def nachfassen(self, records: list[NormalizedRecord], geos: list[GeoResult],
                   unbekannt: list[bool]) -> None:
        kand = [k for k, (rec, g, u) in enumerate(zip(records, geos, unbekannt))
                if g.lat is None and not u and rec.image_type not in ("portrait", "group")]
        if not kand:
            return
        self.kandidaten += len(kand)
        tri = self._gestaffelt(
            [records[k] for k in kand], "triage", _TRIAGE_BATCH,
            lambda rs: self._frage(rs, TRIAGE_PROMPT, MODEL, _MODEL_KW, _MAX_TOKENS, "haiku"))
        spez = [k for k, t in zip(kand, tri) if t.get("locatable") == "specific"]
        if not spez:
            return
        self.bestimmbar += len(spez)
        ident = self._gestaffelt(
            [records[k] for k in spez], "ident", _OPUS_BATCH,
            lambda rs: self._frage(rs, IDENT_PROMPT, MODEL_OPUS, _OPUS_KW, _MAX_TOKENS, "opus"))
        for k, x in zip(spez, ident):
            rec = records[k]
            g, wie = self._koordinate(rec, x)
            name = (x.get("official_name") or "").strip()
            rec.nachgefasst = f"Opus: {name or '—'} ({x.get('confidence', '?')}) → {wie}"
            if g is not None:
                geos[k] = g
                self.gefunden += 1
                if not rec.city and x.get("city"):
                    rec.city = x["city"]
            if self.debug:
                print(f"  [nachfassen] {rec.title[:50]!r}: {rec.nachgefasst}")

    def kosten_usd(self) -> float:
        h, o = self.tokens["haiku"], self.tokens["opus"]
        return h[0] / 1e6 * 0.10 + h[1] / 1e6 * 0.50 + o[0] / 1e6 * 4 + o[1] / 1e6 * 20
