"""
Testmetrik für TestFile.csv.

TESTLOG.md: CSV-Tabelle, neueste Zeile zuoberst (unter Header).
Terminal: letzte 3 Zeilen + aktuelle, eine Zeile pro Messung.

Direkt: python -m v3.metrics
"""

import math
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

TESTFILE = "v4/TestFile.csv"
GEO_OUT  = "v4/TestFile_geo.csv"
TESTLOG  = "TESTLOG.md"

HEADER = "| Timestamp        | <100m | 100-1km | 1-10km | >10km | kein | FB%  | S5%  | Ext   | n/a→∅ |"
SEP    = "|------------------|-------|---------|--------|-------|------|------|------|--------|-------|"


def _try_float(v):
    try:    return float(v)
    except: return None


def _hav(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2-lat1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def run_pipeline():
    r = subprocess.run([sys.executable, "-m", "v3", TESTFILE], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("Google requests:"):
            return int(line.split(":")[1].strip())
    return 0


def measure(out: pd.DataFrame, google: int) -> dict:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "mac_roman"):
        try:
            src = pd.read_csv(TESTFILE, sep=";", quotechar='"', encoding=enc)
            break
        except (UnicodeDecodeError, Exception):
            continue
    else:
        src = pd.read_csv(TESTFILE, sep=";", quotechar='"', encoding="latin-1", on_bad_lines="skip")
    dists, no_ok, no_bad = [], 0, 0
    for i in range(len(src)):
        lt = _try_float(src.iloc[i].get("lat_true"))
        ln = _try_float(src.iloc[i].get("lon_true"))
        has = i < len(out) and pd.notna(out.iloc[i].get("Lat"))
        if lt is not None and ln is not None:
            dists.append(_hav(float(out.iloc[i]["Lat"]), float(out.iloc[i]["Lon"]), lt, ln) if has else None)
        else:
            if has: no_bad += 1
            else:   no_ok  += 1
    t = len(dists)
    b = dict.fromkeys(["<100m","100-1km","1-10km",">10km","kein"], 0)
    for d in dists:
        if   d is None: b["kein"]    += 1
        elif d <  0.1:  b["<100m"]   += 1
        elif d <  1.0:  b["100-1km"] += 1
        elif d < 10.0:  b["1-10km"]  += 1
        else:           b[">10km"]   += 1
    n = len(out)
    return dict(t=t, n=n, google=google, no_ok=no_ok, no_bad=no_bad,
                fb=round((out["Fallback"]==True).sum()/n*100),
                s5=round((out["Coord-Quality-Score"]==5).sum()/n*100), **b)


def to_row(m: dict, ts: str) -> str:
    t, n = m["t"], m["n"]
    p = lambda x: f"{x*100//t:3.0f}%" if t else "  — "
    na = f"{m['no_ok']}/{m['no_ok']+m['no_bad']}" + (f"⚠{m['no_bad']}" if m["no_bad"] else "")
    return (f"| {ts} "
            f"| {m['<100m']:5d} "
            f"| {m['100-1km']:7d} "
            f"| {m['1-10km']:6d} "
            f"| {m['>10km']:5d} "
            f"| {m['kein']:4d} "
            f"| {m['fb']:3d}% "
            f"| {m['s5']:3d}% "
            f"| {m['google']:6d} "
            f"| {na:5s} |")


def update_log(row: str):
    log = Path(TESTLOG)
    lines = log.read_text().splitlines() if log.exists() else []
    # Strip existing header/sep if present
    data = [l for l in lines if l and not l.startswith("|---") and l != HEADER]
    new_lines = [HEADER, SEP] + [row] + data
    log.write_text("\n".join(new_lines) + "\n")


def print_table(current_row: str):
    log = Path(TESTLOG)
    data = []
    if log.exists():
        data = [l for l in log.read_text().splitlines()
                if l and not l.startswith("|---") and l != HEADER]
    # last 3 historical (data[0] is newest = just written = current, so skip it for "past")
    past = data[1:4]  # rows 1-3 are the previous entries

    print("\nTESTMETRIK")
    print(HEADER)
    print(SEP)
    for row in reversed(past):  # oldest first
        print(row)
    print(f"▶{current_row[1:]}")  # replace leading | with ▶


def main():
    print("Running pipeline on TestFile.csv…")
    google = run_pipeline()
    out = pd.read_csv(GEO_OUT)
    m   = measure(out, google)
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M")
    row = to_row(m, ts)
    update_log(row)
    print_table(row)


if __name__ == "__main__":
    main()
