# apple-loch-ness

A local SQLite database of Apple's own UI translations, taken from macOS, Apple's apps and the
installed iOS/watchOS simulator runtimes. When we localize, we look up how Apple translated the
same English string, so our wording matches the system's.

Only Apple software is scanned:

- macOS: `/System/Library`, `/System/Applications`, `/System/iOSSupport`, the Safari and OS cryptexes, `/Library/Apple`
- Apple apps in `/Applications` (`com.apple.*` bundle IDs only, e.g. Pages, Numbers, TestFlight, SF Symbols) and the selected Xcode
- every Ready simulator runtime listed by `xcrun simctl runtime list` (the whole `RuntimeRoot`: UIKit, SwiftUI, Settings, Voice Memos, Camera…)

Third-party apps are skipped.

It reads `.loctable` files (macOS packs every language into one binary plist), `xx.lproj/*.strings`
(binary, XML or old-style text) and `.stringsdict`. Plural and device variants are flattened into a
`variant` path such as `count/one`.

Python 3 standard library only.

## Use

```sh
python3 -m lochness scan            # everything (~10–15 min); re-run after an OS/Xcode update
python3 -m lochness scan --list     # what would be scanned
python3 -m lochness scan --only iOS # one source; already-scanned sources are skipped unless --rescan

python3 -m lochness lookup "Delete Recording" -l ar,he,fr
python3 -m lochness lookup "done" -i -p iOS --json
python3 -m lochness search "recording"           # Apple's English strings containing these words

# Compare our translations with Apple's for every string whose English matches Apple exactly
python3 -m lochness check ~/Developer/VidCap/iosapp/Spiik/Localizable.xcstrings -l ar,he
python3 -m lochness check ~/Developer/VidCap/iosapp/VidAppCore/Sources/VidAppCore/VidCap/Localization/en.lproj/Localizable.strings -l ar

python3 -m lochness stats --langs
```

`lookup` lists each translation with the number of Apple bundles that use it, so the
majority choice is first and the outliers show up below it.

The database lives at `data/apple-l10n.sqlite` (gitignored; override with `--db` or `$LOCHNESS_DB`).

## Schema

| table | what |
|---|---|
| `sources` | each scanned OS (platform, version, build, root) |
| `bundles` | app/framework/extension per platform, path inside the OS, bundle ID |
| `tables` | a strings table in a bundle (`Localizable`, `MainMenu`…) and its format |
| `table_sources` | which OS builds contain the table |
| `entries` | `(table, key, variant, lang, text)`, all interned IDs |
| `keys`, `langs`, `variants`, `texts` | interned values; `texts.h` is a 64-bit BLAKE2b hash used for exact lookups |
| `source_fts` | FTS5 index over English texts (`en`, `en-US`, `Base`) |
| `v_entries` | everything joined, for ad-hoc SQL |

Language codes are normalized to String Catalog codes: `English`→`en`, `zh_CN`→`zh-Hans`,
`zh_TW`→`zh-Hant`, `no`→`nb`, `pt_BR`→`pt-BR`. Apple's bare `pt` is Brazilian Portuguese.

Many keys are meaningless nib IDs (`feR-HL-qNb.title`), so always look strings up by their English
text, which `lookup` and `check` do.

```sql
-- Every Arabic translation of the English "Share…" on iOS
SELECT DISTINCT bundle, text FROM v_entries
WHERE platform = 'iOS' AND lang = 'ar' AND (bundle_path, table_name, key) IN (
  SELECT bundle_path, table_name, key FROM v_entries WHERE platform = 'iOS' AND lang = 'en' AND text = 'Share…');
```

The data is Apple's: it's a private reference, not for redistribution.
