"""Locationer — Orte fuer historische Bilder finden.

Versionierung (seit 2026-10-25, Dec: „muss auf dem GUI stehen"):
  MAJOR  Ergebnisse aendern sich grundlegend (anderes Modell, neuer Pipeline-Schritt)
  MINOR  neue Funktion oder Korrektur, die Ergebnisse aendert
  PATCH  Korrektur ohne Einfluss auf Ergebnisse
Jedes Deployment bekommt eine neue Nummer und einen Git-Tag `vX.Y.Z`; die
Liste der Versionen steht in description.md („Versionen").  Jeder Auftrag
haelt fest, mit welcher Version er gerechnet wurde (auftrag.json).
"""
import subprocess
from pathlib import Path

VERSION = "6.3.1"
VERSION_DATUM = "2026-10-27"


def _commit() -> str:
    """Git-Commit des laufenden Codes: aus BUILD (beim Deployment geschrieben),
    sonst aus dem lokalen Repository; '' wenn beides fehlt."""
    hier = Path(__file__).resolve().parent
    build = hier / "BUILD"
    if build.exists():
        return build.read_text().strip()
    try:
        r = subprocess.run(["git", "-C", str(hier), "describe", "--always", "--dirty"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


COMMIT = _commit()


def version_text() -> str:
    return f"v{VERSION} · {VERSION_DATUM}" + (f" · {COMMIT}" if COMMIT else "")
