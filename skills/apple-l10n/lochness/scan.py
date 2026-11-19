"""Find Apple localization files on this Mac and in simulator runtimes, and load them into SQLite."""

import datetime
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from multiprocessing import Pool

from . import db, parse

# macOS locations that only hold Apple software.
MAC_ROOTS = [
    "/System/Library",
    "/System/Applications",
    "/System/iOSSupport",
    "/System/Cryptexes/App/System",
    "/System/Cryptexes/OS/System",
    "/Library/Apple",
]
# Third-party apps live here too; only com.apple.* bundles are scanned.
APP_DIRS = ["/Applications", "/Applications/Utilities"]
CRYPTEX_PREFIXES = ("/System/Cryptexes/App", "/System/Cryptexes/OS")


def system_version(root):
    p = os.path.join(root, "System/Library/CoreServices/SystemVersion.plist")
    try:
        info = plistlib.loads(open(p, "rb").read())
        return info.get("ProductName"), info.get("ProductVersion"), info.get("ProductBuildVersion")
    except Exception:
        return None, None, None


def apple_apps():
    apps = []
    for d in APP_DIRS:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if not n.endswith(".app") or os.path.islink(os.path.join(d, n)):
                continue
            bid = parse.bundle_info(os.path.join(d, n)) or ""
            if bid.startswith("com.apple."):
                apps.append(os.path.join(d, n))
    # The selected Xcode, wherever it lives.
    try:
        dev = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, timeout=10).stdout.strip()
        xcode = dev.split("/Contents/Developer")[0]
        if xcode.endswith(".app") and xcode not in apps and (parse.bundle_info(xcode) or "").startswith("com.apple."):
            apps.append(xcode)
    except Exception:
        pass
    return apps


def discover_sources():
    sources = []
    name, version, build = system_version("/")
    mac_dirs = [r for r in MAC_ROOTS if os.path.isdir(r)] + apple_apps()
    sources.append({"platform": "macOS", "os_version": version, "os_build": build,
                    "root": "/", "dirs": mac_dirs, "label": f"macOS {version} ({build})"})
    ios = latest_ios_runtime()
    if ios:
        sources.append(ios)
    return sources


def _version_key(version, build):
    """Sort key: 27.2 > 27.1.1, then Apple build numbers, 24B91016s > 24B8117."""
    v = tuple(int(x) for x in re.findall(r"\d+", version or ""))
    m = re.match(r"(\d+)([A-Z])(\d+)([a-z]?)", build or "")
    b = (int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)) if m else (0, "", 0, "")
    return v, b


def latest_ios_runtime():
    """The newest installed iOS simulator runtime as a source, or None. Only one is scanned:
    older runtimes and other platforms add disk space but little new wording."""
    try:
        out = subprocess.run(["xcrun", "simctl", "runtime", "list", "-j"], capture_output=True, text=True, timeout=60)
        runtimes = json.loads(out.stdout or "{}")
    except Exception:
        runtimes = {}
    found = []
    for rt in runtimes.values():
        bundle = rt.get("runtimeBundlePath")
        if rt.get("platformIdentifier") != "com.apple.platform.iphonesimulator":
            continue
        if not bundle or rt.get("state") not in (None, "Ready"):
            continue
        root = os.path.join(bundle, "Contents/Resources/RuntimeRoot")
        if not os.path.isdir(root):
            continue
        _, version, build = system_version(root)
        version, build = version or rt.get("version"), build or rt.get("build")
        found.append({"platform": "iOS", "os_version": version, "os_build": build, "root": root, "dirs": [root],
                      "label": f"iOS {version} ({build}) simulator"})
    return max(found, key=lambda s: _version_key(s["os_version"], s["os_build"]), default=None)


NO_SIMULATOR = ("No iOS simulator runtime found, so only macOS will be scanned and iOS-only wording "
                "(Settings, Health, SpringBoard…) will be missing. Install one in Xcode > Settings > Components, "
                "or run `xcodebuild -downloadPlatform iOS`, then `lochness scan` again.")


def has_ios(sources):
    return any(s["platform"] == "iOS" for s in sources)


# Rough database growth per source, measured on macOS 26 + iOS 27 + watchOS 27
# (about 72 bytes per stored string). A second runtime of a platform already in
# the database adds little, because identical tables are stored once.
GB_PER_SOURCE = {"macOS": 1.6, "iOS": 2.0, "watchOS": 0.9, "tvOS": 0.9, "visionOS": 1.6, "xrOS": 1.6}
GB_EXTRA_RUNTIME = 0.2
MINUTES_PER_SOURCE = 2.5
MINUTES_INDEXING = 2.5


def scanned_keys(conn):
    return {(p, b) for p, b in conn.execute("SELECT platform, os_build FROM sources WHERE scanned_at IS NOT NULL")}


def pending_sources(sources, done):
    """Sources not in the database yet. Matched on platform and build, not on the
    filesystem root: simulator runtimes are mounted under a path that changes."""
    return [s for s in sources if (s["platform"], s["os_build"]) not in done]


def estimate(pending, done_platforms=()):
    """(gigabytes, minutes) that scanning these sources should take."""
    gb, seen = 0.0, set(done_platforms)
    for s in pending:
        gb += GB_EXTRA_RUNTIME if s["platform"] in seen else GB_PER_SOURCE.get(s["platform"], 1.5)
        seen.add(s["platform"])
    minutes = MINUTES_PER_SOURCE * len(pending) + (MINUTES_INDEXING if pending else 0)
    return gb, minutes


def free_gb(db_path):
    d = os.path.dirname(os.path.abspath(db_path))
    while not os.path.exists(d):
        d = os.path.dirname(d)
    return shutil.disk_usage(d).free / 1e9


def walk_files(dirs):
    for top in dirs:
        for dirpath, dirnames, filenames in os.walk(top, onerror=lambda e: None):
            for f in filenames:
                if f.endswith((".loctable", ".strings", ".stringsdict")):
                    yield os.path.join(dirpath, f)


def rel_path(path, root):
    rel = "/" + os.path.relpath(path, root) if root != "/" else path
    # Don't record where this Mac keeps its Xcode.
    if ".app/" in rel and not rel.startswith(("/System/", "/Applications/", "/Library/")):
        app, rest = rel.split(".app/", 1)
        rel = "/Applications/" + os.path.basename(app) + ".app/" + rest
    for prefix in CRYPTEX_PREFIXES:
        if rel.startswith(prefix + "/"):
            rel = rel[len(prefix):]
    return rel


_bundle_cache = {}


def parse_job(job):
    path, root = job
    cls = parse.classify(path)
    if cls is None:
        return None
    fmt, table, lang = cls
    try:
        rows = parse.read_loctable(path) if fmt == "loctable" else parse.read_lproj_file(path, lang)
    except Exception as e:
        return ("error", path, str(e)[:200])
    bdir = parse.find_bundle(path, root)
    if bdir not in _bundle_cache:
        _bundle_cache[bdir] = parse.bundle_info(bdir)
    name = os.path.basename(bdir)
    kind = os.path.splitext(name)[1].lstrip(".") or "dir"
    return ("ok", rel_path(bdir, root), _bundle_cache[bdir], name, kind, table, fmt, rows)


class TextInterner:
    """Interns texts by hash so the whole corpus never has to sit in memory."""

    def __init__(self, conn):
        self.conn = conn
        self.ids = dict((h, i) for i, h in conn.execute("SELECT id, h FROM texts"))
        self.next = max(self.ids.values(), default=0) + 1
        self.pending = []

    def get(self, text):
        h = db.text_hash(text)
        i = self.ids.get(h)
        if i is None:
            i = self.ids[h] = self.next
            self.next += 1
            self.pending.append((i, h, text))
        return i

    def flush(self):
        if self.pending:
            self.conn.executemany("INSERT INTO texts (id, h, text) VALUES (?, ?, ?)", self.pending)
            self.pending = []


class Interner:
    def __init__(self, conn, table, column):
        self.conn, self.table, self.column = conn, table, column
        self.ids = dict((v, i) for i, v in conn.execute(f"SELECT id, {column} FROM {table}"))
        self.next = max(self.ids.values(), default=0) + 1
        self.pending = []

    def get(self, value):
        i = self.ids.get(value)
        if i is None:
            i = self.ids[value] = self.next
            self.next += 1
            self.pending.append((i, value))
        return i

    def flush(self):
        if self.pending:
            self.conn.executemany(f"INSERT INTO {self.table} (id, {self.column}) VALUES (?, ?)", self.pending)
            self.pending = []


def scan(db_path, only=None, rescan=False, workers=None, force=False):
    sources = discover_sources()
    if only:
        sources = [s for s in sources if any(o.lower() in s["label"].lower() for o in only)]
    first_run = not os.path.exists(db_path)
    done = set()
    if not first_run:
        with db.connect(db_path, readonly=True) as c:
            done = scanned_keys(c)
    if not has_ios(sources) and not (only and not any("ios" in o.lower() for o in only)):
        print(NO_SIMULATOR + "\n", flush=True)
    todo = sources if rescan else pending_sources(sources, done)
    if not todo:
        print("Nothing new to scan: every source is already in the database (use --rescan to scan again).")
        return
    gb, minutes = estimate([] if rescan else todo, {p for p, _ in done})
    free = free_gb(db_path)
    print(("Building" if first_run else "Updating") +
          f" the Apple localization database at {db_path}\n"
          f"from Apple's own software on this Mac (nothing is downloaded):", flush=True)
    for s in todo:
        print(f"  - {s['label']}", flush=True)
    print(f"This takes about {minutes:.0f} minutes and needs about {gb:.1f} GB of disk "
          f"({free:.0f} GB free). `lochness purge` deletes it.\n", flush=True)
    if free < gb * 1.5 + 1 and not force:
        raise SystemExit(f"Not enough free disk: about {gb * 1.5 + 1:.1f} GB is needed while building. "
                         "Free some space, scan fewer sources with --only, or pass --force.")

    conn = db.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = OFF")
    keys = Interner(conn, "keys", "key")
    langs = Interner(conn, "langs", "code")
    variants = Interner(conn, "variants", "variant")
    texts = TextInterner(conn)
    bundles = {(p, path): i for i, p, path in conn.execute("SELECT id, platform, path FROM bundles")}
    tables = {(b, n, f): i for i, b, n, f in conn.execute("SELECT id, bundle_id, name, format FROM tables")}

    for src in sources:
        if src not in todo:
            print(f"skip {src['label']} (already scanned; use --rescan)", flush=True)
            continue
        row = conn.execute("SELECT id FROM sources WHERE platform=? AND os_build IS ?",
                           (src["platform"], src["os_build"])).fetchone()
        if row:
            source_id = row[0]
            conn.execute("UPDATE sources SET root=? WHERE id=?", (src["root"], source_id))
        else:
            source_id = conn.execute(
                "INSERT INTO sources (platform, os_version, os_build, root, label) VALUES (?,?,?,?,?)",
                (src["platform"], src["os_version"], src["os_build"], src["root"], src["label"])).lastrowid
        print(f"scan {src['label']}: listing files…", flush=True)
        t0 = time.time()
        files = list(walk_files(src["dirs"]))
        print(f"  {len(files):,} files in {time.time() - t0:.0f}s", flush=True)
        n_rows = n_err = done = 0
        batch = []
        platform = src["platform"]
        with Pool(workers or os.cpu_count()) as pool:
            for res in pool.imap_unordered(parse_job, ((f, src["root"]) for f in files), chunksize=64):
                done += 1
                if res is None:
                    continue
                if res[0] == "error":
                    n_err += 1
                    if n_err <= 20:
                        print(f"  ! {rel_path(res[1], src['root'])}: {res[2].replace(res[1], '')}", file=sys.stderr)
                    continue
                _, bpath, bid, bname, kind, tname, fmt, rows = res
                bkey = (platform, bpath)
                b = bundles.get(bkey)
                if b is None:
                    b = bundles[bkey] = conn.execute(
                        "INSERT INTO bundles (platform, path, bundle_id, name, kind) VALUES (?,?,?,?,?)",
                        (platform, bpath, bid, bname, kind)).lastrowid
                tkey = (b, tname, fmt)
                t = tables.get(tkey)
                if t is None:
                    t = tables[tkey] = conn.execute(
                        "INSERT INTO tables (bundle_id, name, format) VALUES (?,?,?)", (b, tname, fmt)).lastrowid
                    conn.execute("INSERT OR IGNORE INTO table_sources VALUES (?,?)", (t, source_id))
                else:
                    conn.execute("INSERT OR IGNORE INTO table_sources VALUES (?,?)", (t, source_id))
                for lang, key, variant, text in rows:
                    batch.append((t, keys.get(key), variants.get(variant), langs.get(lang), texts.get(text)))
                n_rows += len(rows)
                if len(batch) >= 200_000:
                    for i in (keys, langs, variants, texts):
                        i.flush()
                    conn.executemany("INSERT OR IGNORE INTO entries VALUES (?,?,?,?,?)", batch)
                    batch = []
                    conn.commit()
                    print(f"  {done:,}/{len(files):,} files, {n_rows:,} strings", flush=True)
        for i in (keys, langs, variants, texts):
            i.flush()
        conn.executemany("INSERT OR IGNORE INTO entries VALUES (?,?,?,?,?)", batch)
        conn.execute("UPDATE sources SET scanned_at=? WHERE id=?",
                     (datetime.datetime.now().isoformat(timespec="seconds"), source_id))
        conn.commit()
        print(f"  done: {n_rows:,} strings, {n_err} unreadable files, {time.time() - t0:.0f}s", flush=True)

    print("indexing…", flush=True)
    t0 = time.time()
    conn.executescript(db.POST_INDEXES)
    conn.executescript(db.FTS)
    conn.execute("ANALYZE")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    print(f"  {time.time() - t0:.0f}s", flush=True)
    print(f"Done. The database is {db.size_on_disk(db_path) / 1e9:.1f} GB.", flush=True)
