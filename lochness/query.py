"""Queries: Apple's translations of an English string, full-text search, and String Catalog checks."""

import json
import sys
from collections import defaultdict

from . import db, parse

# When Apple has no table for a code, try these instead.
LANG_FALLBACKS = {"pt-BR": ["pt"], "pt": ["pt-BR"], "es-419": ["es-MX", "es-US"], "nb": ["no"], "zh-HK": ["zh-Hant"]}


def lang_ids(conn):
    return dict(conn.execute("SELECT code, id FROM langs"))


def translations(conn, text, langs=None, platform=None, nocase=False, variant=None):
    """Apple's translations of an English source text.

    Returns {lang: [(translation, n_bundles, [bundle names], {platforms})]}, most common first,
    plus the list of (bundle, platform, table, key, variant) places the English text was found.
    """
    ids = lang_ids(conn)
    src_ids = [ids[c] for c in db.SOURCE_LANGS if c in ids]
    if nocase:
        words = " ".join(f'"{w}"' for w in text.replace('"', "").split())
        text_ids = [i for i, t in conn.execute("SELECT rowid, text FROM source_fts WHERE source_fts MATCH ?", (words,))
                    if t.casefold() == text.casefold()] if words else []
    else:
        text_ids = [i for i, t in conn.execute("SELECT id, text FROM texts WHERE h = ?", (db.text_hash(text),))
                    if t == text]
    if not text_ids or not src_ids:
        return {}, []
    q = f"""
        SELECT DISTINCT e.table_id, e.key_id, e.variant_id, b.name, b.platform, t.name, k.key, v.variant
        FROM entries e
        JOIN tables t ON t.id = e.table_id JOIN bundles b ON b.id = t.bundle_id
        JOIN keys k ON k.id = e.key_id JOIN variants v ON v.id = e.variant_id
        WHERE e.lang_id IN ({','.join('?' * len(src_ids))}) AND e.text_id IN ({','.join('?' * len(text_ids))})
    """
    places = conn.execute(q, src_ids + text_ids).fetchall()
    if platform:
        places = [p for p in places if p[4].lower() == platform.lower()]
    if variant is not None:
        places = [p for p in places if p[7] == variant]
    want = None
    if langs:
        want = {}
        for code in langs:
            for c in [parse.norm_lang(code)] + LANG_FALLBACKS.get(parse.norm_lang(code), []):
                if c in ids:
                    want[ids[c]] = code
                    break
    code_by_id = {i: c for c, i in ids.items()}
    agg = defaultdict(lambda: defaultdict(lambda: [set(), set()]))
    for table_id, key_id, variant_id, bname, bplat, *_ in places:
        for lang_id, tr in conn.execute(
                "SELECT e.lang_id, x.text FROM entries e JOIN texts x ON x.id = e.text_id "
                "WHERE e.table_id=? AND e.key_id=? AND e.variant_id=?", (table_id, key_id, variant_id)):
            if want is not None and lang_id not in want:
                continue
            if lang_id in src_ids and want is None:
                continue
            code = want[lang_id] if want else code_by_id[lang_id]
            agg[code][tr][0].add(bname)
            agg[code][tr][1].add(bplat)
    out = {}
    for code, trs in agg.items():
        out[code] = sorted(((tr, len(b), sorted(b), p) for tr, (b, p) in trs.items()), key=lambda r: -r[1])
    order = langs or sorted(out)
    out = {c: out[c] for c in order if c in out}
    return out, [(p[3], p[4], p[5], p[6], p[7]) for p in places]


def cmd_lookup(args):
    conn = db.connect(args.db, readonly=True)
    langs = args.lang.split(",") if args.lang else None
    res, places = translations(conn, args.text, langs, args.platform, args.nocase)
    if args.json:
        json.dump({"text": args.text, "found_in": places,
                   "translations": {c: [{"text": t, "bundles": n, "examples": b[:5], "platforms": sorted(p)}
                                        for t, n, b, p in rows] for c, rows in res.items()}},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
        return
    if not places:
        print(f'No Apple string reads exactly "{args.text}". Try: python3 -m lochness search "{args.text}"')
        return
    bundles = sorted({f"{p[0]} [{p[1]}]" for p in places})
    print(f'"{args.text}" appears in {len(bundles)} Apple bundles: {", ".join(bundles[:6])}{" …" if len(bundles) > 6 else ""}')
    variants = sorted({p[4] for p in places if p[4]})
    if variants:
        print(f"  (incl. plural/device variants: {', '.join(variants[:5])})")
    for code, rows in res.items():
        for i, (tr, n, b, plats) in enumerate(rows[: args.top]):
            label = code if i == 0 else ""
            ex = ", ".join(b[:3]) + (" …" if len(b) > 3 else "")
            print(f"  {label:<8} {tr}   ×{n}  {'/'.join(sorted(plats))}  ({ex})")


def cmd_search(args):
    conn = db.connect(args.db, readonly=True)
    ids = lang_ids(conn)
    src_ids = [ids[c] for c in db.SOURCE_LANGS if c in ids]
    q = " ".join(f'"{w}"' for w in args.query.replace('"', "").split())
    rows = conn.execute(
        f"""SELECT f.rowid, f.text, (SELECT COUNT(DISTINCT t.bundle_id) FROM entries e JOIN tables t ON t.id=e.table_id
                WHERE e.lang_id IN ({','.join('?' * len(src_ids))}) AND e.text_id=f.rowid) AS n
            FROM source_fts f WHERE source_fts MATCH ? ORDER BY n DESC, length(f.text) LIMIT ?""",
        src_ids + [q, args.limit]).fetchall()
    for _, text, n in rows:
        print(f"{n:>5}  {text}")


def _xc_value(loc):
    unit = (loc or {}).get("stringUnit")
    return unit.get("value") if unit else None


def load_catalog(path):
    """Read a .xcstrings catalog, or a .strings table plus its sibling lproj tables, as xcstrings-shaped JSON."""
    if path.endswith(".xcstrings"):
        return json.load(open(path, encoding="utf-8"))
    import os
    name = os.path.basename(path)
    lproj_dir = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    strings = defaultdict(lambda: {"localizations": {}})
    for d in sorted(os.listdir(lproj_dir)):
        f = os.path.join(lproj_dir, d, name)
        if not (d.endswith(".lproj") and os.path.isfile(f)):
            continue
        code = parse.norm_lang(d[:-6])
        for key, value in parse.load_plist(f).items():
            if isinstance(value, str):
                strings[key]["localizations"][code] = {"stringUnit": {"value": value}}
    return {"sourceLanguage": "en", "strings": strings}


def cmd_check(args):
    """Compare a catalog's translations with Apple's for identical English strings."""
    conn = db.connect(args.db, readonly=True)
    cat = load_catalog(args.xcstrings)
    src_lang = cat.get("sourceLanguage", "en")
    langs = args.lang.split(",") if args.lang else None
    agree = differ = unmatched = 0
    report = []
    for key, entry in cat.get("strings", {}).items():
        if entry.get("shouldTranslate") is False:
            continue
        locs = entry.get("localizations", {})
        source = _xc_value(locs.get(src_lang)) or key
        if not source.strip():
            continue
        targets = langs or [c for c in locs if c != src_lang]
        res, places = translations(conn, source, targets, args.platform, variant="")
        if not places:
            unmatched += 1
            continue
        for code in targets:
            ours = _xc_value(locs.get(code))
            apple = res.get(code)
            if not apple:
                continue
            apple_texts = {t for t, *_ in apple}
            top, n, b, _ = apple[0]
            if ours is None:
                report.append(("missing", code, key, source, None, top, n, b))
            elif ours in apple_texts:
                agree += 1
            else:
                differ += 1
                report.append(("differs", code, key, source, ours, top, n, b))
    if args.json:
        json.dump([dict(zip(("status", "lang", "key", "english", "ours", "apple", "apple_bundles", "examples"), r[:7] + (r[7][:5],)))
                   for r in report], sys.stdout, ensure_ascii=False, indent=2)
        print()
        return
    seen = set()
    for status, code, key, source, ours, top, n, b in sorted(report, key=lambda r: (r[1], r[0], r[3])):
        if (code, source, ours) in seen:
            continue
        seen.add((code, source, ours))
        print(f"[{code}] {status}: \"{source}\"")
        if ours is not None:
            print(f"    ours : {ours}")
        print(f"    apple: {top}   ×{n} ({', '.join(b[:3])})")
    print(f"\n{agree} agree with Apple, {differ} differ, "
          f"{sum(1 for r in report if r[0] == 'missing')} missing where Apple has one; "
          f"{unmatched} source strings have no exact Apple match.")


def cmd_stats(args):
    conn = db.connect(args.db, readonly=True)
    for (label, scanned) in conn.execute("SELECT label, scanned_at FROM sources ORDER BY id"):
        print(f"source  {label}  scanned {scanned}")
    for name in ("bundles", "tables", "keys", "texts", "langs", "entries"):
        print(f"{name:<8}{conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]:>14,}")
    if args.langs:
        for code, n in conn.execute(
                "SELECT l.code, COUNT(*) FROM entries e JOIN langs l ON l.id=e.lang_id GROUP BY l.code ORDER BY 2 DESC"):
            print(f"  {code:<10}{n:>12,}")
