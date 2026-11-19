import argparse

from . import db, query, scan


def main():
    p = argparse.ArgumentParser(prog="lochness", description="Apple's own localizations as a reference database.")
    p.add_argument("--db", default=db.DEFAULT_DB, help=f"SQLite path (default {db.DEFAULT_DB}, or $LOCHNESS_DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="scan macOS and installed simulator runtimes")
    s.add_argument("--only", action="append", help="only sources whose label contains this (e.g. macOS, iOS, watchOS)")
    s.add_argument("--rescan", action="store_true", help="scan sources again even if already in the database")
    s.add_argument("--workers", type=int)
    s.add_argument("--list", action="store_true", help="list the sources that would be scanned and exit")

    l = sub.add_parser("lookup", help="Apple's translations of an exact English string")
    l.add_argument("text")
    l.add_argument("-l", "--lang", help="comma-separated codes, e.g. ar,he,fr (default: all)")
    l.add_argument("-p", "--platform", help="macOS, iOS or watchOS")
    l.add_argument("-i", "--nocase", action="store_true")
    l.add_argument("-n", "--top", type=int, default=3, help="translations to show per language")
    l.add_argument("--json", action="store_true")

    f = sub.add_parser("search", help="full-text search over Apple's English strings")
    f.add_argument("query")
    f.add_argument("--limit", type=int, default=30)

    c = sub.add_parser("check", help="compare a .xcstrings catalog (or any lproj .strings table) with Apple's translations")
    c.add_argument("xcstrings", help="a .xcstrings file, or one lproj's .strings file (its sibling lprojs are read too)")
    c.add_argument("-l", "--lang", help="comma-separated codes (default: every language in the catalog)")
    c.add_argument("-p", "--platform")
    c.add_argument("--json", action="store_true")

    st = sub.add_parser("stats", help="what is in the database")
    st.add_argument("--langs", action="store_true")

    args = p.parse_args()
    if args.cmd == "scan":
        if args.list:
            for src in scan.discover_sources():
                print(src["label"], "\n   ", "\n    ".join(src["dirs"]))
            return
        scan.scan(args.db, args.only, args.rescan, args.workers)
    else:
        getattr(query, f"cmd_{args.cmd}")(args)


if __name__ == "__main__":
    main()
