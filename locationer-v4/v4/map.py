"""Generate a Leaflet map from a _geo.csv for visual QA.

Usage:
    python -m v4.map path/to/output_geo.csv
"""
import argparse
import webbrowser
from pathlib import Path

import folium
import pandas as pd
from folium.plugins import MarkerCluster

_SCORE_COLOR = {5: "green", 4: "lightgreen", 3: "orange", 2: "lightred", 0: "red"}
_SCORE_LABEL = {
    5: "●●●●● Score 5 (GEO DB / TGN präzis)",
    4: "●●●●○ Score 4 (Nominatim präzis)",
    3: "●●●○○ Score 3 (Stadtzentrum GEO DB)",
    2: "●●○○○ Score 2 (Stadtzentrum Nominatim)",
    0: "○○○○○ Score 0 (nicht gefunden)",
}


def _popup(row: pd.Series, csv_line: int) -> str:
    score = int(row.get("Coord-Quality-Score") or 0)
    lines = [
        f"<b>{row.get('Title', '') or ''}</b>",
        f"{row.get('City', '') or ''} · {row.get('Country', '') or ''}",
        f"{_SCORE_LABEL.get(score, str(score))}",
    ]
    if row.get("Fallback"):
        lines.append("<i>Fallback (Stadtzentrum)</i>")
    if "Periode" in row.index and pd.notna(row.get("Periode")):
        lines.append(f"Periode: {row['Periode']}")
    if "Urheber" in row.index and pd.notna(row.get("Urheber")):
        lines.append(f"Urheber: {row['Urheber']}")
    if "Deviation_km" in row.index and pd.notna(row.get("Deviation_km")):
        dev = float(row["Deviation_km"])
        color = "red" if dev > 10 else "orange" if dev > 1 else "green"
        lines.append(f'<span style="color:{color}"><b>Δ {dev:.1f} km</b></span>')
    lines.append(f"<small>Lat {row.get('Lat', ''):.5f}  Lon {row.get('Lon', ''):.5f}</small>")
    lines.append(f"<small style='color:#888'>CSV-Zeile {csv_line}</small>")
    return "<br>".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Locationer V4 — QA map")
    ap.add_argument("input", help="*_geo.csv from locationer")
    ap.add_argument("--no-open", action="store_true", help="Do not open browser automatically")
    args = ap.parse_args()

    df = pd.read_csv(args.input)
    mapped = df[df["Lat"].notna() & df["Lon"].notna()].copy()
    skipped = len(df) - len(mapped)

    if mapped.empty:
        print("Keine Koordinaten in der Datei gefunden.")
        return

    center = [mapped["Lat"].mean(), mapped["Lon"].mean()]
    m = folium.Map(location=center, zoom_start=7, tiles="OpenStreetMap")

    # Legend
    legend_html = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;
                padding:10px 14px;border:1px solid #ccc;border-radius:6px;font-size:13px;">
      <b>Score</b><br>
      <span style="color:green">●</span> 5 · GEO DB / TGN präzis<br>
      <span style="color:#8BC34A">●</span> 4 · Nominatim präzis<br>
      <span style="color:orange">●</span> 3 · Stadtzentrum GEO DB<br>
      <span style="color:#E57373">●</span> 2 · Stadtzentrum Nominatim<br>
      <span style="color:red">●</span> 0 · nicht gefunden
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    cluster = MarkerCluster().add_to(m)

    for idx, row in mapped.iterrows():
        csv_line = idx + 2  # row 1 = header, first data row = 2
        score = int(row.get("Coord-Quality-Score") or 0)
        color = _SCORE_COLOR.get(score, "gray")
        folium.Marker(
            location=[row["Lat"], row["Lon"]],
            popup=folium.Popup(_popup(row, csv_line), max_width=320),
            tooltip=str(row.get("Title", "") or "")[:60],
            icon=folium.Icon(color=color),
        ).add_to(cluster)

    out_path = str(Path(args.input).with_suffix("")) + "_map.html"
    m.save(out_path)
    n = len(mapped)
    print(f"Map: {n} Objekte  |  {skipped} ohne Koordinaten  →  {out_path}")
    if not args.no_open:
        webbrowser.open(out_path)


if __name__ == "__main__":
    main()
