"""
Manage manual location overrides (explicit list).

Stored in explicit_list/explicit.sqlite — version-controllable, separate from
the auto-generated cache (cache/locationer.sqlite).

Pattern format:  location|city|country   (* = wildcard for any field)

Examples:
  "Cresta||Switzerland"    — location=Cresta, no city, country=CH (exact)
  "*|Avers|Switzerland"    — any record with city=Avers in CH
  "Cresta|*|Switzerland"   — any Cresta in CH regardless of city

Usage:
  python -m v3.overrides list
  python -m v3.overrides add "Cresta||Switzerland" 46.453581 9.542744 "Avers GR" "Notiz"
  python -m v3.overrides remove "Cresta||Switzerland"
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from .explicit_store import ExplicitStore

OVERRIDES_PATH = os.getenv("OVERRIDES_PATH", "explicit_list/explicit.sqlite")


def cmd_list(store: ExplicitStore):
    rows = store.list_all()
    if not rows:
        print("Keine Overrides gespeichert.")
        return
    print(f"{'Pattern':<35} {'Lat':>10} {'Lon':>10} Sc {'Name':<25} Notiz")
    print("─" * 115)
    for pat, lat, lon, score, name, note in rows:
        print(f"{pat:<35} {lat:>10.5f} {lon:>10.5f}  {score}  {(name or ''):25s} {note or ''}")


def main():
    ap = argparse.ArgumentParser(description="Manage Locationer location overrides")
    sub = ap.add_subparsers(dest="cmd")

    # ── geo overrides (Phase 2 coordinates) ──────────────────────────────────
    sub.add_parser("list", help="List geo overrides")

    p_add = sub.add_parser("add", help="Add/update a geo override")
    p_add.add_argument("pattern", help="location|city|country  (* = wildcard)")
    p_add.add_argument("lat", type=float)
    p_add.add_argument("lon", type=float)
    p_add.add_argument("match_name", nargs="?", default="")
    p_add.add_argument("note", nargs="?", default="")
    p_add.add_argument("--score", type=int, default=3)

    p_rm = sub.add_parser("remove", help="Remove a geo override")
    p_rm.add_argument("pattern")

    # ── norm overrides (Phase 1 city/country correction) ─────────────────────
    sub.add_parser("norm-list", help="List norm overrides")

    p_nadd = sub.add_parser("norm-add",
        help="Add a Phase-1 city/country correction (substring match on raw input)")
    p_nadd.add_argument("pattern", help="substring to match in raw row text (case-insensitive)")
    p_nadd.add_argument("--city",    default="", help="Set city to this value")
    p_nadd.add_argument("--country", default="", help="Set country to this value")
    p_nadd.add_argument("note", nargs="?", default="")

    p_nrm = sub.add_parser("norm-remove", help="Remove a norm override")
    p_nrm.add_argument("pattern")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        sys.exit(1)

    store = ExplicitStore(OVERRIDES_PATH)

    if args.cmd == "list":
        cmd_list(store)
    elif args.cmd == "add":
        store.add(args.pattern, args.lat, args.lon, args.score, args.match_name, args.note)
        print(f"Gespeichert: {args.pattern!r} → ({args.lat}, {args.lon})  [{args.match_name}]")
    elif args.cmd == "remove":
        print("Gelöscht." if store.remove(args.pattern) else "Nicht gefunden.")

    elif args.cmd == "norm-list":
        rows = store.list_norm()
        if not rows:
            print("Keine Norm-Overrides.")
        else:
            print(f"{'Pattern':<30} {'city':<20} {'country':<15} Notiz")
            print("─" * 90)
            for pat, city, country, note in rows:
                print(f"{pat:<30} {city or '':20s} {country or '':15s} {note or ''}")
    elif args.cmd == "norm-add":
        store.add_norm(args.pattern, args.city, args.country, args.note)
        print(f"Norm-Override: {args.pattern!r} → city={args.city!r} country={args.country!r}")
    elif args.cmd == "norm-remove":
        print("Gelöscht." if store.remove_norm(args.pattern) else "Nicht gefunden.")

    store.close()


if __name__ == "__main__":
    main()
