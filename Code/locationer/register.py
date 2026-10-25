"""Auftragsliste fuer Server-Betrieb — Webseite/API schreiben, der Arbeiter liest.

Die Wahrheit ueber einen Auftrag (Teildateien, Kosten, Grenze) liegt in
seinem Verzeichnis (auftrag.py).  Hier steht nur, was Seite und Arbeiter
miteinander besprechen: Zustand, Wunsch („anhalten"), Fortschritt fuer die
Anzeige, und die Ausgaben pro Tag fuer die Tagesgrenze.

Zustaende:
  prüfen      hochgeladen; der Arbeiter liest Spalten und schaetzt die Kosten
  bereit      Schaetzung liegt vor, wartet auf „Starten"
  wartet      in der Schlange
  läuft       wird gerechnet
  angehalten  Grenze erreicht oder von Hand angehalten — fortsetzbar
  fertig      ausgabe.csv liegt vor
  abgebrochen von Hand beendet, Teilergebnis abrufbar
  fehler      etwas ist schiefgegangen (grund) — nach Behebung fortsetzbar
"""
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .kasse import USD_CHF

DATEN = Path(os.getenv("LOCATIONER_DATEN", "auftraege"))
TAGESGRENZE_CHF = float(os.getenv("LOCATIONER_TAGESGRENZE_CHF", "100"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auftrag (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    quelle           TEXT NOT NULL,            -- web | api
    erstellt         TEXT NOT NULL,
    zustand          TEXT NOT NULL,
    grund            TEXT NOT NULL DEFAULT '',
    soll             TEXT NOT NULL DEFAULT '', -- '' | anhalten | abbrechen
    starten          INTEGER NOT NULL DEFAULT 0, -- nach der Pruefung gleich in die Schlange
    grenze_chf       REAL NOT NULL,
    bbox             TEXT,
    zeilen           INTEGER,
    erledigt         INTEGER NOT NULL DEFAULT 0,
    chf              REAL NOT NULL DEFAULT 0,
    hochrechnung_chf REAL,
    schaetzung       TEXT,                     -- JSON aus auftrag.schaetzen
    spalten          TEXT,                     -- Spaltenbericht als Text
    aktualisiert     TEXT,
    version          TEXT                      -- Locationer-Version des letzten Laufs
);
CREATE TABLE IF NOT EXISTS ausgaben (
    tag  TEXT PRIMARY KEY,
    usd  REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS einstellung (
    name  TEXT PRIMARY KEY,
    wert  TEXT NOT NULL
);
"""


def verbinde() -> sqlite3.Connection:
    DATEN.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATEN / "register.sqlite", timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    spalten = {r["name"] for r in conn.execute("PRAGMA table_info(auftrag)")}
    if "version" not in spalten:          # Register von vor der Versionierung
        conn.execute("ALTER TABLE auftrag ADD COLUMN version TEXT")
    return conn


def verzeichnis(auftrag_id: int) -> Path:
    return DATEN / f"{auftrag_id:05d}"


def jetzt() -> str:
    return datetime.now().isoformat(timespec="seconds")


def aendern(conn, auftrag_id: int, **werte) -> None:
    werte["aktualisiert"] = jetzt()
    spalten = ", ".join(f"{k}=?" for k in werte)
    conn.execute(f"UPDATE auftrag SET {spalten} WHERE id=?", (*werte.values(), auftrag_id))
    conn.commit()


def holen(conn, auftrag_id: int) -> "sqlite3.Row | None":
    return conn.execute("SELECT * FROM auftrag WHERE id=?", (auftrag_id,)).fetchone()


def ausgabe_buchen(conn, usd: float) -> None:
    if usd <= 0:
        return
    conn.execute("INSERT INTO ausgaben (tag, usd) VALUES (?, ?) "
                 "ON CONFLICT(tag) DO UPDATE SET usd = usd + excluded.usd",
                 (date.today().isoformat(), usd))
    conn.commit()


def heute_chf(conn) -> float:
    r = conn.execute("SELECT usd FROM ausgaben WHERE tag=?", (date.today().isoformat(),)).fetchone()
    return (r["usd"] if r else 0.0) * USD_CHF


def tagesgrenze(conn) -> float:
    """Auf der Seite einstellbar (fuer DISCO hoeher); Vorgabe aus .env."""
    r = conn.execute("SELECT wert FROM einstellung WHERE name='tagesgrenze_chf'").fetchone()
    return float(r["wert"]) if r else TAGESGRENZE_CHF


def setze_tagesgrenze(conn, chf: float) -> None:
    conn.execute("INSERT INTO einstellung (name, wert) VALUES ('tagesgrenze_chf', ?) "
                 "ON CONFLICT(name) DO UPDATE SET wert = excluded.wert", (str(chf),))
    conn.commit()


def tagesgrenze_erreicht(conn) -> str:
    h, g = heute_chf(conn), tagesgrenze(conn)
    if h >= g:
        return (f"Tagesgrenze erreicht: heute {h:.2f} von {g:.2f} CHF "
                f"— morgen weiter oder Tagesgrenze erhöhen")
    return ""
