"""
test_generate_ollama.py — Standalone quality-test script for a local Ollama model.

Generates devotionals (reflexion + oracion) for a small seed slice, in-process,
without running the FastAPI server. Same prompt shape and ContentBuilder
as API_Server_Seed.py / client_generate_from_seed.py, so output is directly
comparable to Gemini/Claude runs.

Usage:
  python3 test_generate_ollama.py [--limit N] [--model gemma4:26b]

Requires Ollama running locally (http://localhost:11434) with the model pulled.
"""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request

from seed_generation.shared.generation_core import (
    ContentBuilder,
    DevotionalValidationError,
)

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:26b"
DEFAULT_SEED = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "2026",
    "seeds",
    "ES",
    "seed_es_RVR1960_ollama_test.json",
)
MASTER_LANG = "es"
MASTER_VERSION = "RVR1960"


def build_prompt(verse_cita: str, lang: str) -> str:
    return "\n\n".join(
        [
            f"You are a devoted biblical devotional writer. "
            f'Write a christian devotional in {lang.upper()} based on the key verse: "{verse_cita}".',
            "Write in a simple, warm, pastoral tone. "
            "State ideas affirmatively — express what is true and present, "
            "not what is absent, false, or being denied. "
            "Avoid 'not X, but Y' style contrast constructions,"
            "Return ONLY a valid JSON object with these exact keys:",
            f"- `reflexion`: contextualized reflection on the verse "
            f"(minimum 900 characters, in {lang}).",
            f"- `oracion`: Prayer on the devotional theme (minimum 150 words, 100% in {lang}), "
            f"MUST end with the standard closing phrase 'in the name of Jesus, amen', "
            f"written entirely in {lang} (do not mix in any English words). "
            f"Write this closing phrase exactly ONCE, as the very last words of the prayer.",
            f"RULES:\n"
            f"- ALL text MUST be 100% in {lang} — no language mixing.\n"
            f"- Do NOT include transliterations, romanizations, or text in parentheses.",
        ]
    )


def call_ollama(model: str, prompt: str, timeout: int = 180) -> dict:
    payload = json.dumps(
        {"model": model, "prompt": prompt, "think": False, "stream": False}
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_content(raw_text: str) -> tuple[str, str]:
    raw = raw_text.strip().replace("```json", "").replace("```", "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in Ollama response")
    data = json.loads(match.group())
    reflexion = data.get("reflexion", "").strip()
    oracion = data.get("oracion", "").strip()
    if not reflexion or not oracion:
        raise ValueError("Empty reflexion or oracion in Ollama response")
    return reflexion, oracion


def main():
    parser = argparse.ArgumentParser(description="Ollama devotional generation test")
    parser.add_argument("--seed", default=DEFAULT_SEED, help="Path to seed JSON")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag")
    parser.add_argument("--limit", type=int, default=5, help="Max entries to generate")
    args = parser.parse_args()

    with open(args.seed, encoding="utf-8") as f:
        seed = json.load(f)

    dates = sorted(seed.keys())[: args.limit]
    print(f"Model: {args.model} | Entries: {len(dates)} | Seed: {args.seed}\n")

    completed = {}
    for i, date_key in enumerate(dates, 1):
        seed_entry = seed[date_key]
        cita = seed_entry["versiculo"]["cita"]
        print(f"[{i}/{len(dates)}] {date_key} — {cita}")

        prompt = build_prompt(cita, MASTER_LANG)
        t0 = time.time()
        try:
            result = call_ollama(args.model, prompt)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  ERROR — cannot reach Ollama: {e}\n")
            continue
        elapsed = time.time() - t0

        try:
            reflexion, oracion = parse_content(result.get("response", ""))
        except ValueError as e:
            print(f"  ERROR — {e}\n  raw: {result.get('response', '')[:300]}\n")
            continue

        eval_count = result.get("eval_count", 0)
        eval_duration = result.get("eval_duration", 0) / 1e9
        tok_s = eval_count / eval_duration if eval_duration else 0
        print(
            f"  OK — {elapsed:.1f}s | {eval_count} tokens | {tok_s:.1f} tok/s | "
            f"reflexion {len(reflexion)} chars | oracion {len(oracion)} chars"
        )

        try:
            builder = ContentBuilder(date_key, seed_entry, MASTER_LANG, MASTER_VERSION)
            devotional = builder.merge(
                {"reflexion": reflexion, "oracion": oracion}
            ).build()
            completed[date_key] = devotional
        except DevotionalValidationError as e:
            print(f"  Validation error: {e}")
            continue

        print()

    if not completed:
        print("No devotionals generated.")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(args.seed))
    out_path = os.path.join(
        out_dir, f"ollama_test_{args.model.replace(':', '-')}_{ts}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"data": {MASTER_LANG: {d: [v] for d, v in completed.items()}}},
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("=" * 60)
    print(f"Generated {len(completed)}/{len(dates)} devotionals")
    print(f"Output -> {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
