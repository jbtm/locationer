"""Arbeiter fuer den Server-Betrieb:  python -m locationer.worker

Holt Auftraege aus der Auftragsliste (register.py) und rechnet sie, einen
nach dem anderen, jeden mit mehreren parallelen Paketen (auftrag.py).  Laeuft
als eigener Dienst, getrennt von der Webseite: ein Neustart der Seite bricht
keinen Auftrag ab.

Nach einem Absturz oder Neustart wird ein Auftrag, der „läuft" war, wieder in
die Schlange gestellt und fortgesetzt — mit der vollen Pruefung aus
auftrag.py (Fingerabdruck der Eingabe, jede Teildatei Zeile fuer Zeile).
"""
import contextlib
import io
import json
import os
import shutil
import sys
import time
import traceback
from contextlib import closing
from datetime import datetime
from pathlib import Path

from . import VERSION, version_text
from . import register as R
from .auftrag import Auftrag, AuftragFehler, schaetzen
from .pipeline import _read, _spaltenbericht

WORKERS = int(os.getenv("LOCATIONER_WORKERS", "6"))
PAUSE_S = 3
# Voruebergehende Stoerungen (Netz, Anthropic ueberlastet, Zeitgrenze) halten
# einen Lauf ueber Nacht nicht an: nach einer Pause geht er von selbst weiter,
# hoechstens so oft hintereinander.  Danach bleibt er auf „fehler" stehen.
NEUVERSUCH_S = 300
NEUVERSUCHE = 6
_versuche: dict[int, int] = {}
_nicht_vor: dict[int, float] = {}


def _voruebergehend(e: Exception) -> bool:
    import anthropic
    import requests
    return isinstance(e, (TimeoutError, anthropic.APIConnectionError, anthropic.APITimeoutError,
                          anthropic.RateLimitError, anthropic.InternalServerError,
                          requests.ConnectionError, requests.Timeout))


def _log(a_dir: Path, text: str) -> None:
    zeile = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {text}"
    print(zeile, flush=True)
    with open(a_dir / "protokoll.log", "a", encoding="utf-8") as f:
        f.write(zeile + "\n")


def upload_pfad(auftrag_id: int) -> "Path | None":
    for p in (R.DATEN / "uploads").glob(f"{auftrag_id:05d}.*"):
        return p
    return None


def pruefen(conn, row) -> None:
    """Hochgeladene Datei → Auftrag anlegen, Spalten lesen, Kosten schaetzen."""
    aid = row["id"]
    a_dir = R.verzeichnis(aid)
    up = upload_pfad(aid)
    if a_dir.exists() and not (a_dir / "auftrag.json").exists() and not any(a_dir.glob("teile/p*.csv")):
        # Anlegen ist frueher gescheitert (nur die Kopie der Eingabe liegt da):
        # weg damit, sonst verweigert anlegen() das nicht leere Verzeichnis.
        shutil.rmtree(a_dir)
    if (a_dir / "auftrag.json").exists():
        a = Auftrag(a_dir)          # angelegt, aber die Schaetzung kam nicht durch: nur diese wiederholen
    else:
        if up is None:
            raise AuftragFehler("Hochgeladene Datei fehlt")
        bbox = tuple(json.loads(row["bbox"])) if row["bbox"] else None
        a = Auftrag.anlegen(a_dir, up, grenze_chf=row["grenze_chf"], bbox=bbox, name=row["name"])
    if up is not None:
        up.unlink()
    df = a.eingabe()
    puffer = io.StringIO()
    with contextlib.redirect_stdout(puffer):
        _spaltenbericht(df)
    s = schaetzen(df, a.m["extra_desc_cols"])
    _log(a_dir, f"geprüft: {s['zeilen']} Zeilen, {s['neu']} neu, Schätzung {s['chf']:.2f} CHF")
    werte = dict(zeilen=a.m["zeilen"], schaetzung=json.dumps(s), spalten=puffer.getvalue().strip())
    if R.holen(conn, aid)["zustand"] != "prüfen":       # inzwischen abgebrochen
        R.aendern(conn, aid, **werte)
        return
    if not row["starten"]:
        R.aendern(conn, aid, zustand="bereit", grund="", **werte)
    elif s["chf"] > row["grenze_chf"]:
        R.aendern(conn, aid, zustand="angehalten",
                  grund=f"Schätzung {s['chf']:.2f} CHF liegt über der Grenze von {row['grenze_chf']:.2f} CHF",
                  **werte)
    else:
        R.aendern(conn, aid, zustand="wartet", grund="", **werte)


def rechnen(conn, row) -> None:
    aid = row["id"]
    a_dir = R.verzeichnis(aid)
    a = Auftrag(a_dir)
    if abs(a.m["grenze_chf"] - row["grenze_chf"]) > 1e-9:
        a.setze(grenze_chf=row["grenze_chf"])      # Grenze wurde auf der Seite erhoeht
    if a.m.get("versionen") and a.m["versionen"][-1] != version_text():
        _log(a_dir, f"ACHTUNG: bisher gerechnet mit {a.m['versionen'][-1]}, weiter mit {version_text()}")
    R.aendern(conn, aid, zustand="läuft", grund="", soll="", version=VERSION,
              gestartet=row["gestartet"] or R.jetzt(), beendet=None)
    _log(a_dir, f"Locationer {version_text()} · Start: {WORKERS} Pakete parallel, Grenze {a.m['grenze_chf']:.2f} CHF, "
                f"bisher {a.kosten_chf():.2f} CHF")
    gebucht = [a.m["usd"]]

    def anhalten():
        with closing(R.verbinde()) as c:
            soll = R.holen(c, aid)["soll"]
            if soll == "anhalten":
                return "Von Hand angehalten"
            if soll == "abbrechen":
                return "Abgebrochen"
            return R.tagesgrenze_erreicht(c)

    def melden(f):
        with closing(R.verbinde()) as c:
            R.ausgabe_buchen(c, f["usd"] - gebucht[0])
            gebucht[0] = f["usd"]
            R.aendern(c, aid, erledigt=f["erledigt"], chf=f["chf"], hochrechnung_chf=f["hochrechnung_chf"])
        _log(a_dir, f"{f['erledigt']}/{f['zeilen']}  {f['chf']:.2f} CHF"
                    + (f"  Hochrechnung {f['hochrechnung_chf']:.2f} CHF" if f["hochrechnung_chf"] else ""))

    r = a.rechnen(workers=WORKERS, mode="human", ausgeben=False, anhalten=anhalten, melden=melden)
    R.ausgabe_buchen(conn, a.m["usd"] - gebucht[0])
    st = getattr(a, "statistik", {})
    f = a.fortschritt()
    werte = dict(erledigt=f["erledigt"], chf=f["chf"], hochrechnung_chf=f["hochrechnung_chf"], soll="")
    if r["zustand"] == "fertig":
        _karte(a_dir / "ausgabe.csv")
        _log(a_dir, f"fertig: {f['zeilen']} Zeilen, {f['chf']:.2f} CHF, "
                    f"Zuordnung prüfen: {len(st.get('verdacht', []))} Zeilen")
        R.aendern(conn, aid, zustand="fertig", grund="", beendet=R.jetzt(), **werte)
    elif R.holen(conn, aid)["soll"] == "abbrechen":
        if any(a.teile.glob("p*.csv")):
            a.zusammensetzen(teilweise=True)
        _log(a_dir, f"abgebrochen bei {f['erledigt']}/{f['zeilen']}")
        R.aendern(conn, aid, zustand="abgebrochen", grund="Von Hand abgebrochen", beendet=R.jetzt(), **werte)
    else:
        _log(a_dir, f"angehalten: {r['grund']}")
        R.aendern(conn, aid, zustand="angehalten", grund=r["grund"], **werte)


def _karte(csv: Path) -> None:
    from .map import main as map_main
    argv = sys.argv
    try:
        sys.argv = ["locationer.map", str(csv), "--no-open"]
        with contextlib.redirect_stdout(io.StringIO()):
            map_main()
    except Exception as e:
        print(f"Karte nicht erzeugt: {e}", flush=True)
    finally:
        sys.argv = argv


def schritt(conn) -> bool:
    """Eine Aufgabe erledigen.  → False, wenn nichts zu tun war."""
    row = conn.execute("SELECT * FROM auftrag WHERE zustand='prüfen' ORDER BY id LIMIT 1").fetchone()
    arbeit = pruefen
    if row is None:
        bereit = [r for r in conn.execute("SELECT * FROM auftrag WHERE zustand='wartet' ORDER BY id").fetchall()
                  if _nicht_vor.get(r["id"], 0) <= time.time()]
        row = bereit[0] if bereit else None
        arbeit = rechnen
    if row is None:
        return False
    aid = row["id"]
    try:
        arbeit(conn, row)
        _versuche.pop(aid, None)
    except Exception as e:
        ursache = e.__cause__ or e
        grund = f"{type(ursache).__name__}: {ursache}".splitlines()[0][:500]
        print(traceback.format_exc(), flush=True)
        a_dir = R.verzeichnis(aid)
        n = _versuche.get(aid, 0) + 1
        if arbeit is rechnen and _voruebergehend(ursache) and n <= NEUVERSUCHE:
            _versuche[aid] = n
            _nicht_vor[aid] = time.time() + NEUVERSUCH_S
            um = datetime.fromtimestamp(_nicht_vor[aid]).strftime("%H:%M")
            if a_dir.exists():
                _log(a_dir, f"Störung ({grund}) — neuer Versuch {n}/{NEUVERSUCHE} um {um}")
            R.aendern(conn, aid, zustand="wartet", grund=f"Störung, neuer Versuch {n}/{NEUVERSUCHE} um {um}: {grund}",
                      soll="")
        else:
            _versuche.pop(aid, None)
            if a_dir.exists():
                _log(a_dir, f"FEHLER: {grund}")
            R.aendern(conn, aid, zustand="fehler", grund=grund, soll="")
    return True


def main():
    conn = R.verbinde()
    (R.DATEN / "uploads").mkdir(parents=True, exist_ok=True)
    # Was beim letzten Mal mitten im Rechnen stand, wird fortgesetzt.
    for row in conn.execute("SELECT id FROM auftrag WHERE zustand='läuft'").fetchall():
        R.aendern(conn, row["id"], zustand="wartet", grund="nach Neustart fortgesetzt")
    print(f"Locationer-Arbeiter {version_text()} bereit · {R.DATEN.resolve()} · {WORKERS} parallel", flush=True)
    while True:
        if not schritt(conn):
            time.sleep(PAUSE_S)


if __name__ == "__main__":
    main()
