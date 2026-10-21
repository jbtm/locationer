"""
Rueckfluss aus Pictomap: von Menschen gesetzte Standorte als Referenz.

Ein Bild, das in Pictomap per Timeslide (Prio 1) oder von Hand (Prio 2)
verortet wurde, sagt etwas ueber sein MOTIV: steht ein neues Bild mit
demselben Motiv an, steht sein Fotograf mit grosser Wahrscheinlichkeit an
derselben Stelle.  Diese Koordinaten sind die besten, die es gibt — besser
als alles, was der Geostack aus Namen erraet.

Zwei Teile:

    python -m locationer.pictomap_ref sync     Pictomap lesen, lokal ablegen
    PictomapRef.match(record)          Stufe 0.5 im Geostack

Der Abgleich holt jedes Mal die vollstaendige Liste (rund 3000 Bilder, nur
wenige Felder, Sekunden) statt nur das seit dem letzten Mal Geaenderte.  So
verschwinden auch Referenzen, deren Koordinate in Pictomap geloescht wurde —
ein inkrementeller Abgleich ueber `date_updated` saehe das nicht.  Haiku
laeuft trotzdem nur fuer Bilder, deren Text neu ist oder sich geaendert hat.

ENTSCHEIDE (Dec, 2026-10-16)
────────────────────────────
**Nur Prio 1 und 2.**  Importierte (Prio 3/6) und virtuelle (Prio 5)
Koordinaten fliessen NICHT zurueck — das waeren Locationer-Ergebnisse, und der
Locationer laese seine eigenen Fehler wieder ein.

**Timeslide schlaegt manuell**, wie in Pictomap.  Timeslide ist der
Kamerastandort, und das neue Bild hat auch einen: die Leute stehen meist an
denselben Stellen.

**Nur als praezise markierte Punkte** (Entscheid 2026-10-17, Variante A):
Timeslide mit `localization_is_exact_by_ugc`, manuell mit
`localization_is_exact_by_manual_ugc`.

Zuerst galten alle manuellen Verortungen als Punkt.  Das war falsch: bei 1726
von 1831 steht im manuellen Feld exakt die Import-Koordinate des Bildes
(`localization_exact`) — Altlast, keine menschliche Verortung.  Darunter ein
Sammelpunkt in Zuerich (47.37289, 8.52903), auf dem 52 Bilder mit 20
verschiedenen Motiven liegen; er zog Wasserkirche, Ruden, Limmatquai um 1 km
weg.  Und 442 Treffer gaben bloss das alte Locationer-Ergebnis zurueck — genau
der Kreislauf, der vermieden werden soll.

Gemessen am ZIN-Bestand (eigene ID ausgeschlossen, Wahrheit = Timeslide oder
vom Import abweichende manuelle Koordinate, n = 60): Median-Fehler 169 m →
69 m, 34 besser, 9 schlechter.  „Alle manuellen" brachte 543 Treffer mehr,
aber keinen Gewinn an der Wahrheit; „echte manuelle (≠ Import)" brachte auf
ZIN keinen einzigen zusaetzlichen.  Wer von Hand „unpraezise" waehlt, meint
womoeglich wirklich „irgendwo in der Stadt".  Fehlt das Flag bei echten
Punktverortungen, gehoert das in der Pictomap-Oberflaeche geloest, nicht hier
erraten.

**Nur Motive mit Ortsangabe.**  Ohne `location` waere der Schluessel nur
„Stadt | Land" — dann bekaeme jedes Bild aus Chur den Standort eines
beliebigen anderen Churer Bilds.  Dasselbe gilt, wenn `location` gleich der
Stadt ist (Ortsansicht).

**Streuung > 1000 m: verworfen**, der normale Weg laeuft.  Erster Abgleich
2026-10-16: 6 von 1334 Motiven, z. B. „Wetterhorn | Grindelwald" (Fernmotiv,
drei Standorte), „Kirche | Biel" (zwei Kirchen).  Sonst Gitter = Streuung,
mindestens 100 m; die allermeisten Motive haben nur einen Punkt.
"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import GeoResult, NormalizedRecord

REF_PATH = os.getenv("PICTOMAP_REF_PATH", "cache/pictomap_ref.sqlite")

# Liegen die Punkte zu einem Motiv weiter auseinander, ist der Schluessel
# offenbar nicht eindeutig („Kirche | Chur" fuer mehrere Kirchen) — dann
# lieber den normalen Weg gehen als einen Mittelpunkt zwischen zwei Kirchen.
MAX_STREUUNG_M = 1000

# Auch ein einzelner bestaetigter Punkt ist fuer ein ANDERES Bild nicht exakt:
# derselbe Wert wie fuer einen Objekttreffer (GITTER_TREFFER_M in __main__).
MIN_GITTER_M = 100

QUELLEN = ("pictomap_ts", "pictomap_manual")   # Reihenfolge = Vorrang


def motiv_key(location: str, city: str, country: str) -> str:
    """Vergleichsschluessel: klein, ohne Akzente, Leerraum zusammengezogen."""
    def n(s: str) -> str:
        s = unicodedata.normalize("NFKD", (s or "").casefold())
        s = "".join(c for c in s if not unicodedata.combining(c))
        return " ".join(s.replace(".", " ").replace(",", " ").split())
    return f"{n(location)}|{n(city)}|{n(country)}"


def _dist_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_latlon(s) -> Optional[tuple[float, float]]:
    """Pictomap speichert UGC-Koordinaten als Text "lat,lon"."""
    try:
        lat, lon = (float(x) for x in str(s).split(","))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


class PictomapRef:
    def __init__(self, path: str = REF_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ref (
                image_id   INTEGER NOT NULL,
                source     TEXT    NOT NULL,   -- pictomap_ts | pictomap_manual
                lat        REAL    NOT NULL,
                lon        REAL    NOT NULL,
                text_hash  TEXT    NOT NULL,   -- Name/Beschreibung/Ort: Haiku nur bei Aenderung
                title      TEXT,
                location   TEXT,
                city       TEXT,
                country    TEXT,
                motiv      TEXT    NOT NULL,   -- motiv_key(location, city, country)
                synced_at  TEXT,
                PRIMARY KEY (image_id, source)
            );
            CREATE INDEX IF NOT EXISTS idx_ref_motiv ON ref(motiv);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM ref WHERE location != ''").fetchone()[0]

    def match(self, record: NormalizedRecord, ohne: frozenset = frozenset()) -> Optional[GeoResult]:
        """`ohne`: Bild-IDs, die nicht zaehlen — fuer Messungen, damit ein Bild
        nicht seine eigene Koordinate wiederfindet."""
        if not record.location:
            return None
        key = motiv_key(record.location, record.city, record.country)
        loc, city, _ = key.split("|")
        # „Bergün | Bergün" ist eine Ortsansicht, kein Motiv — derselbe Fall wie
        # eine fehlende Ortsangabe.  37 solche Schluessel beim ersten Abgleich.
        if loc == city:
            return None
        rows = self.conn.execute(
            "SELECT source, lat, lon, image_id FROM ref WHERE motiv = ?", (key,),
        ).fetchall()
        for quelle in QUELLEN:
            pts = [(lat, lon, iid) for src, lat, lon, iid in rows
                   if src == quelle and iid not in ohne]
            if not pts:
                continue
            # Median je Achse: ein einzelner Ausreisser verschiebt ihn nicht.
            lat = statistics.median(p[0] for p in pts)
            lon = statistics.median(p[1] for p in pts)
            streuung = statistics.median(_dist_m(lat, lon, p[0], p[1]) for p in pts)
            ids = ",".join(str(p[2]) for p in pts[:5]) + ("…" if len(pts) > 5 else "")
            if streuung > MAX_STREUUNG_M:
                # Nicht still auf die naechste Quelle ausweichen: streuen die
                # Timeslide-Punkte, ist das Motiv mehrdeutig, und eine einzelne
                # manuelle Verortung macht es nicht eindeutiger.
                return GeoResult(debug_info=[
                    f"pictomap: {quelle} n={len(pts)} streut {streuung:.0f} m "
                    f"> {MAX_STREUUNG_M} m — verworfen (Bilder {ids})"])
            return GeoResult(
                lat=lat, lon=lon, quality_score=5, fallback=False,
                source=quelle, match_name=record.location,
                gitter_m=max(MIN_GITTER_M, int(round(streuung))),
                debug_info=[f"pictomap: {quelle} n={len(pts)} "
                            f"streuung={streuung:.0f} m (Bilder {ids})"],
            )
        return None

    def close(self):
        self.conn.close()


# ── Abgleich ──────────────────────────────────────────────────────────────────

_FELDER = ("id,name,description,country,region,city,"
           "localization_exact_by_ugc,localization_is_exact_by_ugc,"
           "localization_manual_by_ugc,localization_is_exact_by_manual_ugc")


def _hole_pictomap() -> list[dict]:
    """Alle Bilder mit Timeslide- oder manueller Koordinate.  Nur lesend."""
    import requests

    base = os.getenv("PICTOMAP_BASE_URL", "https://pictomap.ch")
    token = os.getenv("PICTOMAP_API_TOKEN")
    if not token:
        sys.exit("PICTOMAP_API_TOKEN fehlt (.env)")
    flt = {"_or": [{"localization_exact_by_ugc": {"_nnull": True}},
                   {"localization_manual_by_ugc": {"_nnull": True}}]}
    rows, page = [], 1
    while True:
        r = requests.get(f"{base}/items/images",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"filter": json.dumps(flt), "fields": _FELDER,
                                 "limit": 1000, "page": page},
                         timeout=120)
        r.raise_for_status()
        teil = r.json()["data"]
        rows += teil
        if len(teil) < 1000:
            return rows
        page += 1


def _punkte(bild: dict) -> list[tuple[str, float, float]]:
    out = []
    # Prio 1: Timeslide nur, wenn als exakt markiert (Prio 4 bleibt draussen).
    ts = _parse_latlon(bild.get("localization_exact_by_ugc"))
    if ts and bild.get("localization_is_exact_by_ugc"):
        out.append(("pictomap_ts", *ts))
    # Prio 2: manuell, ebenfalls nur als exakt markiert — siehe Modulkopf.
    mn = _parse_latlon(bild.get("localization_manual_by_ugc"))
    if mn and bild.get("localization_is_exact_by_manual_ugc"):
        out.append(("pictomap_manual", *mn))
    return out


def _text_hash(bild: dict) -> str:
    t = "|".join(str(bild.get(k) or "") for k in ("name", "description", "country", "region", "city"))
    return hashlib.sha256(t.encode()).hexdigest()[:20]


def sync(ref: PictomapRef, dry_run: bool = False) -> None:
    from .cache import Cache
    from .input_normalizer import InputNormalizer

    bilder = _hole_pictomap()
    print(f"Pictomap: {len(bilder)} Bilder mit Timeslide- oder manueller Koordinate")

    soll: dict[tuple[int, str], tuple[float, float, dict]] = {}
    for b in bilder:
        for quelle, lat, lon in _punkte(b):
            soll[(b["id"], quelle)] = (lat, lon, b)

    ist = {(iid, src): (lat, lon, th, loc, city, country, title)
           for iid, src, lat, lon, th, loc, city, country, title in ref.conn.execute(
               "SELECT image_id, source, lat, lon, text_hash, location, city, country, title FROM ref")}

    weg = [k for k in ist if k not in soll]
    # Haiku nur, wo sich der TEXT geaendert hat; eine verschobene Koordinate
    # braucht keine neue Normalisierung.  Ein Bild mit beiden Quellen wird nur
    # einmal normalisiert.
    neu_text: dict[int, dict] = {}
    for (iid, src), (_, _, b) in soll.items():
        alt = ist.get((iid, src))
        if alt is None or alt[2] != _text_hash(b):
            neu_text[iid] = b

    n_ts = sum(1 for k in soll if k[1] == "pictomap_ts")
    print(f"  Punkte: {len(soll)}  (Timeslide {n_ts}, manuell {len(soll) - n_ts})")
    print(f"  zu normalisieren: {len(neu_text)} Bilder  ·  zu entfernen: {len(weg)}")
    if dry_run:
        return

    norm: dict[int, NormalizedRecord] = {}
    if neu_text:
        cache = Cache(os.getenv("CACHE_PATH", "cache/locationer.sqlite"))
        normalizer = InputNormalizer(cache)
        liste = list(neu_text.values())
        # Dieselben Spaltennamen, die auch eine Importdatei haette — damit
        # COLUMN_MAP sie erkennt und der norm_cache greift, wo das Bild schon
        # einmal durch den Locationer lief.
        rows = [{"name": b.get("name"), "description": b.get("description"),
                 "country": b.get("country"), "region": b.get("region"),
                 "city": b.get("city")} for b in liste]
        # Haiku liefert gelegentlich kaputtes JSON, und normalize_batch bricht
        # dann ab.  Statt den ganzen Abgleich zu verlieren: den Stapel teilen,
        # bis der Fehler auf einem Bild sitzt, und nur dieses auslassen.  Es
        # fehlt dann in der Referenz und kommt beim naechsten Abgleich wieder.
        # Fertige Stapel liegen schon im norm_cache und kosten nichts mehr.
        ausgelassen: list[int] = []

        def normalisiere(teil_b: list[dict], teil_r: list[dict]) -> None:
            try:
                for b, rec in zip(teil_b, normalizer.normalize_batch(teil_r)):
                    norm[b["id"]] = rec
            except Exception as e:
                if len(teil_r) == 1:
                    ausgelassen.append(teil_b[0]["id"])
                    print(f"\n  Bild {teil_b[0]['id']} ausgelassen: {type(e).__name__}")
                    return
                step = 20 if len(teil_r) > 20 else 1
                for s in range(0, len(teil_r), step):
                    normalisiere(teil_b[s:s + step], teil_r[s:s + step])

        for start in range(0, len(rows), 200):
            normalisiere(liste[start:start + 200], rows[start:start + 200])
        print(f"  Haiku-Aufrufe: {normalizer.haiku_count}"
              + (f"  ·  ausgelassen: {len(ausgelassen)}" if ausgelassen else ""))
        cache.close()

    jetzt = datetime.now().isoformat(timespec="seconds")
    with ref.conn:
        for k in weg:
            ref.conn.execute("DELETE FROM ref WHERE image_id=? AND source=?", k)
        for (iid, src), (lat, lon, b) in soll.items():
            if iid in norm:
                r = norm[iid]
                title, loc, city, country = r.title, r.location, r.city, r.country
            elif iid in neu_text:
                continue                  # Normalisierung fehlgeschlagen
            else:
                _, _, _, loc, city, country, title = ist[(iid, src)]
            ref.conn.execute(
                "INSERT OR REPLACE INTO ref VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (iid, src, lat, lon, _text_hash(b), title, loc or "", city or "",
                 country or "", motiv_key(loc, city, country), jetzt))
        ref.conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)", (jetzt,))

    mit_ort = ref.count()
    motive = ref.conn.execute(
        "SELECT COUNT(DISTINCT motiv) FROM ref WHERE location != ''").fetchone()[0]
    print(f"  gespeichert: {mit_ort} Punkte mit Ortsangabe auf {motive} Motive  ·  {REF_PATH}")


def main():
    from dotenv import load_dotenv
    load_dotenv(".env")
    load_dotenv(".env.local")

    ap = argparse.ArgumentParser(description="Rueckfluss Pictomap → Locationer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync", help="Timeslide- und manuelle Koordinaten aus Pictomap holen")
    p.add_argument("--dry-run", action="store_true", help="nur zaehlen, nichts normalisieren")
    sub.add_parser("stats", help="Inhalt der lokalen Referenztabelle")
    args = ap.parse_args()

    ref = PictomapRef(REF_PATH)
    if args.cmd == "sync":
        sync(ref, dry_run=args.dry_run)
    else:
        last = ref.conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
        print(f"letzter Abgleich: {last[0] if last else '—'}")
        for src, n in ref.conn.execute("SELECT source, COUNT(*) FROM ref GROUP BY 1"):
            print(f"  {src:<16} {n}")
        print(f"  mit Ortsangabe:  {ref.count()}")
        for motiv, n in ref.conn.execute(
                "SELECT motiv, COUNT(*) n FROM ref WHERE location != '' "
                "GROUP BY motiv HAVING n > 1 ORDER BY n DESC LIMIT 10"):
            print(f"  {n:4d}  {motiv}")
    ref.close()


if __name__ == "__main__":
    main()
