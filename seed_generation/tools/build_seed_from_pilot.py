"""
build_seed_from_pilot.py
──────────────────────────
Takes a pilot-format verse list (reference / related / primary_tag /
secondary_tags — see seed_generation/data/sprint1_pilot.json) and resolves
each citation against a KJV SQLite DB to build a seed file in the same
shape as the existing per-language seeds (versiculo / para_meditar / tags),
with sequential calendar dates assigned starting from --start-date.

Usage:
  python build_seed_from_pilot.py \\
      --pilot seed_generation/data/sprint1_pilot.json \\
      --db seed_generation/Bibles/KJV_en.SQLite3 \\
      --start-date 2027-01-01 \\
      --out seed_generation/2027/seeds/EN/seed_en_KJV_for_2027.json
"""

import argparse
import json
from datetime import date, timedelta

from bible_text_normalizer import clean_resolved as clean
from verse_resolver import VerseResolver

from seed_generation.shared.generation_core import build_devotional_seed_entry


def build_seed(pilot_path: str, db_path: str, start_date: str, out_path: str) -> None:
    with open(pilot_path, encoding="utf-8") as f:
        pilot = json.load(f)

    seed = {}
    errors = []
    current_date = date.fromisoformat(start_date)

    with VerseResolver(db_path) as resolver:
        for entry in pilot:
            cita, texto, err = resolver.resolve(entry["reference"])
            if err:
                errors.append({"reference": entry["reference"], "error": err})
                continue
            texto = clean(texto)

            para_meditar = []
            for ref in entry["related"]:
                r_cita, r_texto, r_err = resolver.resolve(ref)
                if r_err:
                    errors.append({"reference": ref, "error": r_err})
                    continue
                para_meditar.append({"cita": r_cita, "texto": clean(r_texto)})

            tags = [entry["primary_tag"]] + entry.get("secondary_tags", [])

            date_key = current_date.isoformat()
            seed[date_key] = build_devotional_seed_entry(
                cita, texto, para_meditar, tags
            )
            current_date += timedelta(days=1)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(seed)} seed entries -> {out_path}")
    if errors:
        print(f"WARNING: {len(errors)} resolution errors:")
        for e in errors:
            print(f"  {e['reference']}: {e['error']}")


def main():
    parser = argparse.ArgumentParser(
        description="Build a KJV seed file from a pilot verse list"
    )
    parser.add_argument(
        "--pilot", required=True, help="Path to pilot JSON (reference/related/tags)"
    )
    parser.add_argument("--db", required=True, help="Path to KJV SQLite DB")
    parser.add_argument("--start-date", required=True, help="First date YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output seed file path")
    args = parser.parse_args()

    build_seed(args.pilot, args.db, args.start_date, args.out)


if __name__ == "__main__":
    main()
