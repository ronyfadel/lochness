---
name: lochness
description: Localize an Apple-platform app (iOS, iPadOS, macOS, watchOS) so its wording matches Apple's own translations in iOS and macOS. Looks up how Apple translated the same English string, or the UI terms inside it, in a local database built from this Mac's system software and the newest simulator runtime of each platform, then translates and reviews a String Catalog (.xcstrings) or .strings/.stringsdict files with that evidence. Use when the user asks to localize or translate an app, fill in missing translations, add a language, review or fix existing translations, make wording "match iOS" or "sound like Apple", or asks how Apple translates a string or term. Also use for "build/update/purge the Apple localization database".
---

# Apple-quality localization

Apple has already translated most of the UI vocabulary an app needs, such as "Cancel", "Not Now",
"Start Recording", "Speech Recognition" and "Check In", into every language it ships, and users see
those words all day. An app that uses the same words feels native, and one that invents its own
looks off even when its translation is correct. This skill gives you Apple's translations as
evidence, so you can match the OS where it applies and write in Apple's style where it doesn't.

The CLI is `bin/lochness` in this skill's base directory. Run it by its full path; it needs only
macOS's `python3`. Below it is written as `lochness`.

## 1. Make sure the database exists

Run `lochness status` first.

- **No database yet.** Before you build it, tell the user, in one or two sentences, that you're
  building a local database of Apple's own translations from macOS and the newest simulator
  runtime of each platform on this Mac. Give the location, the disk space and the time from
  `status`'s estimate (about 4–5 GB and 10 minutes). Say that nothing is downloaded and
  `lochness purge` removes it. If the user has said which languages they localize into, or disk
  space is tight, build only those with `--langs`, using the catalog's locales (for example
  `lochness scan --langs fr-FR,es-MX`). That makes it about 0.4 GB and 1 minute for two languages.
  Tell them that adding a language later means `lochness purge` and a rebuild. Then run the scan
  **in the background**, since it outlasts a
  normal command timeout. Do work that doesn't need it meanwhile, such as reading the catalog and
  the app's context. If `scan` stops because of low disk space, tell the user; don't pass `--force` on
  your own.
- **`status` warns that no simulator runtime, or no iOS runtime, was found.** Pass the warning on:
  iOS wording will be missing. Suggest installing one (Xcode > Settings > Components) for an iOS app.
- **"not scanned yet" lists sources.** macOS was updated or a newer simulator runtime is installed. Tell the user
  and offer `lochness scan`, which adds only the new sources. Don't block on it: the existing data
  is still good.
- **A lookup prints "this database only has …".** It was built with `--langs` and lacks a language
  you need. Tell the user, and rebuild with the union of languages only if they agree.
- Only run `lochness purge --yes` when the user asks to delete the database.

## 2. Gather Apple's evidence for the strings

```sh
lochness suggest path/to/Localizable.xcstrings -p iOS -l fr,de,ja --only-missing -o /tmp/lochness.json
```

- `-p` is the app's platform: `iOS` for iPhone/iPad, `macOS`, `watchOS`. Leave it out for multiplatform
  apps.
- `-l` is the languages to work on (default: those already in the catalog). Use `--only-missing` to
  fill gaps. Leave it out when reviewing existing translations.
- Accepts a `.xcstrings`, one lproj's `.strings` file (its sibling lprojs are read too), or `-` for
  English lines on stdin (with `-l`), for strings that aren't in a catalog yet.
- Write to a file with `-o` and read it in parts; a big catalog in many languages is several MB.
  It takes about 15 seconds for 1,000 strings.

Each string comes back with `match`:

- `"exact"`: Apple ships this exact English string. `translations.<lang>` gives Apple's majority
  translation `apple`, `agreement` (bundles that use it / bundles that translate it),
  `examples` (which Apple bundles), `alternatives` when Apple is split, and `ours` plus
  `matches_apple` when the catalog already has one.
- `"terms"`: there's no exact match, but `terms` lists Apple UI phrases found inside the string with their
  translations. `kind: "phrase"` (two or more words, such as "Speech Recognition") is strong
  evidence. `kind: "word"` is a hint only: Apple may use the word in another sense ("Check" turns up
  as a bank check in Wallet).
- `"none"`: nothing to go on, so translate it yourself in Apple's style.

`lochness lookup "Start Recording" -l ja,ar -p iOS` shows one string. `lochness search "recording"`
finds Apple's English strings that contain those words, which helps when the user's wording is close
to Apple's but not the same.

## 3. Translate with the evidence

1. **Exact match with clear agreement: use Apple's translation as it is**, including its
   punctuation and typographic characters (’ “ ” …, non-breaking spaces, « » in French).
   This covers the system vocabulary: Cancel, Done, OK, Delete, Not Now, Continue, Settings, Share,
   Learn More, Allow, Don't Allow, Turn On…
2. **Check the context before reusing a match.** Apple's string may come from a different feature.
   For example, Japanese "Pause Recording" only exists in a *video* context (録画), but an audio app
   needs 録音. Arabic "Recording" is split between a noun (تسجيل) and "recording in progress"
   (يجري التسجيل). Read `examples` and `alternatives`, and pick what fits the app's meaning.
3. **Apple is split: take the majority**, unless a minority translation comes from bundles closer to
   the app's context, such as the Apple Account screens for a sign-in button.
4. **Ignore a translation that is just the English text** in a non-English language (for example
   "Learn More" left untranslated in a couple of new Health frameworks). It's an untranslated
   leftover, not a choice, unless it's a name such as Wi-Fi or Bluetooth.
5. **For "terms" matches, use the phrases as a glossary** for the whole string. For example, if Apple says
   "Spracherkennung" for "Speech Recognition", write that, adapted to the sentence's grammar (case,
   articles, word order). Keep each term consistent across the whole catalog.
6. **Apple feature and product names always take Apple's translation** (Focus, Live Activities,
   Screen Time, Check In, Wallet, Dynamic Island, Lock Screen, Home Screen, Control Center). Look up any
   you're unsure of.
7. **No evidence: write like Apple does in that language.** Short, plain and friendly. Use the same
   form of address as Apple's strings in the results: for example, French uses *vous* and German
   uses *du*; check a few `exact` results rather than assuming. Follow Apple's capitalization in the target
   language: German capitalizes nouns only, French buttons use sentence case. Keep the app's own
   names untranslated unless the catalog already translates them.
8. **Keep the catalog valid.** Keep every placeholder (`%@`, `%lld`, `%1$@`; reorder with positional
   forms if the grammar needs it), keep plural/device variations and fill every plural category
   the language needs. Set each new translation's state to `translated`.
   Never change the English source or the keys.

## 4. Review

```sh
lochness check path/to/Localizable.xcstrings -p iOS -l fr,de,ja
```

This lists every string whose English matches Apple's exactly but whose translation differs from
Apple's, plus ones that are missing where Apple has one. Fix the system-vocabulary ones. Keep a
difference when the context check in step 3 says Apple's string means something else, and tell the
user which ones you kept and why.

Tell the user what you did: how many strings matched Apple exactly, how many used Apple
terminology, and which were translated without evidence. Those last ones are worth a native speaker's
review.

## Notes

- Languages use String Catalog codes (`zh-Hans`, `pt-BR`, `nb`). Apple's bare `pt` is Brazilian
  Portuguese, and `pt-PT` is separate.
- The database holds Apple's translations from the software installed on this Mac. Use it as a
  reference while translating. Don't copy large dumps of it into the user's repo or share it elsewhere.
- `lochness stats --langs` shows the languages and how many strings each one has.
