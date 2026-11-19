"""SQLite schema and helpers for the Apple localization database."""

import hashlib
import os
import sqlite3


def text_hash(text):
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=8).digest(), "big", signed=True)

# Outside the code, so reinstalling or updating the plugin keeps the database.
DEFAULT_DB = os.environ.get(
    "LOCHNESS_DB",
    os.path.expanduser("~/Library/Application Support/lochness/apple-l10n.sqlite"),
)


def db_files(path):
    """The database and its SQLite sidecar files that exist on disk."""
    return [f for f in (path, path + "-wal", path + "-shm") if os.path.exists(f)]


def size_on_disk(path):
    return sum(os.path.getsize(f) for f in db_files(path))

# Development-language codes, in order of preference, used to find the
# English text a translation was made from.
SOURCE_LANGS = ("en", "en-US", "Base")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,          -- macOS, iOS, watchOS, tvOS, visionOS
    os_version TEXT,
    os_build TEXT,
    root TEXT NOT NULL,              -- filesystem root that was scanned
    label TEXT,
    scanned_at TEXT,
    UNIQUE (platform, os_build, root)
);
CREATE TABLE IF NOT EXISTS bundles (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    path TEXT NOT NULL,              -- path inside the OS root, e.g. /System/Library/Frameworks/UIKit.framework
    bundle_id TEXT,
    name TEXT,                       -- e.g. UIKit.framework
    kind TEXT,                       -- app, framework, appex, bundle, ...
    UNIQUE (platform, path)
);
CREATE TABLE IF NOT EXISTS tables (
    id INTEGER PRIMARY KEY,
    bundle_id INTEGER NOT NULL REFERENCES bundles(id),
    name TEXT NOT NULL,              -- table name, e.g. Localizable
    format TEXT NOT NULL,            -- loctable, strings, stringsdict
    UNIQUE (bundle_id, name, format)
);
CREATE TABLE IF NOT EXISTS table_sources (
    table_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    PRIMARY KEY (table_id, source_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS keys (id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS langs (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS variants (id INTEGER PRIMARY KEY, variant TEXT NOT NULL UNIQUE);
-- h is a 64-bit BLAKE2b hash of the text (see text_hash); indexing it is far
-- smaller than a UNIQUE index on the text itself.
CREATE TABLE IF NOT EXISTS texts (id INTEGER PRIMARY KEY, h INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS texts_by_hash ON texts (h);
-- One row per (table, key, plural/device variant, language, text).
-- variant is '' for plain strings, or a path such as 'count/one' for
-- stringsdict-style entries.
CREATE TABLE IF NOT EXISTS entries (
    table_id INTEGER NOT NULL,
    key_id INTEGER NOT NULL,
    variant_id INTEGER NOT NULL,
    lang_id INTEGER NOT NULL,
    text_id INTEGER NOT NULL,
    PRIMARY KEY (table_id, key_id, variant_id, lang_id, text_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

POST_INDEXES = """
CREATE INDEX IF NOT EXISTS entries_by_text ON entries (lang_id, text_id);
DROP VIEW IF EXISTS v_entries;
CREATE VIEW v_entries AS
SELECT b.platform, b.name AS bundle, b.bundle_id, b.path AS bundle_path,
       t.name AS table_name, t.format, k.key, v.variant, l.code AS lang, x.text
FROM entries e
JOIN tables t ON t.id = e.table_id
JOIN bundles b ON b.id = t.bundle_id
JOIN keys k ON k.id = e.key_id
JOIN variants v ON v.id = e.variant_id
JOIN langs l ON l.id = e.lang_id
JOIN texts x ON x.id = e.text_id;
"""

FTS = """
DROP TABLE IF EXISTS source_fts;
CREATE VIRTUAL TABLE source_fts USING fts5(text, tokenize = 'unicode61 remove_diacritics 2');
INSERT INTO source_fts (rowid, text)
SELECT x.id, x.text FROM texts x
WHERE x.id IN (SELECT DISTINCT e.text_id FROM entries e
               JOIN langs l ON l.id = e.lang_id
               WHERE l.code IN ('en', 'en-US', 'Base'));
"""


def connect(path=DEFAULT_DB, readonly=False):
    if readonly:
        if not os.path.exists(path):
            raise SystemExit(
                f"No database at {path} yet. It is built locally from this Mac's Apple software.\n"
                "Run `lochness status` to see what would be scanned and how much disk it needs, "
                "then `lochness scan` to build it.")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
    conn.execute("PRAGMA cache_size = -500000")
    return conn
