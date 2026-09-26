# Bible SQLite databases

This directory holds the Bible SQLite DBs that `build_seed_for_language.py`,
`sanitize_seed_citations.py`, and other seed_generation tools read via `--db`.
These are committed working copies for the versions this codebase actively
uses — the actual source of truth is the `bible_versions` repo; treat any DB
here as a cache of it, not the canonical original.

## Source of truth

Repo: `https://github.com/develop4God/bible_versions`

Every version's file, download URL, and hash is listed in that repo's
`index.json`, keyed by language then version code:

```json
{
  "languages": {
    "de": {
      "versions": {
        "LU17": {
          "file": "LU17_de.SQLite3.gz",
          "url": "https://raw.githubusercontent.com/develop4God/bible_versions/main/de/LU17_de.SQLite3.gz",
          "hash": "..."
        }
      }
    }
  }
}
```

`bible_books.json` in the same repo is the English-name → canonical
`book_number` lookup (`VerseResolver.load_books_sot`'s default remote source).

**Do not assume a version by name alone** — e.g. `KJV` and `KJ2000` are
different DBs with different `file`/`hash`. Always resolve the exact file
through `index.json`, not by guessing a filename pattern.

## Local machine path

On this machine, a clone of `bible_versions` already exists at:

```
/home/develop4god/Projects/bible_versions/
```

DBs live under `<lang>/<VERSION>_<lang>.SQLite3.gz`, e.g.:

```
/home/develop4god/Projects/bible_versions/de/LU17_de.SQLite3.gz
/home/develop4god/Projects/bible_versions/en/KJV_en.SQLite3.gz
```

Whether you need to decompress depends on which tool you're using:

- **`build_seed_for_language.py` (via `VerseResolver`)** accepts the `.gz`
  directly — no decompression needed:
  ```bash
  python seed_generation/tools/build_seed_for_language.py \
      --source-seed seed_generation/2027/seeds/EN/seed_en_KJV_for_2027.json \
      --db /home/develop4god/Projects/bible_versions/de/LU17_de.SQLite3.gz \
      --lang de \
      --out seed_generation/2027/seeds/DE/seed_de_LU17_for_2027.json
  ```
- **`sanitize_seed_citations.py`** connects with `sqlite3.connect` directly
  and does not decompress — it needs a real `.SQLite3` file:
  ```bash
  gunzip -k -c /home/develop4god/Projects/bible_versions/de/LU17_de.SQLite3.gz \
    > seed_generation/tools/Bibles/DE/LU17_de.SQLite3
  ```

Verify you have the right file before trusting it — compare against
`index.json`'s declared hash for that version:

```bash
sha256sum /home/develop4god/Projects/bible_versions/de/LU17_de.SQLite3.gz | cut -c1-16
# compare against index.json -> languages.de.versions.LU17.hash
```

## Web fetch (no local clone)

If the local clone isn't available (a different machine, CI), pull directly
from the repo's raw URLs instead of guessing a path:

```bash
curl -sL "https://raw.githubusercontent.com/develop4God/bible_versions/main/index.json" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['languages']['de']['versions']['LU17']['url'])"

curl -sL "<url from above>" | gunzip > seed_generation/tools/Bibles/DE/LU17_de.SQLite3
```

`seed_extractor_fetch.py` already automates this end-to-end
(`fetch_versions_index()` + `find_or_download_db()`): it checks for the DB
locally first and downloads it from `bible_versions` automatically if
missing — no flag needed, and no need to re-implement curl calls by hand.

## What NOT to do

- Don't copy a DB into a scratch/temp directory and leave it there as the
  "working copy" for a one-off script run — extract straight into
  `seed_generation/tools/Bibles/<LANG>/` (or the language subfolder matching
  existing conventions) so it's reusable and discoverable next time.
- Don't hardcode a version's filename or URL inline in a script — read it
  from `index.json` so a future hash/URL change in `bible_versions` doesn't
  require hunting through this codebase.
