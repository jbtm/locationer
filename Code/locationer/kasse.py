"""Kostenkasse — was die Modellaufrufe tatsaechlich kosten.

Gebucht wird die `usage` jeder Antwort, nicht eine Schaetzung.  Eine Kasse
gehoert zu einem Auftrag und wird von allen parallelen Paketen geteilt
(darum mit Sperre).  Die Grenze wird nicht hier durchgesetzt, sondern in
auftrag.py nach jedem Paket — hier wird nur gezaehlt.

Preise in USD pro Million Tokens (Stand 2026-10-24, siehe description.md).
Ein unbekanntes Modell wird zum Opus-Preis gebucht: lieber zu teuer
gerechnet als eine Grenze, die nicht greift.
"""
import os
import threading

PREISE = {                      # (Eingabe, Ausgabe) USD / MTok
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-5-5":  (4.00, 20.00),
}
_TEUERSTER = max(PREISE.values(), key=lambda p: p[1])

# Franken pro Dollar.  Bewusst eher hoch: die Grenze soll frueher greifen,
# nicht spaeter.
USD_CHF = float(os.getenv("USD_CHF", "0.90"))

# Richtwert fuer die Schaetzung VOR dem Lauf, pro Zeile, die noch nicht im
# Cache ist: Haiku 1a+1b ≈ 0.20 USD/1000, Schritt 2 bis 0.50 USD/1000
# (gemessen 2026-10-24/25 auf DISCO und ZIN).  Oberer Rand, damit die
# Schaetzung nicht zu tief liegt.
USD_PRO_NEUE_ZEILE = float(os.getenv("USD_PRO_NEUE_ZEILE", "0.0007"))


def usd(model: str, usage) -> float:
    ein, aus = PREISE.get(model, _TEUERSTER)
    return (usage.input_tokens * ein + usage.output_tokens * aus) / 1e6


class Kasse:
    def __init__(self, usd_bisher: float = 0.0, nach_buchung=None):
        self._sperre = threading.Lock()
        self.usd = usd_bisher           # inkl. frueherer Laeufe desselben Auftrags
        self.tokens: dict[str, list[int]] = {}
        # Aufgerufen nach jeder Buchung (ausserhalb der Sperre) — auftrag.py
        # sichert damit den Stand auch mitten im Paket: stirbt der Prozess,
        # fehlt sonst, was die laufenden Pakete schon gekostet haben.
        self.nach_buchung = nach_buchung

    def buche(self, model: str, usage) -> None:
        if usage is None:
            return
        with self._sperre:
            self.usd += usd(model, usage)
            t = self.tokens.setdefault(model, [0, 0])
            t[0] += usage.input_tokens
            t[1] += usage.output_tokens
        if self.nach_buchung:
            self.nach_buchung()

    def buche_usd(self, betrag: float) -> None:
        with self._sperre:
            self.usd += betrag

    @property
    def chf(self) -> float:
        return self.usd * USD_CHF
