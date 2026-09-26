"""
sanitize_seed_citations.py
──────────────────────────
Rewrite the *book title* of every citation in an existing seed file using the
shared, data-driven book-name sanitizer (`book_name_normalizer` +
`book_name_sanitizers/<lang>.json`).

Why this exists
───────────────
No Bible SQLite DB exposes a single citation-ready title column:
  • `books.short_name`    → abbreviation only ("Joh", "Apg", "Mt", "Hi")
  • `books.long_name`     → liturgical long form ("Das Evangelium nach
                            Johannes", "Die Apostelgeschichte", "Die Psalmen")
  • `books_all.long_name` → repeats — and sometimes amplifies — the long form
So canonical titles are curated per language, keyed by the canonical MyBible
`book_number`. Seeds produced before a language config existed (or before it was
completed) therefore carry raw long forms. This pass repairs them using the DB
only to map raw title → book_number → canonical title; it never re-resolves
verse text and never touches dates, tags, entry counts or entry order.

It is deliberately decoupled from `verse_resolver`: adding or correcting a
language is a data change in `book_name_sanitizers/<lang>.json`, and this script
re-applies it to seeds generated before that change.

Usage
─────
  # repair in place (--db needs an uncompressed .SQLite3; see
  # seed_generation/Bibles/README.md for where to get one)
  python sanitize_seed_citations.py \\
      --seed seed_generation/2027/seeds/DE/seed_de_SCH2000_for_2027.json \\
      --db   /tmp/SCH2000_de.SQLite3 \\
      --lang de

  # preview without writing
  python sanitize_seed_citations.py --seed seed.json --db db.SQLite3 --lang de --dry-run

  # write to a different file
  python sanitize_seed_citations.py --seed seed.json --db db.SQLite3 --lang de --out fixed.json

Exit status is 0 on success, 1 if any citation title could not be matched to a
book in the DB (reported so the language config can be extended).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

try:
    from .book_name_normalizer import load_title_aliases, sanitize_book_name
except ImportError:  # Direct execution from seed_generation/tools
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from book_name_normalizer import load_title_aliases, sanitize_book_name

# "Johannes 3:16" / "1. Mose 1:1" / "Psalm 23:1-6" / "Die Psalmen 27:1"
_CITA = re.compile(r"^(?P<title>.+?)\s+(?P<loc>\d+:\d+(?:-\d+)?)$")


def build_title_map(db_path: str, language: str) -> dict[str, str]:
    """Map every raw `books.long_name` in *db_path* to its canonical title.

    Canonical titles are also inserted as identity entries, so the map doubles as
    the "already correct" whitelist: a citation that has already been sanitized
    resolves to itself and is reported as ``canonical`` instead of unmatched. That
    is what makes the pass idempotent and re-runnable on any seed.

    The language's optional ``aliases`` are merged last, so title-level variants
    that no DB column produces (see ``book_name_normalizer.load_title_aliases``)
    are normalized too, rather than being left unmatched.
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT book_number, long_name FROM books").fetchall()
    finally:
        conn.close()

    title_map = {
        long_name: sanitize_book_name(long_name, book_number, language)
        for book_number, long_name in rows
    }
    title_map.update({canonical: canonical for canonical in title_map.values()})
    title_map.update(load_title_aliases(language))
    return title_map


def sanitize_citation(cita: str, title_map: dict[str, str]) -> tuple[str, str]:
    """Return (citation, verdict), verdict being one of:

    ``changed`` / ``canonical`` / ``unconfigured`` / ``unparsed``
    """
    match = _CITA.match(cita.strip())
    if not match:
        return cita, "unparsed"

    title, loc = match.group("title"), match.group("loc")
    canonical = title_map.get(title)
    if canonical is None:
        return cita, "unconfigured"
    if canonical == title:
        return cita, "canonical"
    return f"{canonical} {loc}", "changed"


def sanitize_seed(seed: dict, title_map: dict[str, str]) -> tuple[dict, list, list]:
    """Rewrite every citation title in *seed*. Returns (seed, changes, problems)."""
    changes: list[dict] = []
    problems: list[dict] = []

    for date_key in sorted(seed):
        entry = seed[date_key]
        targets = [("versiculo", entry["versiculo"])]
        targets += [
            (f"para_meditar[{i}]", pm)
            for i, pm in enumerate(entry.get("para_meditar", []))
        ]

        for field, node in targets:
            before = node.get("cita", "")
            after, verdict = sanitize_citation(before, title_map)
            if verdict == "changed":
                node["cita"] = after
                changes.append(
                    {"date": date_key, "field": field, "before": before, "after": after}
                )
            elif verdict in ("unconfigured", "unparsed"):
                problems.append(
                    {"date": date_key, "field": field, "cita": before, "verdict": verdict}
                )

    return seed, changes, problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sanitize book titles in an existing seed's citations."
    )
    parser.add_argument("--seed", required=True, help="Seed JSON to sanitize")
    parser.add_argument("--db", required=True, help="Bible SQLite DB for the seed version")
    parser.add_argument("--lang", required=True, help="Language code (e.g. de, pt, hi)")
    parser.add_argument("--out", help="Write here instead of overwriting --seed")
    parser.add_argument("--dry-run", action="store_true", help="Report only, write nothing")
    args = parser.parse_args()

    with open(args.seed, encoding="utf-8") as f:
        seed = json.load(f)

    title_map = build_title_map(args.db, args.lang)
    seed, changes, problems = sanitize_seed(seed, title_map)

    print(f"  Seed      : {args.seed}")
    print(f"  DB        : {args.db}")
    print(f"  Language  : {args.lang}  ({len(title_map)} raw book titles known)")
    print(f"  Entries   : {len(seed)}")
    print(f"  Citations : changed={len(changes)}  problems={len(problems)}")

    for change in changes[:20]:
        print(f"    [{change['date']}] {change['before']}  ->  {change['after']}")
    if len(changes) > 20:
        print(f"    ... and {len(changes) - 20} more")
    for problem in problems:
        print(
            f"    [!] [{problem['date']}] {problem['field']}: "
            f"{problem['cita']!r} ({problem['verdict']})"
        )

    if args.dry_run:
        print("  (dry run - nothing written)")
        return 1 if problems else 0

    out_path = args.out or args.seed
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
    print(f"  Written   : {out_path}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())



