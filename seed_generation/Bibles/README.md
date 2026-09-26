# Bible databases

No Bible SQLite database is stored in this repo. `VerseResolver` (used by
`build_seed_for_language.py` and other seed_generation tools) works directly
with a `.gz` path — local or remote — so nothing needs to be extracted or
committed here.

## Source of truth

Repo: `https://github.com/develop4God/bible_versions`

`index.json` in that repo lists every version's file and URL, keyed by
language then version code:

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
`book_number` lookup (`VerseResolver.load_books_sot`'s default remote
source).

Resolve the exact file through `index.json` rather than guessing a name —
some versions look alike but aren't (`KJV` vs `KJ2000` are different files
with different hashes).

## Local machine path

```
/home/develop4god/Projects/bible_versions/<lang>/<VERSION>_<lang>.SQLite3.gz
```

```bash
python seed_generation/tools/build_seed_for_language.py \
    --source-seed seed_generation/2027/seeds/EN/seed_en_KJV_for_2027.json \
    --db /home/develop4god/Projects/bible_versions/de/LU17_de.SQLite3.gz \
    --lang de \
    --out seed_generation/2027/seeds/DE/seed_de_LU17_for_2027.json
```

## Remote (no local clone)

`--db` takes a local filesystem path, not a URL — download the `.gz` first
(no need to decompress it), then point `--db` at that path:

```bash
curl -sL "https://raw.githubusercontent.com/develop4God/bible_versions/main/de/LU17_de.SQLite3.gz" \
  -o /tmp/LU17_de.SQLite3.gz

python seed_generation/tools/build_seed_for_language.py \
    --source-seed seed_generation/2027/seeds/EN/seed_en_KJV_for_2027.json \
    --db /tmp/LU17_de.SQLite3.gz \
    --lang de \
    --out seed_generation/2027/seeds/DE/seed_de_LU17_for_2027.json
```
