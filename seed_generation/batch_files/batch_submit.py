"""
batch_submit.py
───────────────
STEP 1 — Submit a full seed file for generation using any configured provider.

Replaces batch_claude_submit.py with a provider-agnostic version.
Provider and model are selected via --provider / --model flags (or providers.yml defaults).

CLI:
  python batch_submit.py \\
         --seed    2025/seeds/seed_tl_ADB_for_2025.json \\
         --lang    tl \\
         --version ADB \\
         --output  2025/yearly_devotionals/TL \\
         --provider gemini \\
         --model   gemini-2.0-flash

  # Dry run — write agnostic prompt JSONL, no API call, no cost:
  python batch_submit.py \\
         --seed 2025/seeds/seed_tl_ADB_for_2025.json \\
         --lang tl --version ADB --output 2025/yearly_devotionals/TL \\
         --dry-run

  # List all available providers and models:
  python batch_submit.py --list-providers

State file saved as:   batch_state_<lang>_<version>_<provider>_<ts>.json
Dry-run file saved as: prompts_<lang>_<version>_<ts>.jsonl  (in output dir)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pipeline_shared import build_prompt
from provider_adapter import load_adapter, list_providers, BatchRequest

_SCRIPT_DIR = Path(__file__).parent


def _safe_custom_id(date_key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", date_key)[:64]


def dry_run(
    seed_path:      str,
    master_lang:    str,
    master_version: str,
    output_dir:     str,
    start_date:     str | None = None,
    limit:          int | None = None,
) -> str:
    """
    Build prompts from seed and write a provider-agnostic JSONL to output_dir.
    No provider API is called. No cost. Use this to review prompts before submitting.

    Each line: {custom_id, date, lang, version, verse, prompt}
    """
    SEP = "=" * 60

    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    all_dates = sorted(seed.keys())
    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
    if limit:
        all_dates = all_dates[:limit]

    total = len(all_dates)

    print("\n" + SEP)
    print("BATCH DRY RUN  (no API call — prompt review only)")
    print(SEP)
    print(f"  Seed       : {seed_path}")
    print(f"  Lang       : {master_lang}  Version: {master_version}")
    print(f"  Entries    : {total}")
    if start_date: print(f"  Start date : {start_date}")
    if limit:      print(f"  Limit      : {limit}")
    print(SEP + "\n")

    if total == 0:
        print("ERROR: No seed entries after filtering. Aborting.")
        sys.exit(1)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(output_dir) / f"prompts_{master_lang}_{master_version}_{ts}.jsonl"

    with open(out_path, "w", encoding="utf-8") as f:
        for date_key in all_dates:
            entry = seed[date_key]
            cita  = entry["versiculo"]["cita"]
            topic = entry.get("topic")
            record = {
                "custom_id": _safe_custom_id(date_key),
                "date":      date_key,
                "lang":      master_lang,
                "version":   master_version,
                "verse":     cita,
                "prompt":    build_prompt(cita, master_lang, topic),
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"✅ Dry-run JSONL → {out_path}")
    print(f"   {total} prompts — no model, no provider, no cost")
    print(f"\nReview the file, then submit for real:")
    print(f"  python batch_submit.py \\")
    print(f"    --seed {seed_path} \\")
    print(f"    --lang {master_lang} --version {master_version} \\")
    print(f"    --output {output_dir} \\")
    print(f"    --provider <gemini|anthropic|fireworks> [--model <alias>]")
    print(SEP + "\n")

    return str(out_path)


def dry_run_full(
    seed_path:      str,
    master_lang:    str,
    master_version: str,
    provider:       str,
    model_alias:    str | None = None,
) -> None:
    """
    Show the exact wire payload for the first seed entry — what the
    adapter actually sends to the provider API.
    No API call. No upload. No cost.
    """
    SEP = "=" * 60

    adapter = load_adapter(provider, model_alias)

    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    first_date = sorted(seed.keys())[0]
    entry = seed[first_date]
    cita  = entry["versiculo"]["cita"]
    topic = entry.get("topic")

    req = BatchRequest(
        date_key=first_date,
        custom_id=_safe_custom_id(first_date),
        prompt=build_prompt(cita, master_lang, topic),
        model_id=adapter.model_id,
    )

    print("\n" + SEP)
    print("DRY RUN FULL — exact wire payload (1 sample entry)")
    print(SEP)
    print(f"  Provider : {provider}")
    print(f"  Model    : {adapter.model_id}  (quality={adapter.quality})")
    print(f"  Seed     : {seed_path}")
    print(f"  Entry    : {first_date}  |  Verse: {cita}")
    print(SEP)

    if hasattr(adapter, "_to_jsonl_line"):
        wire = json.loads(adapter._to_jsonl_line(req))
        print("\n--- WIRE PAYLOAD (as sent to provider) ---")
        print(json.dumps(wire, ensure_ascii=False, indent=2))
    else:
        print(f"\n⚠️  Adapter '{provider}' has no _to_jsonl_line().")
        print("   Inspect its submit() method manually.")
        print(f"\n--- PROMPT CONTENT (agnostic) ---")
        print(req.prompt[:500] + ("..." if len(req.prompt) > 500 else ""))

    print("\n--- PROMPT TEXT (truncated to 300 chars) ---")
    print(req.prompt[:300] + ("..." if len(req.prompt) > 300 else ""))
    print(SEP + "\n")


def submit_batch(
    seed_path:      str,
    master_lang:    str,
    master_version: str,
    output_dir:     str,
    provider:       str,
    model_alias:    str | None = None,
    start_date:     str | None = None,
    limit:          int | None = None,
) -> str:
    """
    Load seed, build BatchRequests, submit via adapter, save state file.
    Returns path to state JSON file.
    """
    SEP = "=" * 60

    # ── Load adapter ───────────────────────────────────────────────────────
    adapter = load_adapter(provider, model_alias)

    # ── Load seed ──────────────────────────────────────────────────────────
    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)

    all_dates = sorted(seed.keys())
    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
    if limit:
        all_dates = all_dates[:limit]

    total = len(all_dates)

    print("\n" + SEP)
    print("BATCH SUBMIT")
    print(SEP)
    print(f"  Provider   : {provider}")
    print(f"  Model      : {adapter.model_id}  (quality={adapter.quality})")
    print(f"  Seed       : {seed_path}")
    print(f"  Lang       : {master_lang}  Version: {master_version}")
    print(f"  Entries    : {total}")
    if start_date: print(f"  Start date : {start_date}")
    if limit:      print(f"  Limit      : {limit}")
    print(SEP + "\n")

    if total == 0:
        print("ERROR: No seed entries after filtering. Aborting.")
        sys.exit(1)

    # ── Build BatchRequest objects ─────────────────────────────────────────
    requests: list[BatchRequest] = []
    for date_key in all_dates:
        seed_entry = seed[date_key]
        cita  = seed_entry["versiculo"]["cita"]
        topic = seed_entry.get("topic")
        requests.append(BatchRequest(
            date_key=date_key,
            custom_id=_safe_custom_id(date_key),
            prompt=build_prompt(cita, master_lang, topic),
            model_id=adapter.model_id,
        ))

    # ── Submit ─────────────────────────────────────────────────────────────
    print(f"INFO: Submitting {total} requests via {provider}...")
    job_id = adapter.submit(requests)

    # ── Save state file ────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    state_filename = f"batch_state_{master_lang}_{master_version}_{provider}_{ts}.json"
    state_path = _SCRIPT_DIR / state_filename

    state = {
        "job_id":          job_id,
        "provider":        provider,
        "batch_strategy":  adapter.batch_strategy,
        "model_alias":     model_alias or "default",
        "model_id":        adapter.model_id,
        "seed_path":       str(seed_path),
        "master_lang":     master_lang,
        "master_version":  master_version,
        "output_dir":      str(output_dir),
        "dates":           all_dates,
        "total":           total,
        "submitted_at":    datetime.now().isoformat(),
    }

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"\nState file → {state_path}")

    if adapter.batch_strategy == "openai_batch_file":
        print(f"\nNext step (after downloading Fireworks results JSONL):")
        print(f"  python batch_collect.py --state {state_path} --results <results.jsonl>")
    else:
        print(f"\nNext step:")
        print(f"  python batch_collect.py --state {state_path}")
    print(SEP + "\n")

    return str(state_path)


def main():
    parser = argparse.ArgumentParser(
        description="Submit seed to any AI provider for devotional generation (Step 1/2)."
    )
    parser.add_argument("--seed",       type=str)
    parser.add_argument("--lang",       type=str)
    parser.add_argument("--version",    type=str)
    parser.add_argument("--output",     type=str)
    parser.add_argument("--provider",   type=str, default="anthropic",
                        help="Provider name (anthropic | gemini | fireworks)")
    parser.add_argument("--model",      type=str, default=None,
                        help="Model alias from providers.yml (uses provider default if omitted)")
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--limit",      type=int, default=None)
    parser.add_argument("--dry-run",    action="store_true",
                        help="Write agnostic prompt JSONL to output dir — no API call, no cost")
    parser.add_argument("--dry-run-full", action="store_true",
                        help="Show exact provider wire payload for 1 entry — no API call, no cost")
    parser.add_argument("--list-providers", action="store_true",
                        help="Print all configured providers and models, then exit")
    args = parser.parse_args()

    if args.list_providers:
        print("\nConfigured providers and models (from providers.yml):\n")
        for prov, models in list_providers().items():
            print(f"  {prov}:")
            for m in models:
                print(f"    --provider {prov} --model {m}")
        print()
        return

    if not all([args.seed, args.lang, args.version, args.output]):
        parser.print_help()
        print("\nERROR: --seed, --lang, --version, --output are all required.")
        sys.exit(1)

    if args.dry_run:
        dry_run(
            seed_path      = args.seed,
            master_lang    = args.lang,
            master_version = args.version,
            output_dir     = args.output,
            start_date     = args.start_date,
            limit          = args.limit,
        )
        return

    if args.dry_run_full:
        dry_run_full(
            seed_path   = args.seed,
            master_lang = args.lang,
            master_version = args.version,
            provider    = args.provider,
            model_alias = args.model,
        )
        return

    submit_batch(
        seed_path      = args.seed,
        master_lang    = args.lang,
        master_version = args.version,
        output_dir     = args.output,
        provider       = args.provider,
        model_alias    = args.model,
        start_date     = args.start_date,
        limit          = args.limit,
    )


if __name__ == "__main__":
    main()
