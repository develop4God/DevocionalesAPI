"""
build_batch.py — GEP Critic v3
Single responsibility: build a Fireworks-compatible batch JSONL from a devotional JSON file.

Usage:
    python3 build_batch.py --lang tl --version ASND --year 2026
    python3 build_batch.py --lang tl --version ASND --year 2026 --local path/to/file.json
    python3 build_batch.py --lang tl --version ASND --year 2026 --skip-reviewed
    python3 build_batch.py --lang en --version KJV  --year 2025 --phase 1
    python3 build_batch.py --lang en --version KJV  --year 2025 --phase 1,2

--phase values:
    2      Phase 2 content only (default — current behavior)
    1      Phase 1 linguistic only
    1,2    Both phases in one request (P1 system+user, then P2 system+user)
    1,2,3  Future-proof: extend as new phases are added

Output filenames:
    batch_input_tl_ASND_2026_p2.jsonl    (--phase 2, default)
    batch_input_en_KJV_2025_p1.jsonl     (--phase 1)
    batch_input_en_KJV_2025_p1p2.jsonl   (--phase 1,2)

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
from datetime import datetime, timezone
import urllib.request
from pathlib import Path

# Load .env for local development (optional)
try:
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    load_dotenv(dotenv_path=_Path(__file__).parent / ".env")
except ImportError:
    pass

# ── Project imports ───────────────────────────────────────────────────────────
from audit  import audit_path, load_reviewed_dates
from genome import ensure_genome
from models import DevotionalEntry
from prompts import (
    build_phase1_system, build_phase1_user,
    build_phase2_system, build_phase2_user,
)
import paths as _paths

# ── Config ────────────────────────────────────────────────────────────────────

_GITHUB_BASE = (
    "https://raw.githubusercontent.com/develop4God/devocionales-json"
    "/refs/heads/main"
)

# SOT exceptions: monolithic files (lang/version combos without split files)
_MONOLITHIC = {
    ("es", "RVR1960"),
    ("es", "NVI"),
}

def _github_url(lang: str, version: str, year: int) -> str:
    if (lang, version) in _MONOLITHIC:
        return f"{_GITHUB_BASE}/Devocional_year_{year}.json"
    return f"{_GITHUB_BASE}/Devocional_year_{year}_{lang}_{version}.json"

# Hardcoded fallbacks — only used when providers.yml cannot be read.
_FALLBACK_FIREWORKS = "accounts/fireworks/models/qwen3-vl-30b-a3b-thinking"
_FALLBACK_DASHSCOPE_P2 = "qwen-plus"
_FALLBACK_DASHSCOPE_P1 = "qwen-flash"

# Sentinel — argparse default; replaced at runtime via providers.yml lookup.
_AUTO = "__auto__"


def _model_for_provider(provider: str, phases: list[int]) -> str:
    """Derive model from providers.yml so it stays in sync with config.

    Falls back to hardcoded strings only if providers.yml is unavailable.
    """
    phase_key = f"phase{2 if 2 in phases else 1}"
    id_hint = provider.lower()  # e.g. "fireworks" or "dashscope"
    try:
        from cloud_client import _load_config
        for p in _load_config().get("providers", []):
            if p.get("phase") == phase_key and id_hint in p.get("id", "").lower():
                return p["model"]
    except Exception:
        pass
    # Static fallbacks
    if provider == "dashscope":
        return _FALLBACK_DASHSCOPE_P2 if 2 in phases else _FALLBACK_DASHSCOPE_P1
    return _FALLBACK_FIREWORKS


# ── Entry loading ─────────────────────────────────────────────────────────────

def load_entries(lang: str, version: str, year: int, local: str | None) -> list[DevotionalEntry]:
    if local:
        local_path = _paths.resolve_local(local)
        print(f"  📂 Loading local: {local_path}")
        with open(local_path, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        url = _github_url(lang, version, year)
        print(f"  📡 Fetching: {url}")
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = json.load(resp)

    # Support {"data": {date: entry}} and nested {"data": {lang: {date: [entry, ...]}}}
    # Support {"data": {lang: {date: [entry, ...]}}} nested format
    raw_data = raw.get("data", raw) if isinstance(raw, dict) else raw
    # Unwrap one more level if keyed by language
    if isinstance(raw_data, dict) and lang in raw_data:
        raw_data = raw_data[lang]
    # raw_data is now {date: entry_or_list}
    items = []
    if isinstance(raw_data, dict):
        for val in raw_data.values():
            if isinstance(val, list):
                items.extend(val)
            else:
                items.append(val)
    else:
        items = raw_data
    entries = []
    for item in items:
        if not isinstance(item, dict):
            continue
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
    phases: list[int],
) -> list[dict]:
    """
    Builds one JSONL record per entry.
    phases controls which phases are included:
      [2]    → Phase 2 only (default, legacy behavior)
      [1]    → Phase 1 only (linguistic scan)
      [1, 2] → Both phases in one request (P1 messages first, then P2)
    System prompt is built once and reused — Fireworks caches it automatically.
    """
    records = []
    skipped = 0

    # Build system prompts once (static — cache hit on Fireworks)
    p1_system = build_phase1_system(lang, genome=genome) if 1 in phases else None
    p2_system = build_phase2_system(
        lang=lang, version=version, genome=genome, phase1_result=None
    ) if 2 in phases else None

    for entry in entries:
        if skip_reviewed and entry.date in reviewed:
            skipped += 1
            continue

        messages = []

        if 1 in phases:
            messages.append({"role": "system", "content": p1_system})
            messages.append({"role": "user",   "content": build_phase1_user(entry, lang)})

        if 2 in phases:
            if 1 in phases:
                # P2 injected as assistant continuation after P1
                messages.append({"role": "assistant", "content": "<<P1_DONE>>"})
                messages.append({"role": "system",    "content": p2_system})
            else:
                messages.append({"role": "system", "content": p2_system})
            messages.append({"role": "user", "content": build_phase2_user(entry, lang)})

        record = {
            "custom_id": entry.id,
            "body": {
                "model":       model,
                "max_tokens":  16384,
                "temperature": 0.1,
                "messages":    messages,
            },
        }
        records.append(record)

    if skipped:
        print(f"  ⏭️  Skipped {skipped} already-reviewed entries")

    return records


# ── Output ────────────────────────────────────────────────────────────────────

def phase_suffix(phases: list[int]) -> str:
    """Returns filename suffix: _p1, _p2, _p1p2, _p1p2p3, etc."""
    return "_" + "".join(f"p{p}" for p in sorted(phases))


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

    # Pricing (batch = 50% off real-time)
    # Fireworks qwen3-vl-30b: ~$0.90/1M → batch ~$0.45/1M
    # DashScope qwen-plus: ~$0.80/1M input → batch ~$0.40/1M
    # DashScope qwen-flash: ~$0.14/1M input → batch ~$0.07/1M
    # Using Fireworks batch rates as conservative estimate
    price_input_per_m  = 0.45   # batch rate
    price_cached_per_m = 0.225  # cached system prompt (Fireworks)
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
    parser.add_argument("--phase",    default="2",
                        help="Phases to include: 1, 2, or comma-separated e.g. 1,2 (default: 2)")
    parser.add_argument("--model",    default=_AUTO, help="Model ID override (default: read from providers.yml)")
    parser.add_argument("--provider", default="fireworks",
                        choices=["fireworks", "dashscope"],
                        help="Batch provider: fireworks (default) or dashscope")
    parser.add_argument("--local",    metavar="FILE", help="Use local JSON file instead of GitHub")
    parser.add_argument("--skip-reviewed", action="store_true",
                        help="Skip entries already in the audit log")
    parser.add_argument("--output",   metavar="FILE", help="Output JSONL path (default: auto)")
    parser.add_argument("--ids",      metavar="ID[,ID...]",
                        help="Comma-separated list of entry IDs to include (re-run specific entries)")
    args = parser.parse_args()

    # Parse --phase into sorted list of ints, validate
    try:
        phases = sorted(set(int(p.strip()) for p in args.phase.split(",")))
    except ValueError:
        print(f"  ❌ Invalid --phase value: '{args.phase}'. Use e.g. 1, 2, or 1,2")
        sys.exit(1)

    print(f"\n{'═'*60}")
    print(f"  🏗️  GEP Batch Builder")
    print(f"  Lang: {args.lang} | Version: {args.version} | Year: {args.year}")
    # Resolve model: providers.yml is the SOT; --model overrides when supplied explicitly
    model = args.model if args.model != _AUTO else _model_for_provider(args.provider, phases)
    print(f"  Provider: {args.provider}")
    print(f"  Phases: {phases}")
    print(f"  Model: {model}")
    print(f"{'═'*60}")

    # Load entries
    entries = load_entries(args.lang, args.version, args.year, args.local)

    # Load reviewed dates (for --skip-reviewed)
    log_path = audit_path(args.lang, args.version, args.year)
    reviewed = load_reviewed_dates(log_path) if args.skip_reviewed else set()
    if args.skip_reviewed:
        print(f"  📋 {len(reviewed)} entries already reviewed — will skip")

    # Filter to specific IDs if --ids provided
    if getattr(args, "ids", None):
        id_set = set(i.strip() for i in args.ids.split(","))
        entries = [e for e in entries if e.id in id_set]
        print(f"  🎯 --ids filter: {len(entries)} entries matched ({len(id_set)} requested)")

    # Load genome — Phase 1 uses linguistic fragments (repetition/typo/grammar, confirmed only)
    #              Phase 2 uses full genome (all confirmed fragments)
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
        model          = model,
        skip_reviewed  = args.skip_reviewed,
        phases         = phases,
    )

    # DashScope requires method + url fields at record top level
    if args.provider == "dashscope":
        for rec in records:
            rec["method"] = "POST"
            rec["url"] = "/v1/chat/completions"

    if not records:
        print("  ✅ Nothing to build — all entries already reviewed.")
        sys.exit(0)

    print(f"  📦 {len(records)} entries → batch")

    # Cost estimate
    estimate_cost(records, model)

    # Write output — filename reflects phases included
    suffix = phase_suffix(phases)
    model_slug = model.replace("accounts/fireworks/models/", "").replace("/", "-").replace(":", "-")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else \
               _paths.BATCH_INPUT_DIR / f"batch_input_{args.lang}_{args.version}_{args.year}{suffix}_{model_slug}_{ts}.jsonl"
    write_jsonl(records, out_path)

    print(f"\n  Next step:")
    print(f"    Run the full pipeline (upload → submit → poll → download → collect):")
    print(f"      python3 batch_pipeline.py --lang {args.lang} --version {args.version} --year {args.year} \\")
    print(f"          --phase {args.phase} --provider {args.provider} \\")
    print(f"          --input {out_path}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
