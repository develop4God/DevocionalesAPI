"""
batch_pipeline.py — GEP Batch Pipeline (SOLID orchestrator)
Single responsibility: orchestrate build → upload → submit → poll → download → collect.

All provider configuration is read from providers.yml.  No values are hardcoded here.

Usage:
    # Full pipeline: build JSONL, upload, submit, poll, download, collect
    python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \\
        --phase 1 --provider dashscope_batch_phase1 \\
        --local Devocional_year_2025_es_RVR1960.json

    # Dry run: build JSONL only, print cost estimate, no upload
    python3 batch_pipeline.py ... --dry-run

    # Use an existing JSONL (skip build step, go straight to upload)
    python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \\
        --phase 1 --provider dashscope_batch_phase1 \\
        --input batch_input_es_RVR1960_2025_p1.jsonl

    # Use already-downloaded results (skip upload/submit/poll/download)
    python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \\
        --phase 1 --provider dashscope_batch_phase1 \\
        --input batch_input_es_RVR1960_2025_p1.jsonl \\
        --results BIJOutputSet_es_RVR1960_2025_results.jsonl

Provider short-names accepted for --provider:
    dashscope   → auto-resolves to dashscope_batch_phase{N} based on --phase
    (or pass the full provider id from providers.yml directly)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import sys
from pathlib import Path

# Load .env for local development (optional)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
except ImportError:
    pass

# ── Project imports ───────────────────────────────────────────────────────────
from audit import audit_path, load_reviewed_dates
from batch_client import BatchClient
from build_batch import (
    build_batch as build_records,
    estimate_cost,
    load_entries,
    phase_suffix,
    write_jsonl,
)
from collect_batch import process_results
from genome import ensure_genome
import paths as _paths


# ── Provider resolution ────────────────────────────────────────────────────────

_PROVIDER_ALIASES: dict[str, str] = {
    "dashscope": "dashscope_batch_phase{phase}",
}


def _resolve_provider_id(name: str, phases: list[int]) -> str:
    """
    Accept either a full provider id (e.g. 'dashscope_batch_phase1') or a short
    alias (e.g. 'dashscope').  Short aliases are expanded using the lowest phase.
    """
    tpl = _PROVIDER_ALIASES.get(name)
    if tpl:
        return tpl.format(phase=min(phases))
    return name


# ── Build step ─────────────────────────────────────────────────────────────────

def _build_jsonl(
    lang: str,
    version: str,
    year: int,
    phases: list[int],
    model: str,
    local: str | None,
    skip_reviewed: bool,
    output: str | None,
    provider_id: str,
    no_genome: bool = False,
) -> Path:
    """Build the batch JSONL file. Returns the path to the written file."""
    entries = load_entries(lang, version, year, local)

    log_path = audit_path(lang, version, year)
    reviewed = load_reviewed_dates(log_path) if skip_reviewed else set()
    if skip_reviewed:
        print(f"  📋 {len(reviewed)} entries already reviewed — will skip")

    genome = ensure_genome(lang, version, year)
    frag_count = len(genome.fragments) if genome else 0
    if no_genome:
        print(f"  🧬 Genome: {frag_count} fragments (SUPPRESSED — baseline run)")
    else:
        print(f"  🧬 Genome: {frag_count} fragments")

    records = build_records(
        lang=lang,
        version=version,
        year=year,
        entries=entries,
        reviewed=reviewed,
        genome=genome,
        model=model,
        provider=provider_id,
        skip_reviewed=skip_reviewed,
        phases=phases,
        no_genome=no_genome,
    )

    if not records:
        print("  ✅ Nothing to build — all entries already reviewed.")
        sys.exit(0)

    # DashScope requires method + url at record top level (OpenAI-compat batch format)
    if "dashscope" in provider_id:
        for rec in records:
            rec.setdefault("method", "POST")
            rec.setdefault("url", "/v1/chat/completions")

    print(f"  📦 {len(records)} entries → batch")
    estimate_cost(records, model)

    suffix = phase_suffix(phases)
    model_slug = model.replace("accounts/fireworks/models/", "").replace("/", "-").replace(":", "-")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = (
        Path(output)
        if output
        else _paths.BATCH_INPUT_DIR / f"batch_input_{lang}_{version}_{year}{suffix}_{model_slug}_{ts}.jsonl"
    )
    write_jsonl(records, out_path)
    return out_path


# ── Pipeline ───────────────────────────────────────────────────────────────────

def run_pipeline(args: argparse.Namespace) -> None:
    try:
        phases = sorted(set(int(p.strip()) for p in args.phase.split(",")))
    except ValueError:
        print(f"  ❌ Invalid --phase: '{args.phase}'. Use e.g. 1, 2, or 1,2")
        sys.exit(1)

    provider_id = _resolve_provider_id(args.provider, phases)

    # Resolve model from providers.yml via BatchClient (avoids hardcoding)
    # We peek at the config without making any HTTP calls.
    try:
        from cloud_client import _load_config
        model = next(
            p["model"]
            for p in _load_config()["providers"]
            if p["id"] == provider_id
        )
    except StopIteration:
        print(f"  ❌ Provider '{provider_id}' not found in providers.yml")
        sys.exit(1)

    print(f"\n{'═'*60}")
    print(f"  🚀  GEP Batch Pipeline")
    print(f"  Lang: {args.lang} | Version: {args.version} | Year: {args.year}")
    print(f"  Provider: {provider_id}  |  Model: {model}")
    print(f"  Phases: {phases}")
    if args.dry_run:
        print(f"  ⚠️  DRY RUN — build only, no upload/submit")
    if getattr(args, "no_genome", False):
        print(f"  ⚠️  --no-genome: genome suppressed (baseline run)")
    print(f"{'═'*60}")

    # ── Step 1: Build or use existing JSONL ───────────────────────────────
    if args.input:
        batch_path = _paths.resolve_batch_input(args.input)
        print(f"  ♻️  Using existing JSONL: {batch_path}")
    else:
        print(f"\n  🏗️  Building batch JSONL …")
        batch_path = _build_jsonl(
            lang=args.lang,
            version=args.version,
            year=args.year,
            phases=phases,
            model=model,
            local=args.local,
            skip_reviewed=args.skip_reviewed,
            output=args.output,
            provider_id=provider_id,
            no_genome=getattr(args, "no_genome", False),
        )

    if args.dry_run:
        print(f"\n  ✅ Dry run complete.")
        print(f"     Review {batch_path} then re-run without --dry-run to submit.\n")
        sys.exit(0)

    # ── Steps 2–5: Upload → Submit → Poll → Download ──────────────────────
    if args.results:
        results_path = _paths.resolve_batch_output(args.results)
        print(f"  ♻️  Using existing results: {results_path}")
    else:
        client = BatchClient(provider_id)

        print(f"\n  📤 Uploading {batch_path.name} …")
        file_id = client.upload(batch_path)
        print(f"  ✅ file_id = {file_id}")

        print(f"\n  🚀 Submitting batch …")
        batch_id = client.submit(file_id)
        print(f"  ✅ batch_id = {batch_id}")

        print(f"\n  ⏳ Polling every {args.poll_interval}s …")
        output_file_id = client.poll(batch_id, interval=args.poll_interval)
        print(f"  ✅ output_file_id = {output_file_id}")

        stem = batch_path.stem.replace("batch_input_", "BIJOutputSet_")
        results_path = _paths.BATCH_OUTPUT_DIR / f"{stem}_results.jsonl"
        # NOTE: stem already contains model_slug + timestamp inherited from batch_path name,
        # so results filename is unique by construction — no extra suffix needed here.
        print(f"\n  📥 Downloading → {results_path.name} …")
        client.download(output_file_id, results_path)
        print(f"  ✅ Saved: {results_path}")

    # ── Step 6: Collect ───────────────────────────────────────────────────
    print(f"\n  📊 Collecting results …")
    process_results(
        input_path=batch_path,
        results_path=results_path,
        lang=args.lang,
        version=args.version,
        year=args.year,
        dry_run=False,
    )

    print(f"{'═'*60}")
    print(f"  ✅ Pipeline complete.\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="GEP Batch Pipeline — full automated build → upload → submit → collect",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Full pipeline\n"
            "  python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \\\n"
            "      --phase 1 --provider dashscope --local Devocional_year_2025_es_RVR1960.json\n\n"
            "  # Dry run only\n"
            "  python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \\\n"
            "      --phase 1 --provider dashscope --local Devocional_year_2025_es_RVR1960.json \\\n"
            "      --dry-run\n\n"
            "  # Use existing JSONL + existing results (collect only)\n"
            "  python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \\\n"
            "      --phase 1 --provider dashscope \\\n"
            "      --input batch_input_es_RVR1960_2025_p1.jsonl \\\n"
            "      --results BIJOutputSet_es_RVR1960_2025_p1_results.jsonl\n"
        ),
    )
    parser.add_argument("--lang",     required=True, help="Language code (es, tl, pt, ...)")
    parser.add_argument("--version",  required=True, help="Bible version (RVR1960, ASND, ...)")
    parser.add_argument("--year",     required=True, type=int, help="Year (2025, 2026, ...)")
    parser.add_argument("--phase",    default="1",
                        help="Phases: 1, 2, or comma-separated e.g. 1,2 (default: 1)")
    parser.add_argument("--provider", default="dashscope",
                        help="Provider id from providers.yml or short alias (default: dashscope)")
    parser.add_argument("--local",    metavar="FILE",
                        help="Use local JSON instead of fetching from GitHub")
    parser.add_argument("--input",    metavar="FILE",
                        help="Use existing batch JSONL — skip build step")
    parser.add_argument("--output",   metavar="FILE",
                        help="Override output JSONL path (build step only)")
    parser.add_argument("--results",  metavar="FILE",
                        help="Use existing results file — skip upload/submit/poll/download")
    parser.add_argument("--skip-reviewed", action="store_true",
                        help="Skip entries already in the audit log")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Build JSONL only — do not upload or submit")
    parser.add_argument("--poll-interval", type=int, default=30,
                        help="Seconds between status polls (default: 30)")
    parser.add_argument("--no-genome", action="store_true",
                        help="Suppress genome injection (baseline run without prior pattern knowledge)")
    args = parser.parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
