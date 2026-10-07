# Arabic (AR) devotionals — book-name fix and the `id` exception

`NAV_ar` and `SVDA_ar` were originally built before the `ar.json` book-name
sanitizer existed (see `bible_resolver`'s `data/book_name_sanitizers/ar.json`).
Their `versiculo`/`para_meditar` citations used each Bible DB's raw, verbose
`long_name` (e.g. "رِسَالَةُ بُولُسَ ٱلرَّسُولِ إِلَى أَهْلِ رُومِيَةَ")
instead of the canonical short form ("رومية"), and a translated version
label ("كتاب الحياة" / "فان دايك") instead of the standard version code
every other language/version uses.

This has since been fixed by regenerating citations against the sanitizer
and patching them into these files — but **only** `versiculo` and
`para_meditar` were changed. `id` was deliberately left as-is.

## Why `id` was not fixed

`id` is built from the raw (pre-sanitizer) citation text and is used by the
app as the key for user favorites. Regenerating `id` to match the new
canonical book names would change existing IDs for 2025/2026 content
already in production, silently breaking any favorite a user saved before
this fix — the app would no longer find the entry the user thinks they
saved.

So:

- **2025 / 2026** (`Devocional_year_2025_ar_NAV.json`,
  `Devocional_year_2026_ar_NAV.json`, and the SVDA equivalents in
  `devocionales-json`): `id` still contains the old long-form book name,
  even though `versiculo`/`para_meditar` now show the short form. This is
  intentional — do not "fix" `id` here without a coordinated favorites
  migration on the app side.
- **2027** (`Devocional_year_2027_ar_NAV.json`,
  `Devocional_year_2027_ar_SVDA.json`): never shipped, so no favorites
  reference these IDs yet. `Devocional_year_2027_ar_SVDA.json` was built
  fresh and already uses the canonical short-form `id`.
  `Devocional_year_2027_ar_NAV.json` predates the fix and still carries
  old-style IDs from its original generation — same reasoning applies if
  it gets regenerated again before release: only bump `id` format here if
  you're sure nothing downstream has captured a favorite against it yet.

## Related fix

A separate, underlying bug was found and fixed while regenerating this
data: `NAV_ar.SQLite3.gz` had Revelation 22:18-21 concatenated into the
verse 17 row, causing citations referencing those verses to fail to
resolve. Fixed in
[develop4God/bible_versions#9](https://github.com/develop4God/bible_versions/pull/9).
