import argparse

from . import db, query, scan


def main():
    p = argparse.ArgumentParser(prog="lochness", description="Apple's own UI translations, from this Mac, as a local reference database.")
    p.add_argument("--db", default=db.DEFAULT_DB, help=f"SQLite path (default {db.DEFAULT_DB}, or $LOCHNESS_DB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="scan macOS and the latest installed iOS simulator runtime")
    s.add_argument("--only", action="append", help="only sources whose label contains this (e.g. macOS, iOS)")
    s.add_argument("--rescan", action="store_true", help="scan sources again even if already in the database")
    s.add_argument("--workers", type=int)
    s.add_argument("--list", action="store_true", help="list the sources that would be scanned and exit")
    s.add_argument("--force", action="store_true", help="scan even if free disk looks too low")

    l = sub.add_parser("lookup", help="Apple's translations of an exact English string")
    l.add_argument("text")
    l.add_argument("-l", "--lang", help="comma-separated codes, e.g. ar,he,fr (default: all)")
    l.add_argument("-p", "--platform", help="macOS or iOS")
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

    g = sub.add_parser("suggest", help="Apple's translations for every string in a catalog, as JSON for Claude")
    g.add_argument("catalog", help="a .xcstrings file, one lproj's .strings file, or - for English lines on stdin")
    g.add_argument("-l", "--lang", help="comma-separated codes (default: every language in the catalog)")
    g.add_argument("-p", "--platform", help="macOS or iOS: match Apple's wording on that platform")
    g.add_argument("--only-missing", action="store_true", help="only languages a string has no translation for yet")
    g.add_argument("--min-bundles", type=int, default=3,
                   help="a term must appear in at least this many Apple bundles (default 3)")
    g.add_argument("--max-words", type=int, default=6, help="longest term to look for, in words (default 6)")
    g.add_argument("-o", "--out", help="write the JSON here instead of stdout")

    ss = sub.add_parser("status", help="where the database is, what is in it, and what is left to scan")
    ss.add_argument("--json", action="store_true")

    pg = sub.add_parser("purge", help="delete the database (rebuild it with scan)")
    pg.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")

    st = sub.add_parser("stats", help="what is in the database")
    st.add_argument("--langs", action="store_true")

    args = p.parse_args()
    if args.cmd == "scan":
        if args.list:
            sources = scan.discover_sources()
            for src in sources:
                print(src["label"], "\n   ", "\n    ".join(src["dirs"]))
            if not scan.has_ios(sources):
                print(scan.NO_SIMULATOR)
            return
        scan.scan(args.db, args.only, args.rescan, args.workers, args.force)
    else:
        getattr(query, f"cmd_{args.cmd}")(args)


if __name__ == "__main__":
    main()
