"""
build_seed_for_language.py
────────────────────────────
Takes an existing English seed file (citations + tags, no generated
content) and re-resolves every citation against a target-language
Bible DB, producing a seed file in the same date/shape but with native
citations and verse text.

Usage:
  python build_seed_for_language.py \\
      --source-seed seed_generation/2027/seeds/EN/seed_en_KJV_for_2027.json \\
      --db seed_generation/Bibles/ES/RVR1960_es.SQLite3 \\
      --lang es \\
      --out seed_generation/2027/seeds/ES/seed_es_RVR1960_for_2027.json
"""

import argparse
import json
import re
from pathlib import Path

from verse_resolver import VerseResolver

_TAGS_MASTER_PATH = Path(__file__).parent.parent / "tags_master.json"


def _normalize_tag(tag: str) -> str:
    return re.sub(r"[\s\-']", "", tag).lower()


def _load_tags_master() -> dict:
    with open(_TAGS_MASTER_PATH, encoding="utf-8") as f:
        return json.load(f)["tags"]


def _translate_tags(tags: list, lang: str, tags_master: dict) -> list:
    if lang == "en":
        return tags
    translated = []
    for tag in tags:
        entry = tags_master.get(_normalize_tag(tag))
        translated.append(entry[lang] if entry and lang in entry else tag)
    return translated

# Some MyBible-format DBs (e.g. RVR1960_es, ARC_pt) store Gospel/epistle
# long_names with a denominational "S." (San/Santo/São) prefix — "S. Mateo",
# "S.Juan", "1 S. Pedro" — that real citations don't use. The prefix can sit
# either at the very start ("S. João") or right after a leading book number
# ("1 S. Pedro"), so both positions must be matched. Stripped here rather
# than in verse_resolver.py to keep that file in sync with its upstream copy
# in devocionales-json.
_S_PREFIX = re.compile(r"^(\d\s+)?S\.\s*")


def _strip_s_prefix(cita: str) -> str:
    return _S_PREFIX.sub(lambda m: m.group(1) or "", cita)


def _capitalize_first_letter(texto: str) -> str:
    """Some DBs (e.g. RVR1960_es) store verses that grammatically continue
    from the previous verse in lowercase. Each verse is shown standalone
    in the seed, so its displayed text should start capitalized."""
    if not texto:
        return texto
    return texto[0].upper() + texto[1:]


def build_seed(source_seed_path: str, db_path: str, lang: str, out_path: str) -> None:
    with open(source_seed_path, encoding="utf-8") as f:
        source = json.load(f)

    tags_master = _load_tags_master()
    seed = {}
    errors = []

    with VerseResolver(db_path) as resolver:
        for date_key in sorted(source):
            entry = source[date_key]

            cita_en = entry["versiculo"]["cita"]
            cita, texto, err = resolver.resolve(cita_en)
            if err:
                errors.append({"date": date_key, "reference": cita_en, "error": err})
                continue
            cita = _strip_s_prefix(cita)
            texto = _capitalize_first_letter(texto)

            para_meditar = []
            for p in entry["para_meditar"]:
                r_cita, r_texto, r_err = resolver.resolve(p["cita"])
                if r_err:
                    errors.append({"date": date_key, "reference": p["cita"], "error": r_err})
                    continue
                para_meditar.append({
                    "cita": _strip_s_prefix(r_cita),
                    "texto": _capitalize_first_letter(r_texto),
                })

            seed[date_key] = {
                "versiculo": {"cita": cita, "texto": texto},
                "para_meditar": para_meditar,
                "tags": _translate_tags(entry["tags"], lang, tags_master),
            }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(seed)} seed entries -> {out_path}")
    if errors:
        print(f"WARNING: {len(errors)} resolution errors:")
        for e in errors:
            print(f"  {e['date']} {e['reference']}: {e['error']}")


def main():
    parser = argparse.ArgumentParser(description="Build a target-language seed from an existing English seed")
    parser.add_argument("--source-seed", required=True, help="Path to source English seed JSON")
    parser.add_argument("--db", required=True, help="Path to target-language SQLite Bible DB")
    parser.add_argument("--lang", required=True, help="Target language code, e.g. es, pt")
    parser.add_argument("--out", required=True, help="Output seed file path")
    args = parser.parse_args()

    build_seed(args.source_seed, args.db, args.lang, args.out)


if __name__ == "__main__":
    main()
