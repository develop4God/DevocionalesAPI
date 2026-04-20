"""
build_batch.py — GEP Critic v3
Single responsibility: build a Fireworks-compatible batch JSONL from a devotional JSON file.

Usage:
    python3 build_batch.py --lang tl --version ASND --year 2026
    python3 build_batch.py --lang tl --version ASND --year 2026 --local path/to/file.json
    python3 build_batch.py --lang tl --version ASND --year 2026 --skip-reviewed

Output:
    batch_input_tl_ASND_2026.jsonl  — ready to upload to Fireworks Batch API

Each line is a self-contained request:
  {
    "custom_id": "<entry.id>",
    "body": {
      "model": "<model>",
      "max_tokens": 1024,
      "temperature": 0.1,
      "messages": [
        {"role": "system", "content": "<system_prompt>"},
        {"role": "user",   "content": "<user_prompt>"}
      ]
    }
  }
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

# ── Project imports ───────────────────────────────────────────────────────────
from audit  import audit_path, load_reviewed_dates
from genome import ensure_genome
from models import DevotionalEntry
from prompts import build_phase2_system, build_phase2_user

# ── Config ────────────────────────────────────────────────────────────────────

GITHUB_URL = (
    "https://raw.githubusercontent.com/develop4God/devocionales-json"
    "/refs/heads/main/Devocional_year_{year}_{lang}_{version}.json"
)

# Default model — qwen2p5-72b is best multilingual on Fireworks
DEFAULT_MODEL = "accounts/fireworks/models/qwen2p5-72b-instruct"


# ── Entry loading ─────────────────────────────────────────────────────────────

def load_entries(lang: str, version: str, year: int, local: str | None) -> list[DevotionalEntry]:
    if local:
        print(f"  📂 Loading local: {local}")
        with open(local, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        url = GITHUB_URL.format(year=year, lang=lang, version=version)
        print(f"  📡 Fetching: {url}")
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = json.load(resp)

    entries = []
    for item in raw:
        entries.append(DevotionalEntry(
            date         = item.get("date", item.get("fecha", "")),
            id           = item.get("id", ""),
            language     = lang,
            version      = version,
            versiculo    = item.get("versiculo", ""),
            reflexion    = item.get("reflexion", ""),
            oracion      = item.get("oracion", ""),
            para_meditar = item.get("para_meditar", []),
            tags         = item.get("tags", []),
        ))
    print(f"  ✅ {len(entries)} entries loaded")
    return entries


# ── JSONL builder ─────────────────────────────────────────────────────────────

def build_batch(
    lang: str,
    version: str,
    year: int,
    entries: list[DevotionalEntry],
    reviewed: set[str],
    genome,
    model: str,
    skip_reviewed: bool,
) -> list[dict]:
    """
    Builds one JSONL record per entry.
    System prompt is built once and reused — Fireworks caches it automatically.
    """
    # Build system prompt once (static across all entries — cache hit on Fireworks)
    system_prompt = build_phase2_system(
        lang    = lang,
        version = version,
        genome  = genome,
        phase1_result = None,   # no P1 in batch mode
    )

    records = []
    skipped = 0

    for entry in entries:
        if skip_reviewed and entry.date in reviewed:
            skipped += 1
            continue

        user_prompt = build_phase2_user(entry, lang)

        record = {
            "custom_id": entry.id,
            "body": {
                "model": model,
                "max_tokens": 1024,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
            },
        }
        records.append(record)

    if skipped:
        print(f"  ⏭️  Skipped {skipped} already-reviewed entries")

    return records


# ── Output ────────────────────────────────────────────────────────────────────

def write_jsonl(records: list[dict], path: Path):
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    size_kb = path.stat().st_size / 1024
    print(f"  💾 Written: {path}  ({len(records)} records, {size_kb:.1f} KB)")


# ── Token estimate ────────────────────────────────────────────────────────────

def estimate_cost(records: list[dict], model: str):
    """
    Rough token estimate. System prompt tokens counted once (cached after first request).
    Uses char/4 heuristic — good enough for cost planning.
    """
    if not records:
        return

    system_chars = len(records[0]["body"]["messages"][0]["content"])
    user_chars   = sum(len(r["body"]["messages"][1]["content"]) for r in records)
    output_chars = len(records) * 200   # ~200 chars per JSON verdict

    system_tokens = system_chars // 4
    user_tokens   = user_chars   // 4
    output_tokens = output_chars // 4

    # Fireworks pricing (approx, May 2025)
    # qwen2p5-72b: $0.90/1M input, $0.90/1M output (real-time)
    # Batch: 50% off → $0.45/1M
    # Cached tokens: 50% off cached → $0.225/1M for system prompt after first hit
    price_input_per_m  = 0.45   # batch rate
    price_cached_per_m = 0.225  # cached system prompt
    price_output_per_m = 0.45

    n = len(records)
    # System prompt: first request full price, rest cached
    system_cost = (system_tokens / 1_000_000) * price_input_per_m
    system_cost += ((n - 1) * system_tokens / 1_000_000) * price_cached_per_m
    user_cost    = (user_tokens   / 1_000_000) * price_input_per_m
    output_cost  = (output_tokens / 1_000_000) * price_output_per_m
    total        = system_cost + user_cost + output_cost

    print(f"\n  📊 Token estimate ({n} entries):")
    print(f"     System prompt : ~{system_tokens:,} tokens (cached after first request)")
    print(f"     User prompts  : ~{user_tokens:,} tokens total")
    print(f"     Output        : ~{output_tokens:,} tokens total")
    print(f"     Est. cost     : ~${total:.3f} USD  (batch + cache rates)")
    print(f"     Model         : {model}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GEP Critic — Build Fireworks Batch JSONL")
    parser.add_argument("--lang",     required=True, help="Language code (tl, es, pt, ...)")
    parser.add_argument("--version",  required=True, help="Bible version (ASND, NVI, ...)")
    parser.add_argument("--year",     required=True, type=int, help="Year (2025, 2026, ...)")
    parser.add_argument("--model",    default=DEFAULT_MODEL, help="Fireworks model ID")
    parser.add_argument("--local",    metavar="FILE", help="Use local JSON file instead of GitHub")
    parser.add_argument("--skip-reviewed", action="store_true",
                        help="Skip entries already in the audit log")
    parser.add_argument("--output",   metavar="FILE", help="Output JSONL path (default: auto)")
    args = parser.parse_args()

    print(f"\n{'═'*60}")
    print(f"  🏗️  GEP Batch Builder")
    print(f"  Lang: {args.lang} | Version: {args.version} | Year: {args.year}")
    print(f"  Model: {args.model}")
    print(f"{'═'*60}")

    # Load entries
    entries = load_entries(args.lang, args.version, args.year, args.local)

    # Load reviewed dates (for --skip-reviewed)
    log_path = audit_path(args.lang, args.version, args.year)
    reviewed = load_reviewed_dates(log_path) if args.skip_reviewed else set()
    if args.skip_reviewed:
        print(f"  📋 {len(reviewed)} entries already reviewed — will skip")

    # Load genome (injects confirmed fragments into system prompt)
    genome = ensure_genome(args.lang, args.version, args.year)
    frag_count = len(genome.fragments) if genome else 0
    print(f"  🧬 Genome: {frag_count} fragments")

    # Build JSONL records
    records = build_batch(
        lang           = args.lang,
        version        = args.version,
        year           = args.year,
        entries        = entries,
        reviewed       = reviewed,
        genome         = genome,
        model          = args.model,
        skip_reviewed  = args.skip_reviewed,
    )

    if not records:
        print("  ✅ Nothing to build — all entries already reviewed.")
        sys.exit(0)

    print(f"  📦 {len(records)} entries → batch")

    # Cost estimate
    estimate_cost(records, args.model)

    # Write output
    out_path = Path(args.output) if args.output else \
               Path(f"batch_input_{args.lang}_{args.version}_{args.year}.jsonl")
    write_jsonl(records, out_path)

    print(f"\n  Next step:")
    print(f"    Upload to Fireworks and submit batch job.")
    print(f"    Then run: python3 collect_batch.py --input {out_path} --results <downloaded.jsonl> \\")
    print(f"              --lang {args.lang} --version {args.version} --year {args.year}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
