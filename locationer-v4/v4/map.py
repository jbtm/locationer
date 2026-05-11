"""Generate a Leaflet QA map from a _geo.csv.

Usage:
    python -m v4.map path/to/output_geo.csv [--no-open]
"""
import argparse
import json
import re
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

_IS_URL = re.compile(r"^https?://", re.I)
_IS_IMG = re.compile(r"\.(jpe?g|png|webp|gif|tiff?)(\?.*)?$", re.I)


def _detect_link_cols(df: pd.DataFrame) -> list[str]:
    """Return column names that contain URLs in most rows."""
    cols = []
    sample = df.dropna(how="all").head(20)
    for col in sample.columns:
        vals = sample[col].dropna().astype(str)
        if vals.empty:
            continue
        if vals.apply(lambda v: bool(_IS_URL.match(v.strip()))).mean() > 0.5:
            cols.append(col)
    return cols


def _popup(row: pd.Series, csv_line: int, link_cols: list[str]) -> str:
    score = int(row.get("Coord-Quality-Score") or 0)
    parts = [
        f"<b>{str(row.get('Title', '') or '')}</b>",
        f"{str(row.get('City', '') or '')} · {str(row.get('Country', '') or '')}",
        _SCORE_LABEL.get(score, str(score)),
    ]
    if row.get("Fallback"):
        parts.append("<i>Fallback (Stadtzentrum)</i>")
    for field, label in [("Periode", "Periode"), ("Urheber", "Urheber")]:
        if field in row.index and pd.notna(row.get(field)):
            parts.append(f"{label}: {row[field]}")
    if "Deviation_km" in row.index and pd.notna(row.get("Deviation_km")):
        dev = float(row["Deviation_km"])
        col = "red" if dev > 10 else "orange" if dev > 1 else "green"
        parts.append(f'<span style="color:{col}"><b>Δ {dev:.1f} km</b></span>')
    parts.append(
        f"<small>Lat {float(row.get('Lat', 0)):.5f}  "
        f"Lon {float(row.get('Lon', 0)):.5f}</small>"
    )
    parts.append(f"<small style='color:#888'>CSV-Zeile {csv_line}</small>")

    # Photo / URL links
    for col in link_cols:
        val = str(row.get(col, "") or "").strip()
        if not val or not _IS_URL.match(val):
            continue
        label = col
        if _IS_IMG.search(val):
            parts.append(
                f'<a href="{val}" target="_blank">'
                f'<img src="{val}" style="max-width:100%;margin-top:4px;border-radius:3px"></a>'
            )
        else:
            parts.append(f'<a href="{val}" target="_blank">🔗 {label}</a>')

    # Annotation controls
    parts.append(
        f'<div class="anno">'
        f'<label><input type="checkbox" id="cbf{csv_line}" '
        f'onchange="setAnno({csv_line},\'fehler\',this.checked)"> ❌ Fehler</label>'
        f'<label style="margin-left:8px"><input type="checkbox" id="cbw{csv_line}" '
        f'onchange="setAnno({csv_line},\'wow\',this.checked)"> ⭐ Wow</label><br>'
        f'<input type="text" id="txt{csv_line}" placeholder="Kommentar…" '
        f'oninput="setAnno({csv_line},\'comment\',this.value)">'
        f'</div>'
    )
    return "<br>".join(parts)


_CSS = """<style>
.anno { margin-top:8px; border-top:1px solid #ddd; padding-top:6px; font-size:12px; }
.anno label { cursor:pointer; }
.anno input[type=text] {
  width:100%; margin-top:5px; font-size:12px;
  box-sizing:border-box; padding:3px 5px;
  border:1px solid #ccc; border-radius:3px;
}
#qa-bar {
  position:fixed; bottom:20px; right:20px; z-index:9999;
  background:white; border:1px solid #aaa; border-radius:8px;
  padding:12px 16px; box-shadow:2px 4px 12px rgba(0,0,0,.2);
  font-size:13px; min-width:190px;
}
#qa-bar button {
  margin-top:8px; width:100%; padding:6px 10px; cursor:pointer;
  border:1px solid #888; border-radius:4px; background:#f5f5f5; font-size:12px;
}
#qa-bar button:hover { background:#e0e0e0; }
</style>"""

_JS_TEMPLATE = """
<div id="qa-bar">
  <b>QA-Annotationen</b><br>
  <span id="qa-n">0 annotiert</span>
  <button onclick="downloadAnno()">⬇ CSV herunterladen</button>
</div>
<script>
var A={}, MD=MARKERDATA;
function setAnno(line,type,val){
  if(!A[line])A[line]={fehler:false,wow:false,comment:''};
  A[line][type]=val;
  var a=A[line];
  if(!a.fehler&&!a.wow&&!a.comment)delete A[line];
  document.getElementById('qa-n').textContent=Object.keys(A).length+' annotiert';
}
function downloadAnno(){
  if(!Object.keys(A).length){alert('Keine Annotationen vorhanden.');return;}
  var reviewer=prompt('Dein Name:','')||'';
  var rows=['csv_zeile,title,lat,lon,fehler,wow,kommentar,reviewer,timestamp'];
  for(var l in A){
    var a=A[l],d=MD[l]||{};
    rows.push([l,'"'+(d.t||'').replace(/"/g,'""')+'"',
      d.la||'',d.lo||'',
      a.fehler?1:0,a.wow?1:0,
      '"'+a.comment.replace(/"/g,'""')+'"',
      '"'+reviewer.replace(/"/g,'""')+'"',
      new Date().toISOString()].join(','));
  }
  var b=new Blob([rows.join('\n')],{type:'text/csv;charset=utf-8;'});
  var u=URL.createObjectURL(b);
  var el=document.createElement('a');
  el.href=u;
  el.download='annotations'+(reviewer?'_'+reviewer:'')+'_'+new Date().toISOString().slice(0,10)+'.csv';
  document.body.appendChild(el);el.click();
  document.body.removeChild(el);URL.revokeObjectURL(u);
}
</script>
"""


def main():
    ap = argparse.ArgumentParser(description="Locationer V4 — QA map")
    ap.add_argument("input", help="*_geo.csv from locationer")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.input, on_bad_lines="skip")
    mapped = df[df["Lat"].notna() & df["Lon"].notna()].copy()
    skipped = len(df) - len(mapped)

    if mapped.empty:
        print("Keine Koordinaten gefunden.")
        return

    link_cols = _detect_link_cols(df)
    if link_cols:
        print(f"Link-Spalten erkannt: {link_cols}")

    center = [mapped["Lat"].mean(), mapped["Lon"].mean()]
    m = folium.Map(location=center, zoom_start=7, tiles="OpenStreetMap")

    m.get_root().html.add_child(folium.Element("""
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;
                padding:10px 14px;border:1px solid #ccc;border-radius:6px;font-size:13px;">
      <b>Score</b><br>
      <span style="color:green">●</span> 5 · GEO DB / TGN präzis<br>
      <span style="color:#8BC34A">●</span> 4 · Nominatim präzis<br>
      <span style="color:orange">●</span> 3 · Stadtzentrum GEO DB<br>
      <span style="color:#E57373">●</span> 2 · Stadtzentrum Nominatim<br>
      <span style="color:red">●</span> 0 · nicht gefunden
    </div>"""))

    cluster = MarkerCluster().add_to(m)
    marker_data: dict[str, dict] = {}

    for idx, row in mapped.iterrows():
        csv_line = idx + 2
        score = int(row.get("Coord-Quality-Score") or 0)
        color = _SCORE_COLOR.get(score, "gray")
        title = str(row.get("Title", "") or "")
        marker_data[str(csv_line)] = {
            "t": title[:80],
            "la": round(float(row["Lat"]), 5),
            "lo": round(float(row["Lon"]), 5),
        }
        folium.Marker(
            location=[row["Lat"], row["Lon"]],
            popup=folium.Popup(_popup(row, csv_line, link_cols), max_width=340),
            tooltip=title[:60],
            icon=folium.Icon(color=color),
        ).add_to(cluster)

    m.get_root().html.add_child(folium.Element(_CSS))
    js = _JS_TEMPLATE.replace("MARKERDATA", json.dumps(marker_data, ensure_ascii=False))
    m.get_root().html.add_child(folium.Element(js))

    out_path = str(Path(args.input).with_suffix("")) + "_map.html"
    m.save(out_path)
    print(f"Map: {len(mapped)} Objekte  |  {skipped} ohne Koordinaten  →  {out_path}")
    if not args.no_open:
        webbrowser.open(out_path)


if __name__ == "__main__":
    main()
