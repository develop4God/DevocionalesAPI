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
import json
import os
import signal
import sys
from datetime import datetime

from seed_generation.shared.generation_core import (
    CheckpointStore,
    DevotionalBuilder,
    DevotionalValidationError,
    build_prompt,
    checkpoint_path_for,
    checkpoint_status,
    parse_content,
    pending_dates,
    save_output,
    slugify_identifier,
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
    resume: bool | None = None,
) -> None:
    """
    resume: None = ask interactively when a matching checkpoint is found
            (default, current behavior). True = resume without asking —
            for unattended/background runs. False = ignore any checkpoint
            and start fresh, without asking.
    """
    os.makedirs(output_dir, exist_ok=True)
    checkpoint_file = checkpoint_path_for(output_dir, provider, model)

    SEP = "=" * 60
    print("\n" + SEP)
    print("SEED-DRIVEN GENERATOR")
    print(SEP)
    print(f"  Seed       : {seed_path}")
    print(f"  Lang       : {master_lang}  Version: {master_version}")
    print(f"  Provider   : {provider}" + (f" ({model})" if model else ""))
    print(f"  Output     : {output_dir}")
    print(f"  Checkpoint : {checkpoint_file}")
    print(SEP + "\n")

    generator = build_generator(provider, model)

    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    all_dates = sorted(seed.keys())
    total = len(all_dates)
    print(f"INFO: {total} seed entries\n")

    checkpoint = CheckpointStore(checkpoint_file)

    completed: dict = {}

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
    elif resume is False:
        checkpoint.delete()
    else:
        data = checkpoint.load()
        if data and data.get("seed_path") != seed_path:
            print(
                f"WARNING: Checkpoint found ({checkpoint_file}) but its seed_path "
                f"({data.get('seed_path')!r}) does not match the current --seed "
                f"({seed_path!r}) — ignoring it and starting fresh."
            )
        elif data:
            if resume is True:
                should_resume = True
            else:
                ans = (
                    input(
                        f"\nCheckpoint — {data['completed_count']} done. Resume? (y/n): "
                    )
                    .strip()
                    .lower()
                )
                should_resume = ans == "y"
            if should_resume:
                completed = data["completed"]
                print(f"INFO: Resuming — {len(completed)}/{total} already done\n")

    dates_to_run = pending_dates(all_dates, completed)

    interrupted = False

    def _sig(sig, frame):
        nonlocal interrupted
        print("\n\nCtrl+C — saving checkpoint...")
        interrupted = True

    signal.signal(signal.SIGINT, _sig)

    already_done = len(completed)
    success_count = already_done
    error_count = 0
    error_dates = []

    print("-" * 60)

    for i, date_key in enumerate(dates_to_run):
        if interrupted:
            break

        seed_entry = seed[date_key]
        cita = seed_entry["versiculo"]["cita"]

        print(f"\n[{already_done + i + 1}/{total}] {date_key} — {cita}")

        try:
            prompt = build_prompt(cita, master_lang)
            raw = generator.generate(prompt)
            reflexion, oracion = parse_content(raw)

            builder = DevotionalBuilder(
                date_key, seed_entry, master_lang, master_version
            )
            devotional = builder.merge(reflexion, oracion).build()
            completed[date_key] = devotional
            success_count += 1
            print(f"  OK — {len(reflexion)} chars | tags: {devotional['tags']}")

            checkpoint.save(
                completed,
                success_count,
                seed_path,
                master_lang,
                master_version,
                output_dir,
                provider=provider,
                model=model,
            )

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
        checkpoint.save(
            completed,
            success_count,
            seed_path,
            master_lang,
            master_version,
            output_dir,
            provider=provider,
            model=model,
        )
        print("\nProgress saved. Run again to resume.")
        sys.exit(0)

    print("\n" + "-" * 60)

    if completed:
        out = save_output(
            completed,
            master_lang,
            master_version,
            output_dir,
            suffix=slugify_identifier(model or provider),
        )
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


def print_status(
    seed_path: str, provider: str, model: str | None, output_dir: str
) -> None:
    """Report a checkpoint's progress for this (seed, provider, model) without
    starting generation — for `--status`."""
    with open(seed_path, encoding="utf-8") as f:
        total = len(json.load(f))
    checkpoint_file = checkpoint_path_for(output_dir, provider, model)
    status = checkpoint_status(checkpoint_file, total=total, seed_path=seed_path)
    if status is None:
        print(f"No checkpoint found at {checkpoint_file}")
        print(f"({total} seed entries, 0 done)")
        return
    print(f"Checkpoint : {status['checkpoint_file']}")
    if status["seed_matches"]:
        print("Seed match : yes")
    else:
        print(
            f"Seed match : NO — checkpoint was for {status['checkpoint_seed_path']!r}"
        )
    print(f"Done       : {status['done']}/{status['total']}")
    print(f"Pending    : {status['pending']}")
    print(f"Last saved : {status['timestamp']}")


def main():
    parser = argparse.ArgumentParser(
        description="Provider-swappable seed-driven devotional generator"
    )
    parser.add_argument("--seed", required=True, help="Path to seed JSON file")
    parser.add_argument("--lang", required=True, help="Language code, e.g. es")
    parser.add_argument(
        "--version", required=True, help="Bible version code, e.g. RVR1960"
    )
    parser.add_argument(
        "--provider", required=True, choices=list(PROVIDERS), help="Generation provider"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name/tag override (provider-specific default if omitted)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output folder (default: seed_generation/data/output/<lang>/)",
    )
    parser.add_argument(
        "--start-date", default=None, help="Start date YYYY-MM-DD (optional)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max seed entries to generate — e.g. 5 for a quick test instead of the full seed",
    )
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--resume",
        "--continue",
        dest="resume",
        action="store_true",
        default=None,
        help="Resume from an existing checkpoint without the interactive prompt "
        "(for unattended/background runs). --continue is an alias, matching "
        "the convention used by git/yt-dlp/etc.",
    )
    resume_group.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Ignore and delete any existing checkpoint, start fresh without asking",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report checkpoint progress (done/pending) and exit — no generation runs",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "seed_generation",
        "data",
        "output",
        args.lang,
    )

    if args.status:
        print_status(args.seed, args.provider, args.model, output_dir)
        return

    generate_from_seed(
        seed_path=args.seed,
        master_lang=args.lang,
        master_version=args.version,
        provider=args.provider,
        output_dir=output_dir,
        model=args.model,
        start_date=args.start_date,
        limit=args.limit,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
