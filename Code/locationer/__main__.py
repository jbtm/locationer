"""Terminal-Lauf:  python -m locationer <eingabe.csv> [--output …] [--workers 6]

Seit 2026-10-25 ist jeder Lauf ein Auftrag (auftrag.py) mit eigenem
Verzeichnis `<ausgabe>.auftrag/` neben der Ausgabe.  Derselbe Befehl
nochmals setzt GENAU diesen Auftrag fort — nach Pruefung, dass die Eingabe
dieselbe ist.  Eine vorhandene Ausgabedatei ohne Auftrag wird nie
ueberschrieben oder ergaenzt.
"""
import argparse
import os
import shutil
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from . import pipeline as P
from . import version_text
from .auftrag import Auftrag, AuftragFehler, _sha256, schaetzen
from .metrics import TESTFILE

_GRENZE_CHF = float(os.getenv("LOCATIONER_GRENZE_CHF", "20"))


def _bbox_fragen():
    bbox = P._COLLECTION_BBOX
    if bbox:
        mn_lat, mx_lat, mn_lon, mx_lon = bbox
        print(f"\nCollection-Bbox: {mn_lat},{mx_lat},{mn_lon},{mx_lon}")
        answer = input("  Passt das für diese Sammlung? [Y/n/-=ohne Filter/neuer Wert] ").strip()
        if answer.lower() == "n":
            sys.exit(f"Abgebrochen — bitte COLLECTION_BBOX in {Path('.env').resolve()} anpassen.")
        elif answer == "-":
            print("  → Kein Schwerpunktfilter für diesen Lauf.")
            return None
        elif answer and answer.lower() not in ("y", "j", ""):
            neu = P._parse_collection_bbox(answer)
            if neu:
                print(f"  → Bbox für diesen Lauf: {answer}")
                return neu
            print("  → Ungültiges Format, bestehende Bbox wird verwendet.")
        return bbox
    print("\nCollection-Bbox: nicht gesetzt (global — kein Schwerpunktfilter)")
    answer = input("  COLLECTION_BBOX setzen? [Enter=weiter / min_lat,max_lat,min_lon,max_lon] ").strip()
    if answer:
        neu = P._parse_collection_bbox(answer)
        if neu:
            print(f"  → Bbox für diesen Lauf: {answer}")
            return neu
    return None


def _statistik(out_df: pd.DataFrame, unbekannt: int) -> None:
    zeilen = [{"Coord-Quality-Score": int(float(s)) if str(s).strip() else 0,
               "Fallback": str(f).strip().lower() == "true"}
              for s, f in zip(out_df["Coord-Quality-Score"], out_df["Fallback"])]
    P._print_stats(zeilen, unbekannt)


def main():
    ap = argparse.ArgumentParser(description="Locationer – geocoding pipeline")
    ap.add_argument("input", help="Input CSV or XLSX file")
    ap.add_argument("--mode", choices=["human", "debug"], default="human")
    ap.add_argument("--output", "-o", help="Output CSV (default: <input>_loc-<datum>.csv)")
    ap.add_argument("--limit", type=int, help="Process only first N rows")
    ap.add_argument("--chunk-size", type=int, default=20, dest="chunk_size",
                    help="Zeilen pro Paket (default: 20)")
    ap.add_argument("--workers", type=int, default=int(os.getenv("LOCATIONER_WORKERS", "6")),
                    help="Pakete gleichzeitig (default: 6; im Debug-Modus immer 1)")
    ap.add_argument("--grenze-chf", type=float, dest="grenze_chf",
                    help=f"Kostengrenze für diesen Auftrag in CHF (default: {_GRENZE_CHF:g}); "
                         f"beim Fortsetzen: neue, höhere Grenze")
    ap.add_argument("--geo-db", default=P._GEO_DB_DEFAULT, dest="geo_db")
    args = ap.parse_args()
    workers = 1 if args.mode == "debug" else max(1, args.workers)

    ist_testdatei = Path(args.input).resolve() == Path(TESTFILE).resolve()
    if args.output:
        out_path = Path(args.output)
    elif ist_testdatei:
        out_path = Path(str(Path(args.input).with_suffix("")) + "_geo.csv")
    else:
        out_path = Path(f"{Path(args.input).with_suffix('')}_loc-{date.today().isoformat()}.csv")
    auftrag_dir = out_path.with_name(out_path.name + ".auftrag")
    if ist_testdatei and not args.output and (auftrag_dir.exists() or out_path.exists()):
        # Die Rauchprobe misst den AKTUELLEN Code: nie fortsetzen, immer neu.
        # Nur fuer den festen TestFile-Namen — fremde Ausgaben bleiben unberuehrt.
        print(f"TestFile: vorherige Rauchprobe {out_path.name} wird ersetzt")
        shutil.rmtree(auftrag_dir, ignore_errors=True)
        out_path.unlink(missing_ok=True)

    try:
        if auftrag_dir.exists():
            a = Auftrag(auftrag_dir)
            if _sha256(Path(args.input)) != a.m["sha256"]:
                sys.exit(f"\n{auftrag_dir} gehört zu einer ANDEREN Eingabe (Fingerabdruck).\n"
                         f"Nicht fortgesetzt.  Anderen --output wählen.")
            if args.limit != a.m.get("limit"):
                sys.exit(f"\nDer Auftrag wurde mit --limit {a.m.get('limit')} angelegt, jetzt --limit {args.limit}.\n"
                         f"Nicht fortgesetzt.  Anderen --output wählen.")
            if args.grenze_chf:
                a.setze(grenze_chf=args.grenze_chf)
            fertig = a.pruefen()
            print(f"Locationer {version_text()}")
            if a.m.get("versionen") and a.m["versionen"][-1] != version_text():
                print(f"ACHTUNG: bisher gerechnet mit {a.m['versionen'][-1]}")
            print(f"Fortsetzen: {auftrag_dir.name} — {len(fertig)} von {a.n_pakete} Paketen fertig, "
                  f"bisher {a.kosten_chf():.2f} CHF, Grenze {a.m['grenze_chf']:.2f} CHF")
        else:
            if out_path.exists():
                sys.exit(f"\n{out_path} existiert schon (ohne Auftrag).  Wird nicht überschrieben.\n"
                         f"Anderen --output wählen.")
            df = P._read(args.input)
            if args.limit:
                df = df.head(args.limit)
            print(f"Locationer {version_text()}  ·  {len(df)} records  ·  mode={args.mode}  ·  "
                  f"{workers} parallel à {args.chunk_size}")
            P._spaltenbericht(df)
            bbox = _bbox_fragen()
            print()
            if not Path(args.geo_db).exists():
                print(f"\n⚠  GEO DB nicht gefunden: {args.geo_db}")
                if input("   Trotzdem starten? [y/N] ").strip().lower() != "y":
                    sys.exit("Abgebrochen.")
            grenze = args.grenze_chf or _GRENZE_CHF
            s = schaetzen(df)
            print(f"Schätzung: {s['verschieden']} verschiedene Zeilen, davon {s['neu']} neu "
                  f"→ höchstens etwa {s['chf']:.2f} CHF  (Grenze {grenze:.2f} CHF)")
            if s["chf"] > grenze:
                sys.exit(f"\nDie Schätzung liegt über der Grenze.  Mit --grenze-chf höher setzen.")
            a = Auftrag.anlegen(auftrag_dir, args.input, grenze_chf=grenze, bbox=bbox, chunk=args.chunk_size,
                                limit=args.limit, name=Path(args.input).name)

        if args.mode == "human":
            print(f"  {'Title':<49}  {'Country/City':<28}  Score  Source")
            print(f"  {'─'*49}  {'─'*28}  {'─'*5}  {'─'*12}")

        def melden(f):
            print(f"   → {f['erledigt']}/{f['zeilen']} fertig  |  {f['chf']:.2f} CHF"
                  + (f"  |  Hochrechnung {f['hochrechnung_chf']:.2f} CHF" if f["hochrechnung_chf"] else ""))

        r = a.rechnen(workers=workers, mode=args.mode, melden=melden)
    except AuftragFehler as e:
        sys.exit(f"\nAuftrag nicht fortsetzbar: {e}")

    st = getattr(a, "statistik", {})
    print(f"\n{'─'*70}")
    print(f"Auftrag:         {auftrag_dir}")
    print(f"Haiku calls (Phase 1a+1b): {st.get('haiku', 0)}")
    print(f"Nominatim calls:           {st.get('nominatim', 0)}")
    print(f"Pictomap-Referenz Treffer: {st.get('pictomap', 0)}")
    print(f"Kosten bisher:             {a.kosten_chf():.2f} CHF  ({a.m['usd']:.3f} USD)")
    if st.get("verdacht"):
        v = st["verdacht"]
        print(f"⚠  Zuordnung pruefen: {len(v)} Zeilen, deren Titel kein Wort mit "
              f"ihrer Eingabe teilt (oft Uebersetzungen): {v[:30]}{' …' if len(v) > 30 else ''}")
    else:
        print("Zuordnung:                 alle Zeilen passen zu ihrer Eingabe")
    if st.get("nachgefasst_kandidaten") is not None and os.getenv("NACHFASSEN", "1") != "0":
        print(f"Nachgefasst (Schritt 2):   {st.get('nachgefasst_kandidaten', 0)} ohne Koordinate → "
              f"{st.get('nachgefasst_bestimmbar', 0)} bestimmbar → {st.get('nachgefasst_gefunden', 0)} gefunden")

    if r["zustand"] != "fertig":
        sys.exit(f"\n⏸  ANGEHALTEN: {r['grund']}\n"
                 f"   Fortsetzen mit demselben Befehl (bei Kostengrenze: --grenze-chf höher).")

    tmp = out_path.with_name(out_path.name + ".tmp")
    shutil.copyfile(auftrag_dir / "ausgabe.csv", tmp)
    os.replace(tmp, out_path)
    print(f"Output:          {out_path}")
    out_df = pd.read_csv(out_path, dtype=str, keep_default_na=False)
    _statistik(out_df, a.unbekannt_zaehlen(a.eingabe()))
    print()

    try:
        from locationer.map import main as map_main
        _argv = sys.argv
        sys.argv = ["locationer.map", str(out_path)]
        map_main()
        sys.argv = _argv
    except Exception as e:
        print(f"Karte nicht generiert: {e}")

    if ist_testdatei:
        from datetime import datetime
        from locationer.metrics import measure, to_row, update_log, print_table
        out_df = pd.read_csv(out_path)
        m   = measure(out_df, st.get("nominatim", 0))
        row = to_row(m, datetime.now().strftime("%Y-%m-%d %H:%M"))
        update_log(row)
        print_table(row)
        n = len(out_df)
        for col in ("Periode", "Urheber", "Technik"):
            found = out_df[col].notna().sum() if col in out_df.columns else 0
            examples = out_df[col].dropna().unique()[:3].tolist() if found else []
            ex_str = "  z.B.: " + ", ".join(f'"{e}"' for e in examples) if examples else ""
            print(f"  {col:<8} {found:3d}/{n} ({found/n*100:4.0f}%){ex_str}")


if __name__ == "__main__":
    main()
