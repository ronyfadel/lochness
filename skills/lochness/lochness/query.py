"""Queries: Apple's translations of an English string, full-text search, and String Catalog checks."""

import json
import os
import re
import sys
from collections import defaultdict

from . import db, parse, scan

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
    # For each requested locale, the Apple codes to read, best first: es-MX, then es-419, then es.
    # Each string uses the best code its table has, since Apple ships es-MX for only a few bundles.
    want = None
    if langs:
        want = {code: [ids[c] for c in parse.lang_candidates(code) if c in ids] for code in langs}
    code_by_id = {i: c for c, i in ids.items()}
    agg = defaultdict(lambda: defaultdict(lambda: [set(), set()]))
    for table_id, key_id, variant_id, bname, bplat, *_ in places:
        by_lang = defaultdict(list)
        for lang_id, tr in conn.execute(
                "SELECT e.lang_id, x.text FROM entries e JOIN texts x ON x.id = e.text_id "
                "WHERE e.table_id=? AND e.key_id=? AND e.variant_id=?", (table_id, key_id, variant_id)):
            by_lang[lang_id].append(tr)
        if want is None:
            picks = [(code_by_id[l], trs) for l, trs in by_lang.items() if l not in src_ids]
        else:
            picks = []
            for code, cands in want.items():
                best = next((l for l in cands if l in by_lang), None)
                if best is not None:
                    picks.append((code, by_lang[best]))
        for code, trs in picks:
            for tr in trs:
                agg[code][tr][0].add(bname)
                agg[code][tr][1].add(bplat)
    out = {}
    for code, trs in agg.items():
        out[code] = sorted(((tr, len(b), sorted(b), p) for tr, (b, p) in trs.items()), key=lambda r: -r[1])
    order = langs or sorted(out)
    out = {c: out[c] for c in order if c in out}
    return out, [(p[3], p[4], p[5], p[6], p[7]) for p in places]


def check_langs(conn, langs):
    """Warn when a database built with --langs can't answer for some of these locales."""
    stored = scan.stored_locales(conn)
    if not stored or not langs:
        return
    have = set(lang_ids(conn))
    missing = [c for c in langs if not any(x in have for x in parse.lang_candidates(c))]
    if missing:
        print(f"note: this database only has {', '.join(stored)} (built with --langs), so there is nothing for "
              f"{', '.join(missing)}. Run `lochness purge` and scan again with those languages added.",
              file=sys.stderr)


def cmd_lookup(args):
    conn = db.connect(args.db, readonly=True)
    langs = args.lang.split(",") if args.lang else None
    check_langs(conn, langs)
    res, places = translations(conn, args.text, langs, args.platform, args.nocase)
    if args.json:
        json.dump({"text": args.text, "found_in": places,
                   "translations": {c: [{"text": t, "bundles": n, "examples": b[:5], "platforms": sorted(p)}
                                        for t, n, b, p in rows] for c, rows in res.items()}},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
        return
    if not places:
        print(f'No Apple string reads exactly "{args.text}". Try: lochness search "{args.text}"')
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


def catalog_value(loc):
    """A localization's text; plural/device variations count as present but have no single value."""
    if not loc:
        return None
    if "variations" in loc:
        return _xc_value(loc) or "<variations>"
    return _xc_value(loc)


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
    check_langs(conn, langs)
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


def cmd_status(args):
    """Where the database is, how big, what is in it, and what could still be scanned."""
    path = args.db
    sources = scan.discover_sources()
    out = {"db": path, "exists": os.path.exists(path)}
    done = set()
    if out["exists"]:
        conn = db.connect(path, readonly=True)
        done = scan.scanned_keys(conn)
        out["size_gb"] = round(db.size_on_disk(path) / 1e9, 2)
        out["languages"] = scan.stored_locales(conn) or "all"
        out["scanned"] = [{"source": label, "scanned_at": at} for label, at in
                          conn.execute("SELECT label, scanned_at FROM sources WHERE scanned_at IS NOT NULL ORDER BY id")]
    pending = scan.pending_sources(sources, done)
    stored = out.get("languages")
    if isinstance(stored, list):
        locales = stored
    elif stored is None and args.langs:
        locales = args.langs.split(",")
    else:
        locales = None
    keep = scan.keep_langs(locales)
    gb, minutes = scan.estimate(pending, {p for p, _ in done}, keep)
    out["not_scanned"] = [s["label"] for s in pending]
    if scan.simulator_warning(sources):
        out["warning"] = scan.simulator_warning(sources)
    if pending:
        out["scan_estimate"] = {"gb": round(gb, 1), "minutes": round(minutes), "free_gb": round(scan.free_gb(path))}
    if args.json:
        json.dump(out, sys.stdout, indent=2)
        print()
        return
    if out["exists"]:
        langs_note = "all languages" if out["languages"] == "all" else "languages: " + ", ".join(out["languages"]) + " + English"
        print(f"database  {path}  ({out['size_gb']:.1f} GB, {langs_note})")
        for s in out["scanned"]:
            print(f"  scanned  {s['source']}  ({s['scanned_at']})")
    else:
        print(f"database  {path}  (not built yet)")
    if pending:
        print("not scanned yet:")
        for label in out["not_scanned"]:
            print(f"  {label}")
        e = out["scan_estimate"]
        print(f"`lochness scan` would take about {e['minutes']} minutes and add about {e['gb']:.1f} GB "
              f"({e['free_gb']} GB free).")
    else:
        print("Up to date with this Mac's macOS and the newest simulator runtime of each platform.")
    if "warning" in out:
        print(out["warning"])


def cmd_purge(args):
    files = db.db_files(args.db)
    if not files:
        print(f"No database at {args.db}.")
        return
    size = sum(os.path.getsize(f) for f in files) / 1e9
    if not args.yes:
        if not sys.stdin.isatty():
            raise SystemExit(f"This deletes {args.db} ({size:.1f} GB). Pass --yes to confirm.")
        if input(f"Delete {args.db} ({size:.1f} GB)? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Kept.")
            return
    for f in files:
        os.remove(f)
    log = os.path.join(os.path.dirname(args.db), "scan.log")
    if os.path.exists(log):
        os.remove(log)
    try:
        os.rmdir(os.path.dirname(args.db))
    except OSError:
        pass
    print(f"Deleted {args.db}, freeing {size:.1f} GB. `lochness scan` rebuilds it.")


# Words that never make a useful glossary term on their own.
STOPWORDS = set("""a an the and or but if of to in on at by for from with as is are was were be been being
it its this that these those you your we our they their he she his her i me my do does did not no yes
can will would should could may might must have has had so than then there here what which who whom
how when where why all any each some more most other such only own same too very just also""".split())

WORD_RE = re.compile(r"[^\W_](?:[\w'’.-]*[^\W_])?|%(?:\d+\$)?[-+ #0]*\d*(?:\.\d+)?(?:ll|l|h|q)?[@dDuUxXoOfFeEgGcCsSpaA]")


def _forms(phrase):
    """The phrase, and the casings Apple's English UI tends to use for it."""
    words = phrase.split(" ")
    sentence = phrase[:1].upper() + phrase[1:]
    title = " ".join(w[:1].upper() + w[1:] if w.lower() not in STOPWORDS or i == 0 else w
                     for i, w in enumerate(words))
    out = []
    for f in (phrase, sentence, title, phrase.lower()):
        if f not in out:
            out.append(f)
    return out


def _summarize(rows):
    total = sum(n for _, n, *_ in rows)
    top, n, b, _ = rows[0]
    d = {"apple": top, "agreement": f"{n}/{total}", "examples": b[:3]}
    if len(rows) > 1:
        d["alternatives"] = [{"text": t, "bundles": k, "examples": bb[:3]} for t, k, bb, _ in rows[1:3]]
    return d


def cmd_suggest(args):
    """For every string in a catalog, Apple's translations: exact matches first, else a glossary of the
    Apple UI terms the string contains. JSON, meant to be read by Claude while translating."""
    conn = db.connect(args.db, readonly=True)
    langs = args.lang.split(",") if args.lang else None
    if args.catalog == "-":
        items = [(line.strip(), line.strip(), {}) for line in sys.stdin if line.strip()]
        if not langs:
            raise SystemExit("Reading English strings from stdin needs -l/--lang.")
    else:
        cat = load_catalog(args.catalog)
        src_lang = cat.get("sourceLanguage", "en")
        items = []
        for key, entry in cat.get("strings", {}).items():
            if entry.get("shouldTranslate") is False:
                continue
            locs = entry.get("localizations", {})
            source = _xc_value(locs.get(src_lang)) or key
            # Nothing to translate in strings that are only placeholders or punctuation, like "%@".
            if any(not m.group().startswith("%") for m in WORD_RE.finditer(source)):
                items.append((key, source, locs))
        if not langs:
            langs = sorted({c for _, _, locs in items for c in locs if c != src_lang})
        if not langs:
            raise SystemExit("The catalog has no target languages yet; pass -l, e.g. -l fr,de,ja,ar.")
    check_langs(conn, langs)

    cache = {}

    def apple(text):
        if text not in cache:
            hit = any(t == text for (t,) in conn.execute("SELECT text FROM texts WHERE h = ?", (db.text_hash(text),)))
            cache[text] = translations(conn, text, langs, args.platform, variant="") if hit else ({}, [])
        return cache[text]

    def glossary(source):
        tokens = [m for m in WORD_RE.finditer(source) if not m.group().startswith("%")]
        n = len(tokens)
        spans = sorted(((i, j) for i in range(n) for j in range(i + 1, min(n, i + args.max_words) + 1)),
                       key=lambda ij: (-(ij[1] - ij[0]), ij[0]))
        used, terms = set(), []
        for i, j in spans:
            if used & set(range(i, j)):
                continue
            words = [t.group() for t in tokens[i:j]]
            if all(w.lower() in STOPWORDS for w in words) or (j - i == 1 and len(words[0]) < 3):
                continue
            # Keep the punctuation between the words, as in "Wi-Fi" or "Sign In…".
            phrase = source[tokens[i].start():tokens[j - 1].end()]
            # The exact phrase was already looked up; a different casing of it may still match.
            forms = [f for f in _forms(phrase) if f != source]
            for form in forms:
                res, places = apple(form)
                # A lone word is often a different sense in Apple's UI ("Check" as a bank check), so ask for more evidence.
                need = args.min_bundles + (2 if j - i == 1 else 0)
                if len({(p[0], p[1]) for p in places}) >= need and res:
                    terms.append((tokens[i].start(), {
                        "english": form, "kind": "word" if j - i == 1 else "phrase",
                        "apple_bundles": len({(p[0], p[1]) for p in places}),
                        "translations": {c: _summarize(rows) for c, rows in res.items()}}))
                    used |= set(range(i, j))
                    break
        return [t for _, t in sorted(terms, key=lambda t: t[0])]

    out, n_exact, n_glossary, n_none = [], 0, 0, 0
    for key, source, locs in items:
        todo = [c for c in langs if not (args.only_missing and catalog_value(locs.get(c)) is not None)]
        if not todo:
            continue
        res, places = apple(source)
        item = {"key": key, "english": source} if key != source else {"english": source}
        if places:
            n_exact += 1
            item["match"] = "exact"
            item["apple_bundles"] = len({(p[0], p[1]) for p in places})
            per = {}
            for code in todo:
                d = {}
                ours = catalog_value(locs.get(code))
                if ours is not None:
                    d["ours"] = ours
                rows = res.get(code)
                if rows:
                    d.update(_summarize(rows))
                    if ours is not None and ours != "<variations>":
                        d["matches_apple"] = ours in {t for t, *_ in rows}
                per[code] = d
            item["translations"] = per
        else:
            terms = glossary(source)
            if terms:
                n_glossary += 1
                item["match"] = "terms"
                for t in terms:
                    t["translations"] = {c: v for c, v in t["translations"].items() if c in todo}
                item["terms"] = terms
            else:
                n_none += 1
                item["match"] = "none"
            ours = {c: catalog_value(locs.get(c)) for c in todo if catalog_value(locs.get(c)) is not None}
            if ours:
                item["ours"] = ours
        out.append(item)

    result = {"platform": args.platform or "all", "langs": langs,
              "summary": {"strings": len(out), "exact": n_exact, "terms_only": n_glossary, "no_match": n_none},
              "strings": out}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"{len(out)} strings → {args.out}: {n_exact} exact Apple matches, {n_glossary} with Apple terms, "
              f"{n_none} with nothing to go on.")
    else:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
        print()
