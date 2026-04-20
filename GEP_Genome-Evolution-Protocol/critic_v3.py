#!/usr/bin/env python3
"""
critic_v3.py — GEP Critic v3
Entry point. CLI wiring only.

GEP: Genome Evolution Protocol
The critic simulates a real reader, not an editor.
Every PAUSE reaction grows the genome for future runs.

Usage:
  python critic_v3.py --lang pt --version ARC --year 2025 --mode overnight
  python critic_v3.py --lang pt --version ARC --year 2025 --role elder --mode overnight
  python critic_v3.py --lang pt --version ARC --year 2025 --role charismatic
  python critic_v3.py --lang es --version NVI --year 2025 --mode interactive
  python critic_v3.py --lang pt --version ARC --year 2025 --local ./Devocional_year_2025_pt_ARC.json
  python critic_v3.py --list-files
  python critic_v3.py --genome pt ARC 2025

Available roles for pt:
  (default)     Carlos, 42, São Paulo, Baptist — 15 years of devotionals
  elder         Dona Aparecida, 68, Belo Horizonte, Presbyterian — deep ARC memory
  charismatic   Jéssica, 34, Fortaleza, Assembly of God — encounter-oriented
  new_believer  Lucas, 27, Recife — 6 months as Christian, accessibility check
"""

import argparse
import sys

from audit import audit_path, print_summary
from genome import ensure_genome
MODEL_KEYS = ["auto", "fast", "best"]
run_interactive = None
run_overnight = None
get_model_for_key = None
call_ollama = None
from source import extract_entries, fetch_remote, list_known_files, load_local


def cmd_genome(lang: str, version: str, year: int):
    """Print current genome for a lang/version."""
    genome = ensure_genome(lang, version, year)
    if not genome.fragments:
        print(f"\n  Genome for {lang}/{version}/{year}: empty (no pauses recorded yet)\n")
        return
    print(f"\n  🧬 Genome: {genome.genome_version}")
    print(f"  Entries reviewed: {genome.total_entries_reviewed} | Pauses: {genome.total_pauses}")
    print(f"  Fragments: {len(genome.fragments)}\n")
    for f in sorted(genome.fragments, key=lambda x: -x.confidence):
        print(f"  [{f.confidence:.2f}] {f.category.value:<20} \"{f.example_quote[:60]}\"")
        print(f"         {f.pattern[:80]}")
        print(f"         Evidence: {', '.join(f.evidence_dates)}\n")


def main():
    parser = argparse.ArgumentParser(
        description="GEP Critic v3 — Simulated Reader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python critic_v3.py --lang pt --version ARC --year 2025 --mode overnight\n"
            "  python critic_v3.py --lang pt --version ARC --year 2025 --role elder\n"
            "  python critic_v3.py --lang pt --version ARC --year 2025 --role charismatic\n"
            "  python critic_v3.py --lang pt --version ARC --year 2025 --role new_believer\n"
            "  python critic_v3.py --lang es --version NVI --year 2025\n"
            "  python critic_v3.py --lang pt --version ARC --year 2025 --local ./file.json\n"
            "  python critic_v3.py --genome pt ARC 2025\n"
            "  python critic_v3.py --list-files\n"
        ),
    )

    parser.add_argument("--lang",       help="Language code (pt, es, en, ...)")
    parser.add_argument("--version",    help="Bible version (ARC, NVI, KJV, ...)")
    parser.add_argument("--year",       type=int, help="Year (2025, 2026, ...)")
    parser.add_argument("--mode",       choices=["interactive", "overnight"], default="overnight")
    parser.add_argument("--role",       default="default", help="Reader role: default, elder, charismatic, new_believer (pt only for now)")
    parser.add_argument("--model", default="auto", help="Model key: auto (default), fast, best, or any Ollama tag e.g. qwen2.5:7b")
    parser.add_argument("--phase", type=int, default=0, help="Run only phase 1 or 2. Default 0 = both.")
    parser.add_argument("--start-date", dest="start_date", help="Skip entries before YYYY-MM-DD")
    parser.add_argument("--local",      metavar="FILE", help="Use local JSON file instead of GitHub fetch")
    parser.add_argument("--report",     action="store_true", help="Print audit report and exit")
    parser.add_argument("--genome",     nargs=3, metavar=("LANG", "VERSION", "YEAR"), help="Print genome and exit")
    parser.add_argument("--list-files", action="store_true", help="List known lang/version combinations and exit")
    parser.add_argument("--provider", choices=["cloud", "local", "auto"], default="auto", help="Which LLM client to use: cloud, local (Ollama), or auto (default)")
    # Dynamic import/alias for LLM client

    global run_interactive, run_overnight, get_model_for_key, call_ollama
    args = parser.parse_args()
    prefer_local = False
    if args.provider == "local":
        from ollama_client import call_ollama, get_model_for_key as _get_model_for_key
        from runner import run_interactive, run_overnight
        get_model_for_key = lambda key: _get_model_for_key(key)
        prefer_local = True
        print("[INFO] Using local Ollama client.")
    elif args.provider == "cloud":
        from cloud_client import call_ollama, get_model_for_key as _get_model_for_key
        from runner import run_interactive, run_overnight
        get_model_for_key = lambda key: _get_model_for_key(key)
        print("[INFO] Using cloud client.")
    else:  # auto
        from cloud_client import call_ollama, get_model_for_key as _get_model_for_key
        from runner import run_interactive, run_overnight
        def get_model_for_key(key):
            # Auto: prefer provider with default: true, fallback to priority
            from cloud_client import providers_for_phase, _load_config
            phase = 1 if key == "fast" else 2
            cfg = _load_config()
            phase_key = f"phase{phase}"
            candidates = [p for p in cfg["providers"] if p.get("phase") in (phase_key, "both", phase)]
            default_providers = [p for p in candidates if p.get("default", False)]
            if default_providers:
                p = default_providers[0]
                return f"{p['name']}/{p['model']} ({p.get('client_type','api')})"
            if candidates:
                p = sorted(candidates, key=lambda p: p.get("priority", 99))[0]
                return f"{p['name']}/{p['model']} ({p.get('client_type','api')})"
            return "cloud/auto"
        print("[INFO] Using auto provider selection.")

    # ── Special commands ───────────────────────────────────────────────────────
    if args.list_files:
        list_known_files()
        return

    if args.genome:
        cmd_genome(args.genome[0], args.genome[1], int(args.genome[2]))
        return

    # ── Require lang/version/year for review commands ──────────────────────────
    missing = [n for n, v in [("--lang", args.lang), ("--version", args.version),
                               ("--year", args.year)] if not v]
    if missing:
        parser.error(f"Required: {', '.join(missing)}")

    if args.report:
        print_summary(audit_path(args.lang, args.version, args.year, role=args.role))
        return

    # ── Load source data ───────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  📖 GEP Critic v3 — Simulated Reader")
    print(f"  Lang: {args.lang} | Version: {args.version} | Year: {args.year}")
    print(f"  Role: {args.role}")
    print(f"  Mode: {args.mode} | Model: {get_model_for_key(args.model)} | Provider: {args.provider}")
    print(f"{'═'*60}")

    if args.local:
        data = load_local(args.local)
        print(f"  📂 Local file: {args.local}")
    else:
        data = fetch_remote(args.lang, args.version, args.year)

    entries = extract_entries(data, args.lang)
    print(f"  ✅ {len(entries)} entries loaded\n")

    # ── Run ────────────────────────────────────────────────────────────────────
    kwargs = dict(
        entries=entries,
        lang=args.lang,
        version=args.version,
        year=args.year,
        model_key=args.model,
        start_date=args.start_date,
        role=args.role,
        phase=args.phase,
    )

    if args.mode == "interactive":
        run_interactive(**kwargs)
    else:
        overnight_kwargs = {k: v for k, v in kwargs.items() if k != "role"}
        run_overnight(**overnight_kwargs)


if __name__ == "__main__":
    main()
