"""Webseite und API fuer den Locationer — tools.cronica.ch/locationer

    venv/bin/gunicorn --workers 1 --threads 4 --bind 127.0.0.1:5006 locationer.web:app

Die Seite rechnet nichts selbst (ausser /api/orte, siehe unten): sie legt
Auftraege an und zeigt sie; gerechnet wird im Arbeiter (worker.py).  So
bricht ein Neustart der Seite keinen Auftrag ab.

Anmeldung: Seite mit Passwort (LOCATIONER_PASSWORT), API mit Schluessel im
Kopf `Authorization: Bearer <LOCATIONER_API_TOKEN>`.  Ist kein Passwort
gesetzt, verweigert die Seite den Dienst — ausser LOCATIONER_OFFEN=1 (nur lokal).

/loc/ (tools.cronica.ch/loc): nur die Einzelabfrage, grosse Karte.  Zwei
Zugaenge mit eigenem Cookie: LOC_PASSWORT (mit Logik) und LOC_GAST_PASSWORT
(ohne Logik, alle Gaeste zusammen hoechstens LOC_GAST_TAG_CHF pro Tag).

Relative Pfade sind Pflicht (wie bei LayerCurator): die App haengt unter
`/locationer/`; jeder fetch() im JavaScript ist relativ.
"""
import hashlib
import hmac
import io
import json
import os
import threading
import time
from contextlib import closing, redirect_stdout
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from flask import (Flask, Response, abort, jsonify, redirect, render_template_string, request,
                   send_file, session, url_for)
from itsdangerous import BadSignature, URLSafeTimedSerializer

load_dotenv(".env")

from . import VERSION, VERSION_DATUM, COMMIT, version_text
from . import register as R
from .auftrag import Auftrag, AuftragFehler
from .kasse import USD_CHF, Kasse
from .pipeline import _COLLECTION_BBOX, _parse_collection_bbox

PASSWORT = os.getenv("LOCATIONER_PASSWORT", "")
API_TOKEN = os.getenv("LOCATIONER_API_TOKEN", "")
OFFEN = os.getenv("LOCATIONER_OFFEN", "") == "1"
GRENZE_VORGABE = float(os.getenv("LOCATIONER_GRENZE_CHF", "20"))
LOC_PASSWORT = os.getenv("LOC_PASSWORT", "")
LOC_GAST_PASSWORT = os.getenv("LOC_GAST_PASSWORT", "")
LOC_GAST_TAG_CHF = float(os.getenv("LOC_GAST_TAG_CHF", "1"))
ORTE_MAX = 50
ENDUNGEN = {".csv", ".xlsx", ".xls"}

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(32)
app.config["MAX_CONTENT_LENGTH"] = 400 * 1024 * 1024      # DISCO-Liste: 190 MB
if not OFFEN:
    # Nur ueber https; SameSite=Lax haelt fremde Seiten von POSTs mit unserem Cookie ab.
    app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE="Lax")


class Praefix:
    """nginx setzt `X-Forwarded-Prefix: /locationer`; daraus wird SCRIPT_NAME,
    damit url_for() und Umleitungen unter dem Unterpfad stimmen (LayerCurator)."""
    def __init__(self, wsgi):
        self.wsgi = wsgi

    def __call__(self, environ, start_response):
        p = (environ.get("HTTP_X_FORWARDED_PREFIX") or "").rstrip("/")
        if p:
            environ["SCRIPT_NAME"] = p
        return self.wsgi(environ, start_response)


app.wsgi_app = Praefix(app.wsgi_app)


# ── Anmeldung ─────────────────────────────────────────────────────────────────
def _api_schluessel_ok() -> bool:
    kopf = request.headers.get("Authorization", "")
    return bool(API_TOKEN) and kopf.startswith("Bearer ") and \
        hmac.compare_digest(kopf[7:].strip().encode(), API_TOKEN.encode())


@app.before_request
def wache():
    if request.endpoint in ("login", "static"):
        return None
    if request.path == "/loc" or request.path.startswith("/loc/"):
        return None             # eigene Wache mit eigenem Cookie: _loc_rolle()
    if not PASSWORT and not OFFEN:
        return Response("LOCATIONER_PASSWORT ist nicht gesetzt — Dienst gesperrt.", 503)
    if OFFEN and not PASSWORT:
        return None
    if session.get("auth") is True or _api_schluessel_ok():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"fehler": "nicht angemeldet"}), 401
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    fehler = None
    if request.method == "POST":
        if PASSWORT and hmac.compare_digest(request.form.get("passwort", "").encode(), PASSWORT.encode()):
            session["auth"] = True
            return redirect(url_for("start"))
        fehler = "Falsches Passwort."
    return render_template_string(SEITE_LOGIN, fehler=fehler, version=version_text())


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Hilfen ────────────────────────────────────────────────────────────────────
def _db():
    return closing(R.verbinde())


def _zahl(wert, name: str) -> float:
    try:
        z = float(str(wert).replace("'", "").replace(",", "."))
    except (TypeError, ValueError):
        abort(Response(json.dumps({"fehler": f"{name} fehlt oder ist keine Zahl"}), 400,
                       mimetype="application/json"))
    if z <= 0:
        abort(Response(json.dumps({"fehler": f"{name} muss grösser als 0 sein"}), 400,
                       mimetype="application/json"))
    return z


def _fehler(text: str, code: int = 400):
    return jsonify({"fehler": text}), code


def _mit_zone(x):
    """Register-Zeiten sind Ortszeit des Servers (UTC) ohne Zone; mit Zone liest
    der Browser sie richtig und zeigt sie in der Zeit des Betrachters."""
    return datetime.fromisoformat(x).astimezone().isoformat(timespec="seconds") if x else x


def _als_dict(row) -> dict:
    d = dict(row)
    for k in ("erstellt", "gestartet", "beendet", "aktualisiert"):
        d[k] = _mit_zone(d.get(k))
    d["schaetzung"] = json.loads(d["schaetzung"]) if d.get("schaetzung") else None
    d["bbox"] = json.loads(d["bbox"]) if d.get("bbox") else None
    a_dir = R.verzeichnis(d["id"])
    d["ergebnis"] = (a_dir / "ausgabe.csv").exists()
    d["teilergebnis"] = any((a_dir / "teile").glob("p*.csv")) if (a_dir / "teile").exists() else False
    d["karte"] = (a_dir / "ausgabe_map.html").exists()
    return d


def _auftrag_oder_404(conn, aid: int):
    row = R.holen(conn, aid)
    if row is None:
        abort(404)
    return row


# ── Seite ─────────────────────────────────────────────────────────────────────
@app.route("/")
def start():
    vorgabe = ",".join(f"{x:g}" for x in _COLLECTION_BBOX) if _COLLECTION_BBOX else ""
    return render_template_string(SEITE_START, grenze=GRENZE_VORGABE, bbox=vorgabe, version=version_text())


@app.route("/auftrag/<int:aid>/karte")
def karte(aid):
    p = R.verzeichnis(aid) / "ausgabe_map.html"
    if not p.exists():
        abort(404)
    return send_file(p.resolve(), mimetype="text/html")


# ── API: Auftraege ────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    with _db() as conn:
        return jsonify({"heute_chf": round(R.heute_chf(conn), 2), "tagesgrenze_chf": R.tagesgrenze(conn),
                        "usd_chf": USD_CHF, "version": VERSION, "version_datum": VERSION_DATUM,
                        "commit": COMMIT})


@app.route("/api/tagesgrenze", methods=["POST"])
def api_tagesgrenze():
    daten = request.get_json(silent=True) or request.form
    chf = _zahl(daten.get("chf"), "chf")
    with _db() as conn:
        R.setze_tagesgrenze(conn, chf)
        return jsonify({"tagesgrenze_chf": R.tagesgrenze(conn)})


@app.route("/api/auftraege")
def api_liste():
    with _db() as conn:
        rows = conn.execute("SELECT * FROM auftrag ORDER BY id DESC LIMIT 200").fetchall()
        return jsonify([_als_dict(r) for r in rows])


@app.route("/api/auftraege", methods=["POST"])
def api_anlegen():
    """CSV/XLSX als Datei (Feld `datei`) oder JSON {"zeilen": [...]}.

    Pflicht: grenze_chf.  Optional: name, bbox ("min_lat,max_lat,min_lon,max_lon";
    leer = Vorgabe aus .env, "-" = ohne), starten (1 = nach der Pruefung gleich
    in die Schlange, sofern die Schaetzung unter der Grenze liegt).
    """
    if request.is_json:
        daten = request.get_json(silent=True) or {}
        zeilen = daten.get("zeilen")
        if not isinstance(zeilen, list) or not zeilen or not all(isinstance(z, dict) for z in zeilen):
            return _fehler("zeilen: Liste von Objekten erwartet")
        inhalt = pd.DataFrame(zeilen).to_csv(index=False).encode("utf-8")
        endung, name = ".csv", str(daten.get("name") or "API-Auftrag")
        dateiname = "JSON-Zeilen"
        felder = daten
    else:
        datei = request.files.get("datei")
        if datei is None or not datei.filename:
            return _fehler("Keine Datei")
        endung = Path(datei.filename).suffix.lower()
        if endung not in ENDUNGEN:
            return _fehler(f"Nur {', '.join(sorted(ENDUNGEN))}")
        inhalt = datei.read()
        dateiname = Path(datei.filename).name
        name = request.form.get("name") or dateiname
        felder = request.form
    grenze = _zahl(felder.get("grenze_chf"), "grenze_chf")
    roh = str(felder.get("bbox") or "").strip()
    if roh == "-":
        bbox = None
    elif roh:
        bbox = _parse_collection_bbox(roh)
        if bbox is None:
            return _fehler("bbox: vier Zahlen min_lat,max_lat,min_lon,max_lon")
    else:
        bbox = _COLLECTION_BBOX
    starten = str(felder.get("starten", "")).lower() in ("1", "true", "ja", "on")

    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO auftrag (name, datei, quelle, erstellt, zustand, grenze_chf, bbox, starten, aktualisiert) "
            "VALUES (?, ?, ?, ?, 'prüfen', ?, ?, ?, ?)",
            (name[:200], dateiname[:200], "json" if request.is_json else ("api" if _api_schluessel_ok() else "web"),
             R.jetzt(), grenze, json.dumps(list(bbox)) if bbox else None, int(starten), R.jetzt()))
        aid = cur.lastrowid
        up = R.DATEN / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        tmp = up / f"{aid:05d}{endung}.tmp"
        tmp.write_bytes(inhalt)
        os.replace(tmp, up / f"{aid:05d}{endung}")
        conn.commit()
        return jsonify(_als_dict(R.holen(conn, aid))), 201


@app.route("/api/auftraege/<int:aid>")
def api_auftrag(aid):
    with _db() as conn:
        d = _als_dict(_auftrag_oder_404(conn, aid))
    log = R.verzeichnis(aid) / "protokoll.log"
    d["protokoll"] = log.read_text(encoding="utf-8").splitlines()[-40:] if log.exists() else []
    d["zone"] = datetime.now().astimezone().strftime("%z")      # fuer die Zeiten im Protokoll
    return jsonify(d)


@app.route("/api/auftraege/<int:aid>/starten", methods=["POST"])
def api_starten(aid):
    daten = request.get_json(silent=True) or request.form
    with _db() as conn:
        row = _auftrag_oder_404(conn, aid)
        if row["zustand"] not in ("bereit", "angehalten") or row["erledigt"]:
            return _fehler(f"Zustand {row['zustand']}: Starten geht nur nach der Prüfung", 409)
        grenze = (_zahl(daten.get("grenze_chf"), "grenze_chf") if daten.get("grenze_chf") not in (None, "")
                  else row["grenze_chf"])
        s = json.loads(row["schaetzung"] or "null")
        if s is None:
            return _fehler("Noch keine Schätzung", 409)
        if s["chf"] > grenze:
            return _fehler(f"Schätzung {s['chf']:.2f} CHF liegt über der Grenze {grenze:.2f} CHF", 409)
        R.aendern(conn, aid, zustand="wartet", grund="", soll="", grenze_chf=grenze)
        return jsonify(_als_dict(R.holen(conn, aid)))


@app.route("/api/auftraege/<int:aid>/weiter", methods=["POST"])
def api_weiter(aid):
    """Angehaltenen Auftrag fortsetzen.  Bei Kostengrenze: nur mit hoeherer Grenze."""
    daten = request.get_json(silent=True) or request.form
    with _db() as conn:
        row = _auftrag_oder_404(conn, aid)
        if row["zustand"] not in ("angehalten", "fehler", "abgebrochen"):
            return _fehler(f"Zustand {row['zustand']}: nichts fortzusetzen", 409)
        grenze = (_zahl(daten.get("grenze_chf"), "grenze_chf") if daten.get("grenze_chf") not in (None, "")
                  else row["grenze_chf"])
        if grenze <= row["chf"]:
            return _fehler(f"Die Grenze muss über den bisherigen Kosten von {row['chf']:.2f} CHF liegen", 409)
        if row["hochrechnung_chf"] and grenze < row["hochrechnung_chf"] and \
                not str(daten.get("trotzdem", "")).lower() in ("1", "true", "ja", "on"):
            return _fehler(f"Die Hochrechnung liegt bei {row['hochrechnung_chf']:.2f} CHF — mit dieser "
                           f"Grenze hält der Auftrag gleich wieder an", 409)
        if not row["schaetzung"]:
            # Die Pruefung war fehlgeschlagen: noch einmal pruefen statt rechnen.
            R.aendern(conn, aid, zustand="prüfen", grund="", soll="", grenze_chf=grenze)
            return jsonify(_als_dict(R.holen(conn, aid)))
        R.aendern(conn, aid, zustand="wartet", grund="", soll="", grenze_chf=grenze)
        return jsonify(_als_dict(R.holen(conn, aid)))


@app.route("/api/auftraege/<int:aid>/anhalten", methods=["POST"])
def api_anhalten(aid):
    with _db() as conn:
        row = _auftrag_oder_404(conn, aid)
        if row["zustand"] == "läuft":
            R.aendern(conn, aid, soll="anhalten")
        elif row["zustand"] in ("wartet", "bereit"):
            R.aendern(conn, aid, zustand="angehalten", grund="Von Hand angehalten")
        else:
            return _fehler(f"Zustand {row['zustand']}: nichts anzuhalten", 409)
        return jsonify(_als_dict(R.holen(conn, aid)))


@app.route("/api/auftraege/<int:aid>/abbrechen", methods=["POST"])
def api_abbrechen(aid):
    with _db() as conn:
        row = _auftrag_oder_404(conn, aid)
        if row["zustand"] == "läuft":
            R.aendern(conn, aid, soll="abbrechen")
        elif row["zustand"] in ("wartet", "bereit", "angehalten", "fehler", "prüfen"):
            R.aendern(conn, aid, zustand="abgebrochen", grund="Von Hand abgebrochen", soll="", beendet=R.jetzt())
        else:
            return _fehler(f"Zustand {row['zustand']}: nichts abzubrechen", 409)
        return jsonify(_als_dict(R.holen(conn, aid)))


@app.route("/api/auftraege/<int:aid>/ergebnis")
def api_ergebnis(aid):
    """?format=json für JSON; ?teilweise=1 für die bisher fertigen Pakete."""
    with _db() as conn:
        row = _auftrag_oder_404(conn, aid)
    a_dir = R.verzeichnis(aid)
    if request.args.get("teilweise") == "1":
        if row["zustand"] == "läuft":
            return _fehler("Teilergebnis erst, wenn der Auftrag steht (anhalten)", 409)
        try:
            pfad = Auftrag(a_dir).zusammensetzen(teilweise=True)
        except AuftragFehler as e:
            return _fehler(str(e), 409)
    else:
        pfad = a_dir / "ausgabe.csv"
        if row["zustand"] != "fertig" or not pfad.exists():
            return _fehler(f"Zustand {row['zustand']}: noch kein Ergebnis", 409)
    if request.args.get("format") == "json":
        df = pd.read_csv(pfad, dtype=str, keep_default_na=False)
        return Response(df.to_json(orient="records", force_ascii=False), mimetype="application/json")
    # Wie die Kommandozeile: <Name>_loc-<Datum>.csv.  Fertig: Tag des Abschlusses, damit
    # jeder Download gleich heisst; Teilergebnis: heute, es ist ein Zwischenstand.
    teil = pfad.name == "teilergebnis.csv"
    tag = date.today().isoformat() if teil else (row["beendet"] or R.jetzt())[:10]
    # Nach der Datei, nicht nach dem Auftragsnamen (Dec 2026-10-26); JSON-Auftraege haben keine.
    name = Path(row["datei"] or row["name"]).stem + ("_teil" if teil else "") + f"_loc-{tag}.csv"
    return send_file(pfad.resolve(), mimetype="text/csv", as_attachment=True, download_name=name)


# ── API: sofort (fuer einzelne Bilder aus Pictomap) ──────────────────────────
_SOFORT = threading.Lock()      # einer nach dem anderen — der Server hat 4 GB


@app.route("/api/orte", methods=["POST"])
def api_orte():
    """Bis 50 Zeilen sofort: {"zeilen": [{"title": …, "description": …}, …], "bbox": …}

    Mit "logik": true kommt das Debug-Protokoll mit (Antwort von Haiku, jede
    Station im Geostack, Kandidaten, Nachfassen) — fuer den Knopf „Logik" in
    der Einzelabfrage.  Dieselbe Pipeline; der Debug-Modus druckt nur mehr.
    """
    daten = request.get_json(silent=True) or {}
    zeilen = daten.get("zeilen")
    if not isinstance(zeilen, list) or not zeilen or not all(isinstance(z, dict) for z in zeilen):
        return _fehler("zeilen: Liste von Objekten erwartet")
    if len(zeilen) > ORTE_MAX:
        return _fehler(f"Höchstens {ORTE_MAX} Zeilen — grössere Mengen als Auftrag")
    roh = str(daten.get("bbox") or "").strip()
    bbox = None if roh == "-" else (_parse_collection_bbox(roh) if roh else _COLLECTION_BBOX)
    with _db() as conn:
        halt = R.tagesgrenze_erreicht(conn)
    if halt:
        return _fehler(halt, 429)
    antwort, _ = _orte(zeilen, bbox, bool(daten.get("logik")))
    return jsonify(antwort)


def _orte(zeilen: list, bbox, logik: bool) -> "tuple[dict, float]":
    """Rechnet Zeilen sofort durch die Pipeline; gibt Antwort und Kosten in CHF."""
    from .pipeline import Umgebung, paket
    df = pd.DataFrame(zeilen)
    kasse = Kasse()
    protokoll = io.StringIO()
    with _SOFORT:
        u = Umgebung(kasse=kasse, bbox=bbox, mode="debug" if logik else "human", fortschritt=False)
        try:
            # _SOFORT haelt andere Einzelabfragen draussen; sonst druckt in
            # diesem Dienst niemand nach stdout.
            with redirect_stdout(protokoll):
                out, _, verdacht = paket(u, df, 0, len(df), False, ausgeben=logik)
        finally:
            u.schliessen()
    with _db() as conn:
        R.ausgabe_buchen(conn, kasse.usd)
    out = out.astype(object).where(pd.notna(out), None)
    antwort = {"zeilen": out.to_dict("records"), "verdacht": verdacht, "kosten_chf": round(kasse.chf, 4)}
    if logik:
        antwort["logik"] = protokoll.getvalue().strip("\n")
    return antwort, kasse.chf


# ── /loc: nur die Einzelabfrage ───────────────────────────────────────────────
# Eigenes Cookie mit Pfad /loc/, damit es sich nicht mit der Sitzung von
# /locationer kreuzt.  Es traegt den Fingerabdruck des Passworts: wird das
# Passwort in der .env geaendert, sind alte Anmeldungen ungueltig.
_LOC_COOKIE = "loc"
_LOC_TAGE = 30


def _loc_signer():
    return URLSafeTimedSerializer(app.secret_key, salt="loc")


def _loc_abdruck(pw: str) -> str:
    return hashlib.sha256(("loc:" + pw).encode()).hexdigest()[:16]


def _loc_rolle() -> str:
    """'voll' (mit Logik), 'gast' oder '' (nicht angemeldet)."""
    roh = request.cookies.get(_LOC_COOKIE)
    if not roh:
        return ""
    try:
        d = _loc_signer().loads(roh, max_age=_LOC_TAGE * 86400)
    except BadSignature:
        return ""
    pw = {"voll": LOC_PASSWORT, "gast": LOC_GAST_PASSWORT}.get(d.get("r"), "")
    return d["r"] if pw and hmac.compare_digest(d.get("p", ""), _loc_abdruck(pw)) else ""


@app.route("/loc/", methods=["GET"])
def loc_start():
    rolle = _loc_rolle()
    if not rolle:
        return redirect(url_for("loc_login"))
    return render_template_string(SEITE_LOC, logik=(rolle == "voll"), version=version_text())


@app.route("/loc/login", methods=["GET", "POST"])
def loc_login():
    fehler = None
    if request.method == "POST":
        ein = request.form.get("passwort", "").encode()
        rolle = next((r for r, pw in (("voll", LOC_PASSWORT), ("gast", LOC_GAST_PASSWORT))
                      if pw and hmac.compare_digest(ein, pw.encode())), "")
        if rolle:
            pw = LOC_PASSWORT if rolle == "voll" else LOC_GAST_PASSWORT
            antwort = redirect(url_for("loc_start"))
            antwort.set_cookie(_LOC_COOKIE, _loc_signer().dumps({"r": rolle, "p": _loc_abdruck(pw)}),
                               max_age=_LOC_TAGE * 86400, path=request.script_root + "/loc/",
                               secure=not OFFEN, httponly=True, samesite="Lax")
            return antwort
        time.sleep(1)           # bremst das Durchprobieren
        fehler = "Falsches Passwort."
    return render_template_string(SEITE_LOC_LOGIN, fehler=fehler, version=version_text())


@app.route("/loc/logout", methods=["POST"])
def loc_logout():
    antwort = redirect(url_for("loc_login"))
    antwort.delete_cookie(_LOC_COOKIE, path=request.script_root + "/loc/")
    return antwort


@app.route("/loc/api/ort", methods=["POST"])
def loc_api_ort():
    """Eine Zeile: {"title": …, "description": …}; Logik nur mit dem vollen Zugang."""
    rolle = _loc_rolle()
    if not rolle:
        return _fehler("nicht angemeldet", 401)
    daten = request.get_json(silent=True) or {}
    titel = str(daten.get("title") or "").strip()[:500]
    beschr = str(daten.get("description") or "").strip()[:2000]
    if not titel:
        return _fehler("Titel fehlt")
    with _db() as conn:
        halt = R.tagesgrenze_erreicht(conn)
        if not halt and rolle == "gast" and R.gast_heute_chf(conn) >= LOC_GAST_TAG_CHF:
            halt = "Das Kontingent für Gäste ist für heute aufgebraucht — morgen geht es wieder."
    if halt:
        return _fehler(halt, 429)
    antwort, chf = _orte([{"title": titel, "description": beschr}], _COLLECTION_BBOX, rolle == "voll")
    if rolle == "gast":
        with _db() as conn:
            R.gast_buchen(conn, chf)
        antwort.pop("kosten_chf", None)
    return jsonify(antwort)


# ── Vorlagen ──────────────────────────────────────────────────────────────────
# Gemeinsam fuer /locationer und /loc: Hilfen und die Anzeige einer Einzelabfrage.
GRUND_JS = """
const $ = s => document.querySelector(s);
const chf = x => x == null ? '–' : Number(x).toFixed(2);

function el(tag, attrs = {}, ...kinder) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const k of kinder) if (k != null) e.append(k.nodeType ? k : String(k));
  return e;
}

async function api(pfad, opt = {}) {
  const r = await fetch(pfad, opt);
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((d && d.fehler) || ('HTTP ' + r.status));
  return d;
}
"""

EK_JS = """
const ERKL = {
  gitter: 'Unschärfe in Metern: in diesem Radius darf Pictomap das Bild um den Punkt streuen. 0 = genauer Punkt (Gebäude, Gipfel), 100 = Objekt gefunden, aber eine Fläche, mehr = nur der Ort ist bekannt (Radius nach Ortsgrösse). Leer = keine Koordinate.',
  score: '5 = Objekt in GeoNames, TGN oder Ortsnamen-Datenbank gefunden · 4 = Objekt über Wikidata oder OpenStreetMap gefunden · 3 = nur der Ort, Mittelpunkt aus GeoNames · 2 = nur der Ort, über OpenStreetMap · 0 = keine Koordinate.',
  bildtyp: 'Aus Titel und Beschreibung, nicht aus dem Bild: portrait = Person(en) als Motiv · group = gestelltes Gruppenbild · event = Fest, Umzug, Markt, Szene · view = Gebäude, Strasse, Platz, Ortsansicht, Innenraum · landscape = Natur, Berge, See, Küste · other = Fahrzeuge, Dokumente, Reproduktionen.',
  wikidata: 'Feste Wikidata-Kennung des gefundenen Objekts (Wikidata) und des Orts (Wikidata-Ort). Gleiche Kennung = gleiches Motiv bzw. gleicher Ort, in jeder Sprache. Leer, wenn nur der Ort gefunden wurde oder die Quelle keine Kennung kennt.',
};
let ekKarte = null;

// Zeigt die Antwort von /api/orte bzw. /loc/api/ort in #ek_dl, #ek_karte und #ek_logik.
function zeigeOrt(d) {
  if (d.logik != null && $('#ek_logik')) {
    $('#ek_logik').textContent = d.logik || '(kein Protokoll)';
    $('#ek_logik_reihe').hidden = false;
  }
  const z = d.zeilen[0], lat = z['Breitengrad'], lon = z['Längengrad'];
  const dl = $('#ek_dl'); dl.replaceChildren();
  const paar = (k, v, info) => {
    const dd = el('dd', {}, v == null || v === '' ? '–' : v);
    const dt = el('dt', {}, k);
    if (info) {
      // ⓘ klappt die Erklärung auf — ein title-Tooltip ginge auf dem Telefon nicht.
      const txt = el('div', {class: 'hinweis erkl', hidden: ''}, info);
      dt.append(' ', el('button', {type: 'button', class: 'info', 'aria-label': k + ' erklären',
        onclick: () => { txt.hidden = !txt.hidden; }}, 'i'));
      dd.append(txt);
    }
    dl.append(dt, dd);
  };
  paar('Titel', z['Name']); paar('Land', z['Land']); paar('Region', z['Region']); paar('Ort', z['Stadt/Ort']);
  paar('Koordinaten', lat == null ? 'keine' : Number(lat).toFixed(5) + ', ' + Number(lon).toFixed(5));
  paar('Gitter', z['Gitter'] == null ? null : z['Gitter'] + ' m', ERKL.gitter);
  paar('Score', z['Coord-Quality-Score'], ERKL.score); paar('Bildtyp', z['Bildtyp'], ERKL.bildtyp);
  const wd = q => q ? el('a', {href: 'https://www.wikidata.org/wiki/' + q, target: '_blank', rel: 'noopener'}, q) : null;
  paar('Wikidata', wd(z['Wikidata']), ERKL.wikidata); paar('Wikidata-Ort', wd(z['Wikidata-Ort']));
  if (z['Ambiguous']) paar('Ort mehrdeutig', 'ja');
  if (z['Nachgefasst']) paar('Nachgefasst', z['Nachgefasst']);
  if (z['Widerspruch']) paar('Widerspruch', z['Widerspruch']);
  if (d.kosten_chf != null) paar('Kosten', chf(d.kosten_chf) + ' CHF');
  $('#ek_erg').hidden = false;
  if (!ekKarte) {
    ekKarte = L.map('ek_karte');
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      {maxZoom: 19, attribution: '© OpenStreetMap'}).addTo(ekKarte);
  }
  ekKarte.invalidateSize();
  ekKarte.eachLayer(l => { if (!(l instanceof L.TileLayer)) ekKarte.removeLayer(l); });
  if (lat != null) {
    // Erst der Ausschnitt, dann die Ebenen: ein Kreis auf einer Karte ohne
    // Ausschnitt wirft in Leaflet „layerPointToLatLng" (erster Test 2026-10-25).
    const p = L.latLng(lat, lon), r = Number(z['Gitter'] || 0);
    if (r > 0) ekKarte.fitBounds(p.toBounds(r * 2.4)); else ekKarte.setView(p, 16);
    L.marker(p).addTo(ekKarte).bindPopup(z['Name'] || '');
    if (r > 0) L.circle(p, {radius: r, weight: 1}).addTo(ekKarte);
  } else ekKarte.setView([46.8, 8.2], 3);
}
"""

# Logo von tools.cronica.ch/static (dieselbe Datei wie /layer und /timeslides).
LOGO = '<img class="logo" src="/static/logo.png" alt="Locomot">'

STIL = """
:root{--pap:#f5f7f6;--kar:#fff;--lin:#dde3e0;--tex:#1f2623;--sec:#66726c;
      --akz:#1f6f5c;--gut:#2f7d4f;--warn:#9a5b00;--warn-hg:#fbf0dc;--krit:#a3302a;--krit-hg:#f9e4e2}
*{box-sizing:border-box}
body{margin:0;background:var(--pap);color:var(--tex);
     font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",sans-serif}
a{color:var(--akz)}
.hut{display:flex;flex-wrap:wrap;align-items:baseline;gap:14px;padding:14px 22px;
     border-bottom:1px solid var(--lin);background:var(--kar)}
.hut h1{margin:0;font-size:17px;font-weight:650}
.logo{height:26px;width:auto;align-self:center;display:block}
.hut .weg{margin-left:auto;display:flex;gap:14px;align-items:baseline}
main{max-width:1180px;margin:0 auto;padding:22px}
.karte{background:var(--kar);border:1px solid var(--lin);border-radius:8px;padding:18px 20px;margin-bottom:20px}
h2{margin:0 0 14px;font-size:13px;font-weight:650;text-transform:uppercase;letter-spacing:.06em;color:var(--sec)}
label{display:block;font-size:12px;color:var(--sec);margin-bottom:4px}
input[type=text],input[type=file]{width:100%;padding:7px 9px;border:1px solid var(--lin);
    border-radius:5px;background:var(--pap);color:var(--tex);font:inherit}
.raster{display:grid;gap:14px;grid-template-columns:2fr 1.4fr 1fr 1.6fr}
@media(max-width:820px){.raster{grid-template-columns:1fr}}
button{font:inherit;padding:7px 13px;border-radius:6px;border:1px solid var(--lin);
       background:var(--kar);color:var(--tex);cursor:pointer}
button:hover{border-color:var(--akz)}
button.haupt{background:var(--akz);border-color:var(--akz);color:#fff;font-weight:600}
button:disabled{opacity:.45;cursor:not-allowed}
button.warn{border-color:var(--krit);color:var(--krit)}
.reihe{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:14px}
.hinweis{font-size:12px;color:var(--sec)}
pre{background:#1d2421;color:#e2e8e4;padding:12px 14px;border-radius:6px;
    font:12px/1.5 ui-monospace,Menlo,monospace;max-height:320px;overflow:auto;white-space:pre-wrap;margin:0}
.wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--sec);
   font-weight:600;padding:0 10px 8px 0;border-bottom:1px solid var(--lin)}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--lin);vertical-align:middle}
td.z,th.z{text-align:right;font-variant-numeric:tabular-nums}
tr.wahl td{background:#e8f2ee}
tr.zeile{cursor:pointer}
.pille{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;border:1px solid var(--lin)}
.p-läuft{background:#e2f0eb;border-color:var(--akz);color:var(--akz)}
.p-fertig{background:#e4f2e8;border-color:var(--gut);color:var(--gut)}
.p-angehalten,.p-bereit{background:var(--warn-hg);border-color:var(--warn);color:var(--warn)}
.p-fehler,.p-abgebrochen{background:var(--krit-hg);border-color:var(--krit);color:var(--krit)}
.balken{height:6px;background:var(--lin);border-radius:3px;overflow:hidden;min-width:90px}
.balken i{display:block;height:100%;background:var(--akz)}
.alarm{background:var(--krit-hg);border:1px solid var(--krit);color:var(--tex);border-radius:8px;
       padding:12px 16px;margin-bottom:20px}
.alarm strong{color:var(--krit)}
.gelb{background:var(--warn-hg);border:1px solid var(--warn);border-radius:8px;padding:10px 14px;margin:10px 0}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0 0 12px;font-size:14px}
dt{color:var(--sec)} dd{margin:0;font-variant-numeric:tabular-nums}
"""

SEITE_LOGIN = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Locationer</title><style>""" + STIL + """
body{display:grid;place-items:center;min-height:100vh}
form{background:var(--kar);border:1px solid var(--lin);border-radius:8px;padding:26px;width:320px}</style>
<form method="post">
  """ + LOGO + """
  <h2 style="margin-top:18px">Locationer</h2>
  <label for="p">Passwort</label>
  <input type="password" id="p" name="passwort" autofocus
         style="width:100%;padding:7px 9px;border:1px solid var(--lin);border-radius:5px;font:inherit">
  {% if fehler %}<p class="hinweis" style="color:var(--krit)">{{ fehler }}</p>{% endif %}
  <div class="reihe"><button class="haupt" type="submit">Anmelden</button></div>
  <p class="hinweis">{{ version }}</p>
</form>"""

SEITE_START = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Locationer</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>""" + STIL + """
textarea{width:100%;padding:7px 9px;border:1px solid var(--lin);border-radius:5px;background:var(--pap);
         color:var(--tex);font:inherit;min-height:60px;resize:vertical}
.zwei{display:grid;gap:18px;grid-template-columns:1fr 1fr;margin-top:14px}
@media(max-width:820px){.zwei{grid-template-columns:1fr}}
#ek_karte{height:300px;border-radius:6px;border:1px solid var(--lin)}
#sr_karte{height:380px;border-radius:6px;border:1px solid var(--lin)}
#sr_karte.zeichnen{cursor:crosshair}
button.klein{padding:0 8px;font-size:12px;margin-left:6px}
details.karte>summary{cursor:pointer;list-style:none;display:flex;align-items:baseline;gap:12px}
details.karte>summary::-webkit-details-marker{display:none}
details.karte>summary h2{margin:0}
details.karte>summary h2::before{content:'▸ ';display:inline-block;width:1.1em}
details.karte[open]>summary h2::before{content:'▾ '}
details.karte[open]>summary{margin-bottom:14px}
.kopf{display:flex;align-items:baseline;gap:12px} .kopf h2{margin-bottom:14px} .kopf button{margin-left:auto}
button.info{padding:0 6px;border-radius:50%;font:600 11px/16px ui-serif,Georgia,serif;color:var(--akz);border-color:var(--akz);background:var(--kar);vertical-align:1px}
.erkl{margin-top:4px;max-width:46ch}
</style>
<div class="hut">
  """ + LOGO + """
  <h1>Locationer</h1>
  <span class="hinweis">{{ version }} · Orte für Bilder finden · Koordinaten nur aus Datenbanken</span>
  <span class="weg"><span class="hinweis" id="heute"></span>
    <button id="tg_knopf" title="Tagesgrenze für alle Aufträge zusammen">Tagesgrenze ändern</button>
    <form method="post" action="logout"><button>Abmelden</button></form></span>
</div>
<main>
  <div id="alarm"></div>

  <div class="karte">
    <h2>Neuer Auftrag</h2>
    <form id="neu">
      <div class="raster">
        <div><label for="datei">Datei (CSV oder XLSX)</label><input type="file" id="datei" name="datei" accept=".csv,.xlsx,.xls" required></div>
        <div><label for="name">Auftragsname</label><input type="text" id="name" name="name" placeholder="wie die Datei"></div>
        <div><label for="grenze">Kostengrenze CHF</label><input type="text" id="grenze" name="grenze_chf" value="{{ '%g' % grenze }}" required></div>
        <div><label for="bbox">Suchraum (Bbox) <button type="button" class="info" id="sr_info" aria-label="Suchraum erklären">i</button>
             <button type="button" id="sr_knopf" class="klein" aria-expanded="false">Auf Karte</button></label>
             <input type="text" id="bbox" name="bbox" value="{{ bbox }}" placeholder="min_lat,max_lat,min_lon,max_lon · - = ohne"></div>
      </div>
      <p class="hinweis erkl" id="sr_erkl" hidden style="max-width:none">Gilt nur für Zeilen, bei denen kein Land erkannt wird:
        dann werden Treffer ausserhalb des Suchraums verworfen, und bei mehrdeutigen Berg-, See- und Passnamen gewinnt der
        Treffer nahe der Mitte. Mit erkanntem Land spielt der Suchraum keine Rolle. „-“ = ohne Suchraum.</p>
      <div id="sr_panel" hidden style="margin-top:14px">
        <div id="sr_karte"></div>
        <div class="reihe" style="margin-top:8px">
          <button type="button" id="sr_neu">Neu zeichnen</button>
          <button type="button" id="sr_vorgabe">Vorgabe</button>
          <button type="button" id="sr_ohne">Ohne Suchraum</button>
          <span class="hinweis" id="sr_hinweis"></span></div>
      </div>
      <div class="reihe"><button class="haupt" type="submit" id="hoch">Hochladen und prüfen</button>
        <span class="hinweis" id="neu_info">Nach der Prüfung siehst du Spalten und geschätzte Kosten, dann startest du.</span></div>
    </form>
  </div>

  <details class="karte" id="ek_box">
    <summary><h2>Einzelabfrage</h2> <span class="hinweis">eine Zeile sofort prüfen</span></summary>
    <form id="ek">
      <div class="raster" style="grid-template-columns:1fr 1fr">
        <div><label for="ek_titel">Titel</label><input type="text" id="ek_titel" required
             placeholder="z.B. Winterthur, Stadthaus, Teilansicht"></div>
        <div><label for="ek_beschr">Beschreibung (optional)</label><textarea id="ek_beschr" rows="2"></textarea></div>
      </div>
      <div class="reihe"><button class="haupt" type="submit" id="ek_los">Ort suchen</button>
        <span class="hinweis" id="ek_info">Dieselbe Auswertung wie eine Zeile in einer CSV-Datei, mit dem Suchraum aus „Neuer Auftrag“.</span></div>
    </form>
    <div class="zwei" id="ek_erg" hidden><dl id="ek_dl"></dl><div id="ek_karte"></div></div>
    <div class="reihe" id="ek_logik_reihe" hidden>
      <button type="button" id="ek_logik_knopf" aria-expanded="false">Logik</button>
      <span class="hinweis">Was Haiku verstanden hat und was jede Datenbank geantwortet hat.</span></div>
    <pre id="ek_logik" hidden style="margin-top:10px;max-height:480px"></pre>
  </details>

  <div class="karte" id="detail" hidden></div>

  <div class="karte">
    <h2>Aufträge</h2>
    <div class="wrap"><table>
      <thead><tr><th>Nr</th><th>Auftragsname</th><th>Datei</th><th>Erstellt</th><th>Zustand</th><th>Fortschritt</th><th class="z">Zeilen</th>
        <th class="z">Kosten CHF</th><th class="z">Hochrechnung CHF</th><th>Fertig ca.</th><th class="z">Grenze</th><th>Version</th></tr></thead>
      <tbody id="liste"></tbody>
    </table></div>
  </div>
</main>
<script>""" + GRUND_JS + EK_JS + """
let gewaehlt = null, liste = [], neuId = null;
const entwurf = {};      // Auftrag → getippte Grenze, bis sie gespeichert ist
const zahl = x => x == null ? '–' : String(x).replace(/\\B(?=(\\d{3})+(?!\\d))/g, "'");
const zeit = x => x ? new Date(x).toLocaleString('de-CH', {day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'}) : '–';
const uhr = d => d.toLocaleTimeString('de-CH', {hour: '2-digit', minute: '2-digit'});
// Fertig ca.: Tempo seit dem Start mal restliche Zeilen; nur solange der Auftrag läuft.
function fertigCa(a) {
  if (a.zustand !== 'läuft' || !a.gestartet || !a.erledigt || !a.zeilen) return null;
  const jetzt = Date.now(), seit = jetzt - new Date(a.gestartet);
  if (seit <= 0) return null;
  const ende = new Date(jetzt + seit / a.erledigt * (a.zeilen - a.erledigt));
  const tage = Math.round((new Date(ende).setHours(0, 0, 0, 0) - new Date(jetzt).setHours(0, 0, 0, 0)) / 86400000);
  if (tage === 0) return 'heute ' + uhr(ende);
  if (tage === 1) return 'morgen ' + uhr(ende);
  return ende.toLocaleDateString('de-CH', {weekday: 'short', day: 'numeric', month: 'numeric'}) + ' ' + uhr(ende);
}
// Protokollzeilen beginnen mit Serverzeit ohne Zone → in Ortszeit des Betrachters.
const protoZeit = (z, zone) => z.replace(/^(\\d{4}-\\d\\d-\\d\\d) (\\d\\d:\\d\\d:\\d\\d)/, (m, d, t) => {
  const x = new Date(d + 'T' + t + (zone || '').replace(/(\\d\\d)(\\d\\d)$/, '$1:$2'));
  return isNaN(x) ? m : x.toLocaleDateString('sv-SE') + ' ' + x.toLocaleTimeString('de-CH');
});
function dauer(von, bis) {
  if (!von) return '–';
  const s = Math.max(0, ((bis ? new Date(bis) : new Date()) - new Date(von)) / 1000);
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return (h ? h + ' h ' : '') + m + ' min' + (bis ? '' : ' (läuft noch)');
}

function zeichneListe() {
  const tb = $('#liste'); tb.replaceChildren();
  for (const a of liste) {
    const anteil = a.zeilen ? Math.min(100, 100 * a.erledigt / a.zeilen) : 0;
    const tr = el('tr', {class: 'zeile' + (a.id === gewaehlt ? ' wahl' : ''), onclick: () => waehle(a.id)},
      el('td', {}, a.id), el('td', {}, a.name), el('td', {class: 'hinweis'}, a.datei || '–'),
      el('td', {class: 'hinweis'}, zeit(a.erstellt)),
      el('td', {}, el('span', {class: 'pille p-' + a.zustand}, a.zustand)),
      el('td', {}, el('div', {class: 'balken', title: anteil.toFixed(1) + ' %'}, el('i', {style: 'width:' + anteil + '%'}))),
      el('td', {class: 'z'}, zahl(a.erledigt) + ' / ' + zahl(a.zeilen)),
      el('td', {class: 'z'}, chf(a.chf)), el('td', {class: 'z'}, chf(a.hochrechnung_chf)),
      el('td', {class: 'hinweis'}, fertigCa(a) || '–'),
      el('td', {class: 'z'}, chf(a.grenze_chf)), el('td', {class: 'hinweis'}, a.version || ''));
    tb.append(tr);
  }
  // Alarm: jeder angehaltene Auftrag steht oben, und der Tab-Titel zeigt es.
  const halt = liste.filter(a => a.zustand === 'angehalten' || a.zustand === 'fehler');
  const al = $('#alarm'); al.replaceChildren();
  for (const a of halt) al.append(el('div', {class: 'alarm'}, el('strong', {}, (a.zustand === 'fehler' ? 'Fehler' : 'Angehalten') + ' — Auftrag ' + a.id + ' (' + a.name + '): '), a.grund || ''));
  document.title = (halt.length ? '⏸ ' + halt.length + ' · ' : '') + 'Locationer';
}

// Ein zweiter Klick auf denselben Auftrag klappt ihn wieder zu.
async function waehle(id) { gewaehlt = (gewaehlt === id ? null : id); zeichneListe(); await zeichneDetail(); }

async function aktion(pfad, body) {
  try {
    await api(pfad, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
    delete entwurf[gewaehlt];
    await laden();
  } catch (e) { $('#det_info').textContent = e.message; }
}

let protoStand = null;    // Scrollstand des Protokolls über das Neuzeichnen hinweg
async function zeichneDetail() {
  const box = $('#detail');
  const alt = $('#det_protokoll');
  if (alt) protoStand = {id: gewaehlt, oben: alt.scrollTop, unten: alt.scrollTop + alt.clientHeight >= alt.scrollHeight - 4};
  if (gewaehlt == null) { box.hidden = true; return; }
  let a; try { a = await api('api/auftraege/' + gewaehlt); } catch (e) { box.hidden = true; return; }
  box.hidden = false; box.replaceChildren();
  box.append(el('div', {class: 'kopf'}, el('h2', {}, 'Auftrag ' + a.id + ' · ' + a.name),
    el('button', {onclick: () => waehle(a.id), title: 'Detailansicht schliessen'}, 'Schliessen')));
  if (a.grund) box.append(el('div', {class: a.zustand === 'fehler' || a.zustand === 'angehalten' ? 'alarm' : 'gelb'}, el('strong', {}, a.zustand + ': '), a.grund));
  const s = a.schaetzung;
  const dl = el('dl', {},
    el('dt', {}, 'Datei'), el('dd', {}, a.datei || '–'),
    el('dt', {}, 'Erstellt'), el('dd', {}, zeit(a.erstellt)),
    el('dt', {}, 'Gestartet'), el('dd', {}, zeit(a.gestartet)),
    el('dt', {}, 'Beendet'), el('dd', {}, zeit(a.beendet)),
    el('dt', {}, 'Rechenzeit'), el('dd', {}, a.gestartet ? dauer(a.gestartet, a.beendet) : '–'),
    el('dt', {}, 'Zustand'), el('dd', {}, a.zustand),
    el('dt', {}, 'Zeilen'), el('dd', {}, zahl(a.erledigt) + ' von ' + zahl(a.zeilen) +
      (s ? ' · ' + zahl(s.neu) + ' neue von ' + zahl(s.verschieden) + ' verschiedenen' : '')),
    el('dt', {}, 'Schätzung vorher'), el('dd', {}, s ? 'höchstens ' + chf(s.chf) + ' CHF' : '–'),
    el('dt', {}, 'Kosten bisher'), el('dd', {}, chf(a.chf) + ' CHF'),
    el('dt', {}, 'Hochrechnung'), el('dd', {}, a.hochrechnung_chf == null ? 'ab 1000 Zeilen' : chf(a.hochrechnung_chf) + ' CHF'),
    el('dt', {}, 'Fertig ca.'), el('dd', {}, fertigCa(a) || '–'),
    el('dt', {}, 'Grenze'), el('dd', {}, chf(a.grenze_chf) + ' CHF'),
    el('dt', {}, 'Suchraum'), el('dd', {}, a.bbox ? a.bbox.join(', ') : 'ohne'),
    el('dt', {}, 'Version'), el('dd', {}, a.version || '–'));
  box.append(dl);
  const r = el('div', {class: 'reihe'});
  // Was getippt wurde, überlebt das Neuzeichnen alle 4 s (vorher sprang es auf die alte Grenze zurück).
  const gr = el('input', {type: 'text', id: 'neue_grenze', value: entwurf[a.id] ?? a.grenze_chf, style: 'width:110px',
                          oninput: ev => { entwurf[a.id] = ev.target.value; }});
  if (a.zustand === 'bereit') {
    r.append(el('label', {for: 'neue_grenze', style: 'margin:0'}, 'Grenze CHF'), gr,
      el('button', {class: 'haupt', onclick: () => aktion('api/auftraege/' + a.id + '/starten', {grenze_chf: gr.value})}, 'Starten'));
  }
  if (['angehalten', 'fehler', 'abgebrochen'].includes(a.zustand)) {
    r.append(el('label', {for: 'neue_grenze', style: 'margin:0'}, 'Grenze CHF'), gr,
      el('button', {class: 'haupt', onclick: () => aktion('api/auftraege/' + a.id + (a.zustand === 'angehalten' && !a.erledigt && a.schaetzung ? '/starten' : '/weiter'), {grenze_chf: gr.value})}, 'Fortsetzen'));
  }
  if (['läuft', 'wartet', 'bereit'].includes(a.zustand))
    r.append(el('button', {onclick: () => aktion('api/auftraege/' + a.id + '/anhalten')}, 'Anhalten'));
  if (['läuft', 'wartet', 'bereit', 'angehalten', 'fehler', 'prüfen'].includes(a.zustand))
    r.append(el('button', {class: 'warn', onclick: () => aktion('api/auftraege/' + a.id + '/abbrechen')}, 'Abbrechen'));
  if (a.ergebnis && a.zustand === 'fertig') r.append(el('a', {href: 'api/auftraege/' + a.id + '/ergebnis'}, 'Ergebnis CSV'));
  if (a.karte) r.append(el('a', {href: 'auftrag/' + a.id + '/karte', target: '_blank'}, 'Karte'));
  if (a.teilergebnis && a.zustand !== 'fertig' && a.zustand !== 'läuft') r.append(el('a', {href: 'api/auftraege/' + a.id + '/ergebnis?teilweise=1'}, 'Teilergebnis CSV'));
  r.append(el('span', {class: 'hinweis', id: 'det_info'}));
  box.append(r);
  if (a.spalten) { box.append(el('h2', {style: 'margin-top:18px'}, 'Spalten')); box.append(el('pre', {}, a.spalten)); }
  if (a.protokoll && a.protokoll.length) {
    // Bleibt unten, solange man unten ist; wer hochscrollt, um zu lesen, wird nicht zurückgerissen.
    const p = el('pre', {id: 'det_protokoll'}, a.protokoll.map(z => protoZeit(z, a.zone)).join('\\n'));
    box.append(el('h2', {style: 'margin-top:18px'}, 'Protokoll'), p);
    p.scrollTop = protoStand && protoStand.id === a.id && !protoStand.unten ? protoStand.oben : p.scrollHeight;
  }
}

async function laden() {
  try {
    liste = await api('api/auftraege');
    const st = await api('api/status');
    $('#heute').textContent = 'heute ' + chf(st.heute_chf) + ' von ' + chf(st.tagesgrenze_chf) + ' CHF';
    zeichneListe();
    const neu = neuId != null && liste.find(x => x.id === neuId);
    if (neu && neu.zustand !== 'prüfen') {
      $('#neu_info').textContent = 'Auftrag ' + neu.id + ' geprüft: ' + neu.zustand +
        (neu.schaetzung ? ', höchstens ' + chf(neu.schaetzung.chf) + ' CHF' : '') +
        (neu.zustand === 'bereit' ? ' — unten Grenze prüfen und starten.' : (neu.grund ? ' — ' + neu.grund : ''));
      neuId = null;
    }
    if (gewaehlt != null && !document.activeElement?.matches('#neue_grenze')) await zeichneDetail();
  } catch (e) { $('#heute').textContent = 'keine Verbindung: ' + e.message; }
}

$('#neu').addEventListener('submit', async ev => {
  ev.preventDefault();
  const fd = new FormData($('#neu'));
  $('#hoch').disabled = true; $('#neu_info').textContent = 'lädt hoch …';
  try {
    const a = await api('api/auftraege', {method: 'POST', body: fd});
    $('#neu_info').textContent = 'Auftrag ' + a.id + ' angelegt — wird geprüft …'; neuId = a.id;
    $('#neu').reset(); $('#grenze').value = '{{ '%g' % grenze }}'; $('#bbox').value = '{{ bbox }}'; if (srKarte) srZeigen();
    gewaehlt = a.id; await laden();
  } catch (e) { $('#neu_info').textContent = e.message; }
  $('#hoch').disabled = false;
});

$('#tg_knopf').addEventListener('click', () => {
  const box = $('#detail'); gewaehlt = null; box.hidden = false; box.replaceChildren();
  const ein = el('input', {type: 'text', id: 'tg_wert', style: 'width:110px'});
  const info = el('span', {class: 'hinweis'});
  box.append(el('h2', {}, 'Tagesgrenze'),
    el('p', {class: 'hinweis'}, 'Gilt für alle Aufträge zusammen, pro Kalendertag. Ist sie erreicht, halten laufende Aufträge an.'),
    el('div', {class: 'reihe'}, el('label', {for: 'tg_wert', style: 'margin:0'}, 'CHF pro Tag'), ein,
      el('button', {class: 'haupt', onclick: async () => {
        try { await api('api/tagesgrenze', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({chf: ein.value})}); info.textContent = 'gespeichert'; laden(); }
        catch (e) { info.textContent = e.message; } }}, 'Speichern'), info));
  ein.focus();
});

// ── Einzelabfrage: eine Zeile über /api/orte, also dieselbe Pipeline wie im Auftrag
$('#ek').addEventListener('submit', async ev => {
  ev.preventDefault();
  $('#ek_los').disabled = true; $('#ek_info').textContent = 'sucht … (einige Sekunden)';
  try {
    const d = await api('api/orte', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({zeilen: [{title: $('#ek_titel').value, description: $('#ek_beschr').value}],
                            bbox: $('#bbox').value, logik: true})});
    zeigeOrt(d);
    $('#ek_info').textContent = '';
  } catch (e) { $('#ek_info').textContent = e.message; }
  $('#ek_los').disabled = false;
});

$('#ek_logik_knopf').addEventListener('click', ev => {
  const pre = $('#ek_logik'); pre.hidden = !pre.hidden;
  ev.currentTarget.setAttribute('aria-expanded', String(!pre.hidden));
});

// ── Suchraum auf der Karte: zwei Klicks setzen zwei Ecken (geht auch auf dem Telefon,
// und Ziehen bleibt fürs Verschieben der Karte).
const SR_VORGABE = '{{ bbox }}';
let srKarte = null, srRechteck = null, srEcke = null, srZeichnen = false;
const srLesen = t => {
  const z = String(t).split(',').map(x => parseFloat(x));
  return z.length === 4 && z.every(Number.isFinite) ? L.latLngBounds([z[0], z[2]], [z[1], z[3]]) : null;
};
const srText = b => [b.getSouth(), b.getNorth(), b.getWest(), b.getEast()].map(x => x.toFixed(2)).join(',');
function srModus(an) {
  srZeichnen = an; srEcke = null;
  $('#sr_karte').classList.toggle('zeichnen', an);
  $('#sr_hinweis').textContent = an ? 'Erste Ecke anklicken …' : 'Karte verschieben und zoomen wie gewohnt.';
}
function srZeigen() {
  if (srRechteck) { srKarte.removeLayer(srRechteck); srRechteck = null; }
  const b = srLesen($('#bbox').value);
  if (b) { srRechteck = L.rectangle(b, {weight: 2, color: '#1f6f5c', fillOpacity: .08}).addTo(srKarte); srKarte.fitBounds(b.pad(.15)); }
  else srKarte.setView([48, 8], 4);
}
// Einzelabfrage auf- oder zugeklappt: pro Browser gemerkt.
try { if (localStorage.getItem('ek_offen') === '1') $('#ek_box').open = true; } catch (e) {}
$('#ek_box').addEventListener('toggle', () => {
  try { localStorage.setItem('ek_offen', $('#ek_box').open ? '1' : '0'); } catch (e) {}
  if ($('#ek_box').open && ekKarte) ekKarte.invalidateSize();
});

$('#sr_info').addEventListener('click', () => { $('#sr_erkl').hidden = !$('#sr_erkl').hidden; });
$('#sr_knopf').addEventListener('click', ev => {
  const p = $('#sr_panel'); p.hidden = !p.hidden;
  ev.currentTarget.setAttribute('aria-expanded', String(!p.hidden));
  if (p.hidden) return;
  if (!srKarte) {
    srKarte = L.map('sr_karte');
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      {maxZoom: 18, attribution: '© OpenStreetMap'}).addTo(srKarte);
    srKarte.on('click', e => {
      if (!srZeichnen) return;
      if (!srEcke) {
        srEcke = e.latlng;
        if (srRechteck) srKarte.removeLayer(srRechteck);
        srRechteck = L.rectangle(L.latLngBounds(srEcke, srEcke), {weight: 2, color: '#1f6f5c', dashArray: '4', fillOpacity: .05}).addTo(srKarte);
        $('#sr_hinweis').textContent = 'Zweite Ecke anklicken …';
      } else {
        $('#bbox').value = srText(L.latLngBounds(srEcke, e.latlng));
        srModus(false); srZeigen();
        $('#sr_hinweis').textContent = 'Übernommen: ' + $('#bbox').value;
      }
    });
    srKarte.on('mousemove', e => { if (srZeichnen && srEcke && srRechteck) srRechteck.setBounds(L.latLngBounds(srEcke, e.latlng)); });
  }
  srKarte.invalidateSize(); srZeigen(); srModus(false);
});
$('#sr_neu').addEventListener('click', () => srModus(true));
$('#sr_vorgabe').addEventListener('click', () => { $('#bbox').value = SR_VORGABE; srModus(false); srZeigen(); });
$('#sr_ohne').addEventListener('click', () => { $('#bbox').value = '-'; srModus(false); srZeigen(); $('#sr_hinweis').textContent = 'Ohne Suchraum.'; });
$('#bbox').addEventListener('input', () => { if (srKarte && !$('#sr_panel').hidden) srZeigen(); });

laden(); setInterval(laden, 4000);
</script>"""


SEITE_LOC_LOGIN = SEITE_LOGIN.replace("<h2 style=\"margin-top:18px\">Locationer</h2>",
                                      "<h2 style=\"margin-top:18px\">Ort finden</h2>")

SEITE_LOC = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Ort finden · Locationer</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>""" + STIL + """
textarea{width:100%;padding:7px 9px;border:1px solid var(--lin);border-radius:5px;background:var(--pap);
         color:var(--tex);font:inherit;min-height:44px;resize:vertical}
main{max-width:1400px}
.feld{display:grid;gap:12px;grid-template-columns:1fr 1fr auto;align-items:end}
@media(max-width:820px){.feld{grid-template-columns:1fr}}
.erg{display:grid;gap:18px;grid-template-columns:minmax(260px,360px) 1fr;margin-top:16px}
@media(max-width:820px){.erg{grid-template-columns:1fr}}
#ek_karte{height:max(420px,calc(100vh - 250px));border-radius:6px;border:1px solid var(--lin)}
button.info{padding:0 6px;border-radius:50%;font:600 11px/16px ui-serif,Georgia,serif;color:var(--akz);border-color:var(--akz);background:var(--kar);vertical-align:1px}
.erkl{margin-top:4px;max-width:40ch}
</style>
<div class="hut">
  """ + LOGO + """
  <h1>Ort finden</h1>
  <span class="hinweis">Bildtitel eingeben, Ort auf der Karte sehen · Koordinaten nur aus Datenbanken</span>
  <span class="weg"><span class="hinweis">{{ version }}</span>
    <form method="post" action="logout"><button>Abmelden</button></form></span>
</div>
<main>
  <div class="karte">
    <form id="ek" class="feld">
      <div><label for="ek_titel">Titel</label><input type="text" id="ek_titel" required autofocus
           placeholder="z.B. Chur, St. Martinskirche"></div>
      <div><label for="ek_beschr">Beschreibung (optional)</label><textarea id="ek_beschr" rows="1"></textarea></div>
      <div><button class="haupt" type="submit" id="ek_los">Ort suchen</button></div>
    </form>
    <p class="hinweis" id="ek_info" style="margin:8px 0 0"></p>
    <div class="erg" id="ek_erg" hidden><dl id="ek_dl"></dl><div id="ek_karte"></div></div>
    {% if logik %}
    <div class="reihe" id="ek_logik_reihe" hidden>
      <button type="button" id="ek_logik_knopf" aria-expanded="false">Logik</button>
      <span class="hinweis">Was Haiku verstanden hat und was jede Datenbank geantwortet hat.</span></div>
    <pre id="ek_logik" hidden style="margin-top:10px;max-height:480px"></pre>
    {% endif %}
  </div>
</main>
<script>""" + GRUND_JS + EK_JS + """
$('#ek').addEventListener('submit', async ev => {
  ev.preventDefault();
  $('#ek_los').disabled = true; $('#ek_info').textContent = 'sucht … (einige Sekunden)';
  try {
    const d = await api('api/ort', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title: $('#ek_titel').value, description: $('#ek_beschr').value})});
    zeigeOrt(d);
    $('#ek_info').textContent = '';
  } catch (e) { $('#ek_info').textContent = e.message; }
  $('#ek_los').disabled = false;
});
if ($('#ek_logik_knopf')) $('#ek_logik_knopf').addEventListener('click', ev => {
  const pre = $('#ek_logik'); pre.hidden = !pre.hidden;
  ev.currentTarget.setAttribute('aria-expanded', String(!pre.hidden));
});
</script>"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5006")), debug=False)
