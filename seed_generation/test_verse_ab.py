"""
test_verse_ab.py — A/B test: devotional generation with and without the verse text.

A: today's pipeline prompt (citation only; the model recalls the verse itself).
B: the same prompt plus the verse text from the seed.

Same parser, ContentBuilder and length bounds as test_generate_ollama.py.
Honors OLLAMA_URL / OLLAMA_TIMEOUT (see test_generate_ollama.py).

Usage:
  python3 -m seed_generation.test_verse_ab [--limit N] [--model gemma4:12b] [--out FILE]
"""

import argparse
import json
import re
import time

from seed_generation.shared.generation_core import (
    ContentBuilder,
    DevotionalValidationError,
)
from seed_generation.shared.language_length_bounds import get_length_bounds
from seed_generation.test_generate_ollama import (
    DEFAULT_SEED,
    MASTER_LANG,
    MASTER_VERSION,
    build_prompt,
    call_ollama,
    parse_content,
)


def build_prompt_with_verse(cita: str, texto: str, lang: str) -> str:
    return build_prompt(cita, lang) + f'\n\nThe text of the key verse is: "{texto}"'


def _words(text: str) -> set:
    return set(re.findall(r"\w+", text.lower()))


def verse_overlap(verse: str, reflexion: str) -> float:
    verse_words = _words(verse)
    return (
        len(verse_words & _words(reflexion)) / len(verse_words) if verse_words else 0.0
    )


def run_scenario(name, prompt, model, date_key, seed_entry):
    row = {"scenario": name, "date": date_key, "prompt_chars": len(prompt)}
    t0 = time.time()
    result = call_ollama(model, prompt)
    row["seconds"] = round(time.time() - t0, 1)
    row["tokens"] = result.get("eval_count", 0)
    row["prompt_tokens"] = result.get("prompt_eval_count", 0)
    dur = result.get("eval_duration", 0) / 1e9
    row["tok_s"] = round(row["tokens"] / dur, 2) if dur else 0

    try:
        reflexion, oracion = parse_content(result.get("response", ""))
    except ValueError as e:
        row.update(format_ok=False, error=str(e), raw=result.get("response", "")[:500])
        return row
    row["format_ok"] = True

    bounds = get_length_bounds(MASTER_LANG)
    row["reflexion_chars"] = len(reflexion)
    row["oracion_chars"] = len(oracion)
    row["reflexion_in_bounds"] = (
        bounds.reflexion_min <= len(reflexion) <= bounds.reflexion_max
    )
    row["oracion_in_bounds"] = bounds.oracion_min <= len(oracion) <= bounds.oracion_max
    row["ends_amen"] = oracion.rstrip(" .").lower().endswith("amén")
    row["verse_word_overlap"] = round(
        verse_overlap(seed_entry["versiculo"]["texto"], reflexion), 2
    )
    try:
        ContentBuilder(date_key, seed_entry, MASTER_LANG, MASTER_VERSION).merge(
            {"reflexion": reflexion, "oracion": oracion}
        ).build()
        row["builder_valid"] = True
    except DevotionalValidationError as e:
        row.update(builder_valid=False, error=str(e))
    row["reflexion"] = reflexion
    row["oracion"] = oracion
    return row


def main():
    parser = argparse.ArgumentParser(description="Verse vs no-verse A/B test")
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--model", default="gemma4:12b")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--out", default="verse_ab_results.json")
    args = parser.parse_args()

    with open(args.seed, encoding="utf-8") as f:
        seed = json.load(f)

    rows = []
    for date_key in sorted(seed)[: args.limit]:
        entry = seed[date_key]
        cita, texto = entry["versiculo"]["cita"], entry["versiculo"]["texto"]
        for name, prompt in (
            ("A_no_verse", build_prompt(cita, MASTER_LANG)),
            ("B_with_verse", build_prompt_with_verse(cita, texto, MASTER_LANG)),
        ):
            print(f"{date_key} {cita} [{name}] ...", flush=True)
            row = run_scenario(name, prompt, args.model, date_key, entry)
            rows.append(row)
            summary = {
                k: v for k, v in row.items() if k not in ("reflexion", "oracion")
            }
            print(f"  {summary}", flush=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)

    print(f"Results -> {args.out}")


if __name__ == "__main__":
    main()
