"""Readers for Apple's localization file formats.

Every reader returns a list of (lang, key, variant, text) tuples.
"""

import os
import plistlib
import re
import subprocess

# Legacy lproj names and Apple's older region-style codes, mapped to the
# codes Xcode uses in String Catalogs today.
LANG_ALIASES = {
    "English": "en", "French": "fr", "German": "de", "Japanese": "ja",
    "Spanish": "es", "Italian": "it", "Dutch": "nl",
    "zh_CN": "zh-Hans", "zh_TW": "zh-Hant", "zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "no": "nb",
}

# stringsdict metadata keys that never hold user-facing text.
SKIP_KEYS = {"NSStringFormatSpecTypeKey", "NSStringFormatValueTypeKey"}

LANG_RE = re.compile(r"^(Base|[a-z]{2,3}([_-][A-Za-z0-9]{2,8})*|English|French|German|Japanese|Spanish|Italian|Dutch)$")

BUNDLE_EXTS = {
    ".app", ".framework", ".bundle", ".appex", ".plugin", ".kext", ".xpc", ".prefPane",
    ".saver", ".qlgenerator", ".mdimporter", ".menu", ".component", ".driver", ".dext",
    ".docktileplugin", ".ideplugin", ".xcplugin", ".systemextension", ".wdgt", ".action",
    ".service", ".axbundle", ".servicebundle", ".extensionkit", ".appintents", ".loginPlugin",
    ".mediaextension", ".spotlight", ".fsplugin", ".fsmodule", ".ioplugin", ".sync",
}


# macOS loctables carry per-platform wording under keys such as "ar-iphoneos"
# or "en-macos"; they are stored as the base language with an "@platform" variant.
PLATFORM_SUFFIXES = ("macos", "iphoneos", "tvos", "watchos", "xros", "visionos")


def split_platform(lang):
    base, _, suffix = lang.rpartition("-")
    if base and suffix in PLATFORM_SUFFIXES:
        return base, "@" + suffix
    return lang, ""


def norm_lang(raw):
    code = LANG_ALIASES.get(raw, raw)
    return LANG_ALIASES.get(code.replace("_", "-"), code.replace("_", "-"))


def _flatten(value, prefix=""):
    """Yield (variant, text) pairs; nested dicts become a/b/c variant paths."""
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, dict):
        for k, v in value.items():
            if k in SKIP_KEYS:
                continue
            yield from _flatten(v, f"{prefix}/{k}" if prefix else str(k))
    # Numbers, booleans, data and arrays are not user-facing text.


def load_plist(path):
    with open(path, "rb") as f:
        data = f.read()
    if not data.strip():
        return {}
    try:
        return plistlib.loads(data)
    except Exception:
        pass
    # Old-style text .strings (often UTF-16); plutil understands them.
    out = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", path],
                         capture_output=True, timeout=30)
    if out.returncode != 0:
        raise ValueError(out.stderr.decode(errors="replace").strip())
    return plistlib.loads(out.stdout)


def read_loctable(path):
    rows = []
    data = load_plist(path)
    for lang, table in data.items():
        if not isinstance(table, dict) or lang == "LocProvenance" or not LANG_RE.match(lang):
            continue
        base, platform = split_platform(lang)
        code = norm_lang(base)
        for key, value in table.items():
            for variant, text in _flatten(value):
                if platform:
                    variant = f"{platform}/{variant}" if variant else platform
                rows.append((code, key, variant, text))
    return rows


def read_lproj_file(path, lang):
    code = norm_lang(lang)
    data = load_plist(path)
    rows = []
    if isinstance(data, dict):
        for key, value in data.items():
            for variant, text in _flatten(value):
                rows.append((code, key, variant, text))
    return rows


def classify(path):
    """Return (format, table_name, lang or None) for a localization file, else None."""
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)
    if ext == ".loctable":
        return "loctable", stem, None
    if ext in (".strings", ".stringsdict"):
        parent = os.path.basename(os.path.dirname(path))
        if parent.endswith(".lproj"):
            return ext[1:], stem, parent[:-6]
    return None


def find_bundle(path, root):
    """Nearest enclosing bundle directory, as an absolute path."""
    d = os.path.dirname(path)
    while len(d) > len(root):
        if os.path.splitext(d)[1] in BUNDLE_EXTS:
            return d
        d = os.path.dirname(d)
    # Loose resources: use the directory above the .lproj (or the file's dir).
    d = os.path.dirname(path)
    return os.path.dirname(d) if d.endswith(".lproj") else d


def bundle_info(bundle_dir):
    for rel in ("Contents/Info.plist", "Info.plist", "Resources/Info.plist", "Versions/Current/Resources/Info.plist"):
        p = os.path.join(bundle_dir, rel)
        if os.path.isfile(p):
            try:
                info = plistlib.loads(open(p, "rb").read())
                return info.get("CFBundleIdentifier")
            except Exception:
                return None
    return None
