# apple-loch-ness

**Localize your app the way Apple localizes iOS.** A Claude Code skill and CLI that look up how Apple
translated the same English string, or the UI terms inside it, across macOS and the
iOS/watchOS simulator runtimes, so Claude's translations match the system your users already know.

Ask Claude to translate "Not Now" into Italian and it may write "Non adesso". iOS says "Non ora" in
148 places. "Start Recording" in Japanese is 収録を開始 in iOS, not the 録音を開始 a translator might
choose. Get dozens of these right and the app reads like part of the OS. Get them wrong and it reads like a port.

```
$ lochness lookup "Sign In" -l fr,de -p iOS
"Sign In" appears in 64 Apple bundles: …
  fr       Se connecter   ×48  iOS  (ActiveSyncSettings.bundle, AppStoreDaemon.framework …)
           Connexion      ×12  iOS  (AppleAccount.framework, AuthKitUI.framework …)
  de       Anmelden       ×58  iOS  (…)
```

## How it works

Nothing is downloaded and no Apple data is in this repo. On first use, `lochness scan` reads the
localization files that are already on your Mac and builds a local SQLite database:

- macOS: `/System/Library`, `/System/Applications`, `/System/iOSSupport`, the Safari and OS cryptexes, `/Library/Apple`
- Apple's apps in `/Applications` (`com.apple.*` only, e.g. Pages, Numbers, TestFlight) and the selected Xcode
- the newest installed simulator runtime of each platform (iOS, watchOS, tvOS, visionOS; `xcrun simctl runtime list`):
  UIKit, SwiftUI, Settings, Health, Camera… Older runtimes of a platform are skipped: they cost disk space and add
  little new wording. If no simulator runtime is installed, `lochness` says so and scans macOS only.

It reads `.loctable`, `.strings` (binary, XML or text) and `.stringsdict` files. For macOS 26 plus the
iOS 27 and watchOS 27 runtimes, that's tens of millions of translations in about 90 languages, a **4–5 GB**
database built in **about 10 minutes**. Before it starts, `lochness scan` prints the sources, the space it
needs and the free space on your disk. `lochness purge` deletes the database.

The database lives at `~/Library/Application Support/lochness/apple-l10n.sqlite`, outside the
plugin, so updates keep it. Set `$LOCHNESS_DB` or pass `--db` to put it elsewhere.

## Install

**Claude Code plugin** (skill + CLI):

```
/plugin marketplace add ronyfadel/apple-loch-ness
/plugin install apple-l10n@apple-loch-ness
```

Then ask Claude to "localize my app into French, German and Japanese" or "check my translations
against iOS". On first use the skill tells you it's building the database and how much space it
takes, then runs the scan in the background.

**CLI only:** `pipx install git+https://github.com/ronyfadel/apple-loch-ness` (or
`uv tool install …`), or clone and run `skills/apple-l10n/bin/lochness`. It needs macOS and Python 3.9+,
standard library only, and simulator runtimes (installed with Xcode) for iOS and watchOS strings.

## CLI

```sh
lochness status                      # database location and size, what's scanned, what's new
lochness scan                        # build, or add a newer macOS or simulator runtime
lochness scan --only iOS             # one source
lochness purge                       # delete the database

lochness lookup "Delete Recording" -l ar,he,fr -p iOS
lochness search "recording"          # Apple's English strings containing these words

# Apple's evidence for every string in a catalog, as JSON for Claude:
# exact matches with how much Apple agrees with itself, else the Apple UI phrases inside the string
lochness suggest Localizable.xcstrings -p iOS -l fr,de,ja --only-missing -o suggestions.json

# Where a catalog's translations differ from Apple's for identical English
lochness check Localizable.xcstrings -p iOS -l fr,de
lochness check en.lproj/Localizable.strings -l ar   # reads the sibling lprojs too
```

`lookup` lists each translation with the number of Apple bundles that use it, so the majority choice
comes first and Apple's own inconsistencies show up below it.

## Schema

| table | what |
|---|---|
| `sources` | each scanned OS (platform, version, build) |
| `bundles` | app/framework/extension per platform, path inside the OS, bundle ID |
| `tables` | a strings table in a bundle (`Localizable`, `MainMenu`…) and its format |
| `table_sources` | which OS builds contain the table |
| `entries` | `(table, key, variant, lang, text)`, all interned IDs |
| `keys`, `langs`, `variants`, `texts` | interned values; `texts.h` is a 64-bit BLAKE2b hash for exact lookups |
| `source_fts` | FTS5 index over the English texts (`en`, `en-US`, `Base`) |
| `v_entries` | everything joined, for ad-hoc SQL |

Strings are interned because the same keys, languages and translations repeat across
millions of rows. Plural and device variants are flattened into a `variant` path such as `count/one`.
Language codes are normalized to String Catalog codes: `zh_CN`→`zh-Hans`, `no`→`nb`, `pt_BR`→`pt-BR`.
Apple's bare `pt` is Brazilian Portuguese.

Many keys are meaningless nib IDs (`feR-HL-qNb.title`), so look strings up by their English text,
which `lookup`, `suggest` and `check` do.

```sql
-- Every Arabic translation of the English "Share…" on iOS
SELECT DISTINCT bundle, text FROM v_entries
WHERE platform = 'iOS' AND lang = 'ar' AND (bundle_path, table_name, key) IN (
  SELECT bundle_path, table_name, key FROM v_entries WHERE platform = 'iOS' AND lang = 'en' AND text = 'Share…');
```

## Note

The translations are Apple's. This tool reads them from your own Mac as a reference for localizing
your own apps; don't redistribute the database.
