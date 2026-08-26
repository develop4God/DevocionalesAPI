"""
generate_from_seed.py
──────────────────────
Unified, provider-swappable seed-driven devotional generator.

Takes an existing annual seed (verse citations, para_meditar, tags —
built separately by seed_extractor_fetch.py) and generates the
devotional content (reflexion + oracion) from it via a provider.

Replaces the old server/client split (API_Server_Seed.py +
client_generate_from_seed.py) for local/direct providers — no HTTP,
no server process. One prompt + parser (generation_core.build_prompt /
parse_content), one assembly step (generation_core.DevotionalBuilder),
any provider from providers.py — all in seed_generation/shared/.

Usage:
  python generate_from_seed.py --seed seeds/seed_es_RVR1960.json \\
      --lang es --version RVR1960 --provider ollama --model gemma4:26b \\
      --limit 5

  python generate_from_seed.py --seed seeds/seed_es_RVR1960.json \\
      --lang es --version RVR1960 --provider gemini --limit 5

--limit caps how many seed entries are generated — for a quick quality
test on e.g. 5 entries instead of running the full 365-entry seed.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import json
from datetime import datetime

from seed_generation.shared.generation_core import (
    DevotionalBuilder,
    DevotionalValidationError,
    CheckpointStore,
    build_prompt,
    parse_content,
    save_output,
)
from seed_generation.shared.providers import PROVIDERS, build_generator


def generate_from_seed(
    seed_path: str,
    master_lang: str,
    master_version: str,
    provider: str,
    output_dir: str,
    model: str | None = None,
    start_date: str | None = None,
    limit: int | None = None,
) -> None:
    SEP = "=" * 60
    print("\n" + SEP)
    print("SEED-DRIVEN GENERATOR")
    print(SEP)
    print(f"  Seed     : {seed_path}")
    print(f"  Lang     : {master_lang}  Version: {master_version}")
    print(f"  Provider : {provider}" + (f" ({model})" if model else ""))
    print(f"  Output   : {output_dir}")
    print(SEP + "\n")

    generator = build_generator(provider, model)

    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    all_dates = sorted(seed.keys())
    total = len(all_dates)
    print(f"INFO: {total} seed entries\n")

    os.makedirs(output_dir, exist_ok=True)

    checkpoint_file = os.path.join(
        output_dir,
        f"generate_from_seed_checkpoint_{model or provider}.json",
    )
    checkpoint = CheckpointStore(checkpoint_file)

    completed: dict = {}
    start_index = 0

    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
        if limit:
            all_dates = all_dates[:limit]
        total = len(all_dates)
        print(f"INFO: Partial mode — {total} dates | {all_dates[0]} -> {all_dates[-1]}")
    elif limit:
        all_dates = all_dates[:limit]
        total = len(all_dates)
        print(f"INFO: Limited to first {total} dates (--limit {limit})")
    else:
        data = checkpoint.load()
        if data and data.get("seed_path") == seed_path:
            ans = input(f"\nCheckpoint — {data['completed_count']} done. Resume? (y/n): ").strip().lower()
            if ans == "y":
                completed = data["completed"]
                start_index = data["completed_count"]
                print(f"INFO: Resuming from {start_index + 1}/{total}\n")

    interrupted = False

    def _sig(sig, frame):
        nonlocal interrupted
        print("\n\nCtrl+C — saving checkpoint...")
        interrupted = True

    signal.signal(signal.SIGINT, _sig)

    success_count = start_index
    error_count = 0
    error_dates = []

    print("-" * 60)

    for i in range(start_index, total):
        if interrupted:
            break

        date_key = all_dates[i]
        seed_entry = seed[date_key]
        cita = seed_entry["versiculo"]["cita"]

        print(f"\n[{i + 1}/{total}] {date_key} — {cita}")

        try:
            prompt = build_prompt(cita, master_lang)
            raw = generator.generate(prompt)
            reflexion, oracion = parse_content(raw)

            builder = DevotionalBuilder(date_key, seed_entry, master_lang, master_version)
            devotional = builder.merge(reflexion, oracion).build()
            completed[date_key] = devotional
            success_count += 1
            print(f"  OK — {len(reflexion)} chars | tags: {devotional['tags']}")

            checkpoint.save(completed, success_count, seed_path, master_lang, master_version, output_dir)

        except DevotionalValidationError as e:
            print(f"  Validation error: {e}")
            error_count += 1
            error_dates.append({"date": date_key, "cita": cita, "reason": str(e)})

        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"  Error: {msg}")
            error_count += 1
            error_dates.append({"date": date_key, "cita": cita, "reason": msg})

    if interrupted and completed:
        checkpoint.save(completed, success_count, seed_path, master_lang, master_version, output_dir)
        print("\nProgress saved. Run again to resume.")
        sys.exit(0)

    print("\n" + "-" * 60)

    if completed:
        out = save_output(completed, master_lang, master_version, output_dir, suffix=model or provider)
        print(f"\nOutput  -> {out}")
        checkpoint.delete()
        if error_dates:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            ep = os.path.join(
                output_dir,
                f"generation_errors_{master_lang}_{master_version}_{provider}_{ts}.json",
            )
            with open(ep, "w", encoding="utf-8") as f:
                json.dump(error_dates, f, ensure_ascii=False, indent=2)
            print(f"Errors  -> {ep}")
    else:
        print("No devotionals generated.")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Seed entries  : {total}")
    print(f"  Generated OK  : {success_count}")
    print(f"  Skipped       : {error_count}")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Provider-swappable seed-driven devotional generator")
    parser.add_argument("--seed", required=True, help="Path to seed JSON file")
    parser.add_argument("--lang", required=True, help="Language code, e.g. es")
    parser.add_argument("--version", required=True, help="Bible version code, e.g. RVR1960")
    parser.add_argument("--provider", required=True, choices=list(PROVIDERS), help="Generation provider")
    parser.add_argument("--model", default=None, help="Model name/tag override (provider-specific default if omitted)")
    parser.add_argument(
        "--output-dir", default=None,
        help="Output folder (default: seed_generation/data/output/<lang>/)",
    )
    parser.add_argument("--start-date", default=None, help="Start date YYYY-MM-DD (optional)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max seed entries to generate — e.g. 5 for a quick test instead of the full seed",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "seed_generation", "data", "output", args.lang,
    )

    generate_from_seed(
        seed_path=args.seed,
        master_lang=args.lang,
        master_version=args.version,
        provider=args.provider,
        output_dir=output_dir,
        model=args.model,
        start_date=args.start_date,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
