"""Auftrag — ein Lauf ueber eine Eingabedatei, in einem eigenen Verzeichnis.

Warum so: Bis 2026-10-25 setzte ein Lauf fort, indem er an eine vorhandene
Ausgabedatei gleichen Namens anhaengte.  Zwei-, dreimal war das eine fremde
Datei von einem frueheren Lauf, und das Ergebnis war gemischt.  Jetzt:

- Jeder Auftrag hat ein eigenes Verzeichnis.  Darin liegen eine Kopie der
  Eingabe, `auftrag.json` (Fingerabdruck der Eingabe, Einstellungen, Kosten)
  und die Teildateien `teile/p000000.csv` — eine pro Paket.
- Eine Teildatei entsteht unter einem vorlaeufigen Namen und bekommt ihren
  endgueltigen erst, wenn sie vollstaendig geschrieben ist.  Ein Abbruch
  hinterlaesst nie eine halbe Zeile.
- Fortgesetzt wird immer ein bestimmter Auftrag.  Vorher wird geprueft: die
  Eingabe hat noch denselben Fingerabdruck, und JEDE vorhandene Teildatei hat
  genau ihre Zeilenzahl und in den durchgereichten Eingabespalten Zeile fuer
  Zeile die Werte der Eingabe.  Passt etwas nicht: Abbruch mit Meldung, nichts
  wird ueberschrieben.
- Gerechnet werden genau die Pakete, deren Teildatei fehlt.  Parallele Pakete
  duerfen darum in beliebiger Reihenfolge fertig werden.
- `ausgabe.csv` entsteht erst am Schluss aus den Teildateien, in der
  Reihenfolge der Eingabe, mit Zaehlprobe.

Kostengrenze (kasse.py): nach jedem Paket wird geprueft, ob das Ausgegebene
die Grenze erreicht hat oder die Hochrechnung darueber liegt.  Dann haelt der
Auftrag an — er bricht nicht ab: Teildateien und Cache bleiben, Fortsetzen
geht erst mit einer hoeheren Grenze.
"""
import hashlib
import json
import os
import queue
import shutil
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import VERSION, version_text
from .kasse import USD_CHF, USD_PRO_NEUE_ZEILE, Kasse
from .pipeline import (_EXTRA_DESC_COLS, _UMBENENNEN, Umgebung, _has_unknown_location, _read,
                       _true_coords, paket)

VERSION = 1
# Hochrechnung erst, wenn genug Zeilen gerechnet sind — davor ist sie Zufall.
HOCHRECHNUNG_AB = int(os.getenv("HOCHRECHNUNG_AB", "1000"))


class AuftragFehler(Exception):
    """Der Auftrag ist nicht in einem Zustand, in dem weitergerechnet werden darf."""


def _sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _schreibe_atomar(pfad: Path, schreiben) -> None:
    """`schreiben(tmp_pfad)` erzeugt die Datei; erst danach traegt sie ihren Namen."""
    tmp = pfad.with_name(pfad.name + ".tmp")
    schreiben(tmp)
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, pfad)


def _zelle(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    s = str(v).strip()
    try:
        return repr(float(s))       # „1865" und „1865.0" sind dieselbe Zelle
    except ValueError:
        return s


def schaetzen(df: pd.DataFrame, extra_desc_cols=None) -> dict:
    """Wie viele Zeilen brauchen noch das Modell, und was kostet das etwa?

    Gezaehlt werden verschiedene Eingaben (gleiche Zeilen fragt der Lauf nur
    einmal), die noch nicht im Phase-1a-Cache sind.  Preis: oberer Richtwert
    pro Zeile (kasse.USD_PRO_NEUE_ZEILE).
    """
    from .cache import Cache
    from .input_normalizer import _clean, _map_columns, _raw_key
    from .pipeline import _CACHE_DEFAULT
    cols = list(extra_desc_cols if extra_desc_cols is not None else _EXTRA_DESC_COLS)
    cache = Cache(_CACHE_DEFAULT)
    try:
        keys = set()
        for a in range(0, len(df), 10000):        # stueckweise: 700000 dicts auf einmal kosten GB
            teil = df.iloc[a:a + 10000]
            for row in teil.where(pd.notna(teil), None).to_dict("records"):
                keys.add(_raw_key({k: _clean(v) for k, v in _map_columns(row, cols).items()}))
        neu = sum(1 for k in keys if cache.get_norm(k) is None)
    finally:
        cache.close()
    usd = neu * USD_PRO_NEUE_ZEILE
    return {"zeilen": len(df), "verschieden": len(keys), "neu": neu,
            "usd": round(usd, 2), "chf": round(usd * USD_CHF, 2)}


class Auftrag:
    def __init__(self, verzeichnis):
        self.dir = Path(verzeichnis)
        self.teile = self.dir / "teile"
        self.manifest_pfad = self.dir / "auftrag.json"
        if not self.manifest_pfad.exists():
            raise AuftragFehler(f"{self.dir} ist kein Auftrag (auftrag.json fehlt)")
        self.m = json.loads(self.manifest_pfad.read_text())
        if self.m.get("version") != VERSION:
            raise AuftragFehler(f"auftrag.json Version {self.m.get('version')}, erwartet {VERSION}")
        self._sperre = threading.Lock()

    # ── Anlegen ──────────────────────────────────────────────────────────────
    @classmethod
    def anlegen(cls, verzeichnis, eingabe, *, grenze_chf: float, bbox=None, extra_desc_cols=None,
                chunk: int = 20, limit: "int | None" = None, name: str = "") -> "Auftrag":
        d = Path(verzeichnis)
        if d.exists() and any(d.iterdir()):
            raise AuftragFehler(f"{d} existiert schon und ist nicht leer — einen neuen Auftrag "
                                f"nie in ein altes Verzeichnis legen")
        if not grenze_chf or grenze_chf <= 0:
            raise AuftragFehler("Ohne Kostengrenze wird kein Auftrag angelegt")
        eingabe = Path(eingabe)
        d.mkdir(parents=True, exist_ok=True)
        (d / "teile").mkdir()
        kopie = d / f"eingabe{eingabe.suffix.lower()}"
        shutil.copyfile(eingabe, kopie)
        df = _read(str(kopie))
        n = min(len(df), limit) if limit else len(df)
        kopf = df.head(5).where(pd.notna(df.head(5)), None).to_dict("records")
        m = {
            "version": VERSION,
            "name": name or eingabe.name,
            "eingabe": kopie.name,
            "sha256": _sha256(kopie),
            "spalten": [str(c) for c in df.columns],
            "zeilen": n,
            "limit": limit,
            "chunk": chunk,
            "bbox": list(bbox) if bbox else None,
            "extra_desc_cols": list(extra_desc_cols if extra_desc_cols is not None else _EXTRA_DESC_COLS),
            "has_true_coords": any(_true_coords(r) for r in kopf),
            "grenze_chf": float(grenze_chf),
            "usd": 0.0,
            "erstellt": datetime.now().isoformat(timespec="seconds"),
            "version": VERSION,             # mit welcher Version angelegt
            "versionen": [],                # jede Version, die daran gerechnet hat
            "zustand": "neu",
            "grund": "",
        }
        _schreibe_atomar(d / "auftrag.json", lambda p: p.write_text(json.dumps(m, indent=1, ensure_ascii=False)))
        return cls(d)

    # ── Zustand ──────────────────────────────────────────────────────────────
    def _speichern(self) -> None:
        _schreibe_atomar(self.manifest_pfad,
                         lambda p: p.write_text(json.dumps(self.m, indent=1, ensure_ascii=False)))

    def setze(self, **werte) -> None:
        with self._sperre:
            self.m.update(werte)
            self._speichern()

    @property
    def n_pakete(self) -> int:
        return -(-self.m["zeilen"] // self.m["chunk"])

    def _teil(self, k: int) -> Path:
        return self.teile / f"p{k:06d}.csv"

    def _bereich(self, k: int) -> "tuple[int, int]":
        a = k * self.m["chunk"]
        return a, min(a + self.m["chunk"], self.m["zeilen"])

    def eingabe(self) -> pd.DataFrame:
        pfad = self.dir / self.m["eingabe"]
        if _sha256(pfad) != self.m["sha256"]:
            raise AuftragFehler("Die Eingabe im Auftragsverzeichnis wurde veraendert (Fingerabdruck)")
        df = _read(str(pfad))
        if [str(c) for c in df.columns] != self.m["spalten"]:
            raise AuftragFehler("Die Spalten der Eingabe stimmen nicht mit auftrag.json ueberein")
        return df.head(self.m["zeilen"]).reset_index(drop=True)

    def _durchgereicht(self, df_spalten, aus_spalten) -> dict:
        """Eingabespalte → Spalte in der Ausgabe (siehe pipeline._als_importstruktur)."""
        verbraucht = set(_UMBENENNEN.values()) | {"Gitter"}
        paare = {}
        for c in df_spalten:
            if c in verbraucht:
                continue
            ziel = f"{c}_original" if f"{c}_original" in aus_spalten else c
            if ziel not in aus_spalten:
                raise AuftragFehler(f"Eingabespalte {c!r} fehlt in einer Teildatei")
            paare[c] = ziel
        if not paare:
            raise AuftragFehler("Keine durchgereichte Eingabespalte zum Vergleichen")
        return paare

    def _pruefe_teil(self, k: int, df: pd.DataFrame) -> pd.DataFrame:
        """Teildatei k lesen und Zeile fuer Zeile gegen die Eingabe pruefen."""
        try:
            t = pd.read_csv(self._teil(k), dtype=str, keep_default_na=False)
        except Exception as e:
            raise AuftragFehler(f"Teildatei {self._teil(k).name} nicht lesbar: {e}")
        a, b = self._bereich(k)
        if len(t) != b - a:
            raise AuftragFehler(f"Teildatei {self._teil(k).name}: {len(t)} Zeilen, erwartet {b - a}")
        ein = df.iloc[a:b].reset_index(drop=True)
        for c, ziel in self._durchgereicht(df.columns, t.columns).items():
            for i in range(len(t)):
                if _zelle(ein.at[i, c]) != _zelle(t.at[i, ziel]):
                    soll, ist = str(ein.at[i, c]), t.at[i, ziel]
                    p = next((j for j, (x, y) in enumerate(zip(soll, ist)) if x != y), min(len(soll), len(ist)))
                    raise AuftragFehler(
                        f"Teildatei {self._teil(k).name}, Zeile {a + i + 1}, Spalte {c!r} weicht "
                        f"ab Zeichen {p + 1} ab: …{ist[max(0, p - 15):p + 25]!r} statt "
                        f"…{soll[max(0, p - 15):p + 25]!r}")
        return t

    def pruefen(self, df: "pd.DataFrame | None" = None) -> "set[int]":
        """Fertige Pakete — nach voller Pruefung.  Wirft AuftragFehler, wenn etwas nicht passt."""
        df = self.eingabe() if df is None else df
        for rest in self.teile.glob("*.tmp"):
            rest.unlink()           # halb geschriebenes Paket eines Abbruchs: wird neu gerechnet
        fertig = set()
        for p in sorted(self.teile.glob("p*.csv")):
            try:
                k = int(p.stem[1:])
            except ValueError:
                raise AuftragFehler(f"Fremde Datei im Auftrag: {p.name}")
            if k >= self.n_pakete:
                raise AuftragFehler(f"Teildatei {p.name} liegt ausserhalb der Eingabe")
            self._pruefe_teil(k, df)
            fertig.add(k)
        return fertig

    def kosten_chf(self) -> float:
        return self.m["usd"] * USD_CHF

    # ── Rechnen ──────────────────────────────────────────────────────────────
    def rechnen(self, *, workers: int = 6, mode: str = "human", ausgeben: bool = True,
                anhalten=None, melden=None) -> dict:
        """Fehlende Pakete rechnen.  → {"zustand": "fertig" | "angehalten", "grund": …}

        anhalten(): optional, gibt einen Grund zurueck, wenn von aussen angehalten
                    werden soll (Knopf auf der Seite, Tagesgrenze).
        melden(d):  optional, bekommt nach jedem Paket den Fortschritt.
        """
        df = self.eingabe()
        fertig = self.pruefen(df)
        offen = [k for k in range(self.n_pakete) if k not in fertig]
        n, chunk = self.m["zeilen"], self.m["chunk"]
        gesichert = [time.time()]

        def sichern():
            # Hoechstens alle 5 s; die Kosten nach jedem Paket sichert ohnehin der Arbeiter-Thread.
            if time.time() - gesichert[0] >= 5:
                with self._sperre:
                    gesichert[0] = time.time()
                    self.m["usd"] = kasse.usd
                    self._speichern()

        kasse = Kasse(self.m["usd"], nach_buchung=sichern)
        grenze_usd = self.m["grenze_chf"] / USD_CHF
        if kasse.usd >= grenze_usd:
            return self._halt(f"Kostengrenze schon erreicht ({self.kosten_chf():.2f} von "
                              f"{self.m['grenze_chf']:.2f} CHF) — Grenze erhoehen")

        zustand = {"grund": "", "fehler": None}
        erledigt = {"zeilen": sum(self._bereich(k)[1] - self._bereich(k)[0] for k in fertig)}
        zaehler_alle: list[dict] = []
        verdacht: list[int] = []
        unbekannt = [0]
        stop = threading.Event()
        q: "queue.Queue[int]" = queue.Queue()
        for k in offen:
            q.put(k)
        # Festhalten, welche Version rechnet — wird ein angehaltener Auftrag
        # nach einem Update fortgesetzt, stehen danach beide hier.
        versionen = self.m.setdefault("versionen", [])
        if not versionen or versionen[-1] != version_text():
            versionen.append(version_text())
        self.setze(zustand="läuft", grund="")

        def pruefe_grenzen():
            """Nach jedem Paket.  Setzt stop + Grund, wenn angehalten werden muss."""
            if kasse.usd >= grenze_usd:
                zustand["grund"] = (f"Kostengrenze erreicht: {kasse.chf:.2f} von "
                                    f"{self.m['grenze_chf']:.2f} CHF")
            elif erledigt["zeilen"] >= min(HOCHRECHNUNG_AB, n) and erledigt["zeilen"] < n:
                hoch = kasse.usd / erledigt["zeilen"] * n
                if hoch > grenze_usd:
                    zustand["grund"] = (f"Hochrechnung {hoch * USD_CHF:.2f} CHF liegt über der Grenze "
                                        f"von {self.m['grenze_chf']:.2f} CHF (bisher {kasse.chf:.2f} CHF "
                                        f"für {erledigt['zeilen']} von {n} Zeilen)")
            if not zustand["grund"] and anhalten:
                zustand["grund"] = anhalten() or ""
            if zustand["grund"]:
                stop.set()

        def arbeiter():
            u = None
            try:
                u = Umgebung(kasse=kasse, bbox=tuple(self.m["bbox"]) if self.m["bbox"] else None,
                             extra_desc_cols=self.m["extra_desc_cols"], mode=mode,
                             fortschritt=(workers == 1 and ausgeben))
                while not stop.is_set():
                    try:
                        k = q.get_nowait()
                    except queue.Empty:
                        return
                    a, b = self._bereich(k)
                    out, unb, verd = paket(u, df.iloc[a:b], a, n, self.m["has_true_coords"], ausgeben)
                    if len(out) != b - a:
                        raise AuftragFehler(f"Paket {k}: {len(out)} Zeilen statt {b - a}")
                    with self._sperre:
                        # Erst die Kosten festhalten, dann die Teildatei: stirbt der
                        # Prozess dazwischen, wird das Paket (meist aus dem Cache)
                        # neu gerechnet — aber nie zu wenig Kosten verbucht.
                        self.m["usd"] = kasse.usd
                        self._speichern()
                    _schreibe_atomar(self._teil(k), lambda p: out.to_csv(p, index=False))
                    with self._sperre:
                        erledigt["zeilen"] += b - a
                        verdacht.extend(verd)
                        unbekannt[0] += unb
                        pruefe_grenzen()
                        if melden:
                            melden(self.fortschritt(kasse, erledigt["zeilen"]))
            except Exception as e:
                zustand["fehler"] = f"{type(e).__name__}: {e}"
                zustand["trace"] = traceback.format_exc()
                zustand["ursache"] = e
                stop.set()
            finally:
                if u is not None:
                    zaehler_alle.append(u.zaehler())
                    u.schliessen()

        threads = [threading.Thread(target=arbeiter, name=f"paket-{i}", daemon=True)
                   for i in range(max(1, min(workers, len(offen))))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.m["usd"] = kasse.usd
        self.statistik = {"verdacht": sorted(verdacht), "unbekannt": unbekannt[0],
                          **{k: sum(z[k] for z in zaehler_alle) for k in (zaehler_alle[0] if zaehler_alle else {})}}
        if zustand["fehler"]:
            self.setze(zustand="fehler", grund=zustand["fehler"])
            raise AuftragFehler(zustand["fehler"] + "\n" + zustand.get("trace", "")) from zustand["ursache"]
        if zustand["grund"]:
            return self._halt(zustand["grund"])
        fehlend = [k for k in range(self.n_pakete) if not self._teil(k).exists()]
        if fehlend:
            return self._halt(f"{len(fehlend)} Pakete fehlen noch")
        self.zusammensetzen(df)
        self.setze(zustand="fertig", grund="", fertig=datetime.now().isoformat(timespec="seconds"))
        return {"zustand": "fertig", "grund": ""}

    def _halt(self, grund: str) -> dict:
        self.setze(zustand="angehalten", grund=grund)
        return {"zustand": "angehalten", "grund": grund}

    def fortschritt(self, kasse: "Kasse | None" = None, zeilen: "int | None" = None) -> dict:
        usd = kasse.usd if kasse else self.m["usd"]
        if zeilen is None:
            zeilen = sum(self._bereich(int(p.stem[1:]))[1] - self._bereich(int(p.stem[1:]))[0]
                         for p in self.teile.glob("p*.csv"))
        n = self.m["zeilen"]
        hoch = usd / zeilen * n if zeilen >= min(HOCHRECHNUNG_AB, n) and zeilen else None
        return {"zeilen": n, "erledigt": zeilen, "usd": round(usd, 4), "chf": round(usd * USD_CHF, 2),
                "hochrechnung_chf": round(hoch * USD_CHF, 2) if hoch is not None else None,
                "grenze_chf": self.m["grenze_chf"]}

    # ── Zusammensetzen ───────────────────────────────────────────────────────
    def zusammensetzen(self, df: "pd.DataFrame | None" = None, teilweise: bool = False) -> Path:
        """Teildateien in Eingabe-Reihenfolge zu einer CSV — Paket fuer Paket
        geschrieben, damit 700000 Zeilen nicht zweimal im Speicher liegen.

        teilweise=True: nur die vorhandenen Pakete (Teilergebnis eines
        angehaltenen Auftrags) → `teilergebnis.csv`; sonst muessen alle da sein.
        """
        df = self.eingabe() if df is None else df
        ziel = self.dir / ("teilergebnis.csv" if teilweise else "ausgabe.csv")
        kopf = None
        anzahl = 0

        def schreiben(tmp):
            nonlocal kopf, anzahl
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                for k in range(self.n_pakete):
                    if not self._teil(k).exists():
                        if teilweise:
                            continue
                        raise AuftragFehler(f"Teildatei {self._teil(k).name} fehlt")
                    t = self._pruefe_teil(k, df)
                    if kopf is None:
                        kopf = list(t.columns)
                    elif list(t.columns) != kopf:
                        raise AuftragFehler(f"Teildatei {self._teil(k).name} hat andere Spalten als die erste")
                    t.to_csv(f, index=False, header=(anzahl == 0))
                    anzahl += len(t)

        _schreibe_atomar(ziel, schreiben)
        if not teilweise and anzahl != self.m["zeilen"]:
            ziel.unlink()
            raise AuftragFehler(f"Zusammengesetzt {anzahl} Zeilen, erwartet {self.m['zeilen']}")
        return ziel

    def unbekannt_zaehlen(self, df: pd.DataFrame) -> int:
        n = 0
        for a in range(0, len(df), 10000):
            teil = df.iloc[a:a + 10000]
            n += sum(_has_unknown_location(r) for r in teil.where(pd.notna(teil), None).to_dict("records"))
        return n
