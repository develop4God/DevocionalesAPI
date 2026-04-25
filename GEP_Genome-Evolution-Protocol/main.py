#!/usr/bin/env python3
"""
main.py — GEP Genome Evolution Protocol
ASCII interactive launcher. Replaces memorizing command flags.

Run:  python3 main.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Terminal helpers ──────────────────────────────────────────────────────────

def _clear():
    os.system("cls" if os.name == "nt" else "clear")


def _banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║          G E P  —  Genome Evolution Protocol                 ║
║          Simulated Reader · Devotional QA Pipeline           ║
╚══════════════════════════════════════════════════════════════╝""")


def _section(title: str):
    w = 64
    print(f"\n{'─'*w}")
    print(f"  {title}")
    print(f"{'─'*w}")


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return default
    return val if val else default


def _confirm(prompt: str, default: bool = True) -> bool:
    yn = "Y/n" if default else "y/N"
    try:
        val = input(f"  {prompt} [{yn}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not val:
        return default
    return val in ("y", "yes")


def _menu(title: str, options: list[tuple[str, str]], back_label: str = "Back") -> str:
    """
    Display a numbered menu. Returns the key of the chosen option.
    options: list of (key, label) pairs.
    Appends a '0' Back/Exit option automatically.
    """
    _section(title)
    for i, (_, label) in enumerate(options, 1):
        print(f"  {i:>2}.  {label}")
    print(f"\n   0.  {back_label}")
    while True:
        try:
            raw = input("\n  Choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            return "0"
        if raw == "0":
            return "0"
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        print("  ❌  Invalid choice — try again.")


# ── Toggle multi-select ───────────────────────────────────────────────────────

@dataclass
class Toggle:
    key: str
    label: str
    value: bool = False
    description: str = ""


def _toggle_menu(title: str, toggles: list[Toggle]) -> list[Toggle]:
    """
    Show a list of toggleable options (checkboxes).
    Press the number to toggle. Press Enter/0 to confirm.
    """
    while True:
        _section(title)
        for i, t in enumerate(toggles, 1):
            state = "✓" if t.value else " "
            desc  = f"  ({t.description})" if t.description else ""
            print(f"  {i:>2}.  [{state}]  {t.label}{desc}")
        print(f"\n   0.  Done / Confirm")
        try:
            raw = input("\n  Toggle #: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if raw == "0" or raw == "":
            break
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(toggles):
                toggles[idx].value = not toggles[idx].value
        except ValueError:
            pass
    return toggles


# ── Parameter collection ──────────────────────────────────────────────────────

def _collect_lang_version_year(defaults: dict | None = None) -> tuple[str, str, int]:
    d = defaults or {}

    langs = [
        ("es", "es — Spanish"),
        ("pt", "pt — Portuguese"),
        ("en", "en — English"),
        ("tl", "tl — Tagalog"),
        ("ar", "ar — Arabic"),
        ("de", "de — German"),
        ("fr", "fr — French"),
        ("hi", "hi — Hindi"),
        ("zh", "zh — Chinese"),
        ("ja", "ja — Japanese"),
    ]
    _section("Select Language")
    for i, (k, label) in enumerate(langs, 1):
        print(f"  {i:>2}.  {label}")
    lang_default_idx = next((i for i, (k, _) in enumerate(langs, 1) if k == d.get("lang", "es")), 1)
    while True:
        try:
            raw = input(f"\n  Choice [{lang_default_idx}]: ").strip()
            idx = int(raw) - 1 if raw else lang_default_idx - 1
            if 0 <= idx < len(langs):
                lang = langs[idx][0]
                break
        except (ValueError, KeyboardInterrupt):
            lang = d.get("lang", "es")
            break

    version = _ask("Bible version (e.g. RVR1960, NVI, ARC, KJV, ASND)", d.get("version", "RVR1960"))
    year    = _ask("Year", str(d.get("year", "2025")))
    try:
        year_int = int(year)
    except ValueError:
        year_int = 2025
    return lang, version, year_int


def _collect_phase() -> list[int]:
    _section("Select Phase(s)")
    print("  1.  Phase 1 only   (linguistic — fast)")
    print("  2.  Phase 2 only   (content coherence)")
    print("  3.  Both phases    (1,2)")
    raw = _ask("Choice", "1")
    if raw == "2":
        return [2]
    if raw == "3":
        return [1, 2]
    return [1]


def _collect_provider_batch() -> str:
    providers = [
        ("dashscope",              "DashScope (qwen-flash / qwen-plus) — default"),
        ("dashscope_batch_phase2", "DashScope Phase 2 (qwen-plus)"),
        ("fireworks_batch_phase2", "Fireworks (qwen3-vl-30b-thinking)"),
        ("groq_qwen3_phase1",      "Groq (qwen3-32b)"),
    ]
    choice = _menu("Select Cloud Provider", providers, back_label="Cancel")
    return choice if choice != "0" else "dashscope"


# ── Build command string ──────────────────────────────────────────────────────

def _run(cmd: list[str], dry_print: bool = False):
    """Print and optionally run the constructed command."""
    cmd_str = " ".join(cmd)
    print(f"\n  {'─'*60}")
    print(f"  Command:\n  {cmd_str}")
    print(f"  {'─'*60}")
    if dry_print:
        return
    if not _confirm("Run this command?"):
        return
    print()
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\n  ⚠️  Exited with code {result.returncode}")
    input("\n  Press Enter to continue…")


# ══════════════════════════════════════════════════════════════════════════════
# FLOW 1 — Batch Pipeline (build → upload → submit → poll → download → collect)
# ══════════════════════════════════════════════════════════════════════════════

def flow_batch_pipeline():
    _clear()
    _banner()
    _section("Batch Pipeline — build → submit → collect")

    lang, version, year = _collect_lang_version_year()
    phases = _collect_phase()
    provider = _collect_provider_batch()

    toggles = _toggle_menu("Options", [
        Toggle("skip_reviewed", "--skip-reviewed", False, "skip entries already in audit log"),
        Toggle("no_genome",     "--no-genome",     False, "suppress genome (baseline / ablation run)"),
        Toggle("dry_run",       "--dry-run",       False, "build JSONL only, do not submit"),
    ])
    opts = {t.key: t.value for t in toggles}

    local_file = _ask("--local file (leave blank to fetch from GitHub)", "")

    phase_str = ",".join(str(p) for p in phases)
    cmd = [
        sys.executable, "batch_pipeline.py",
        "--lang",     lang,
        "--version",  version,
        "--year",     str(year),
        "--phase",    phase_str,
        "--provider", provider,
    ]
    if opts["skip_reviewed"]:  cmd.append("--skip-reviewed")
    if opts["no_genome"]:      cmd.append("--no-genome")
    if opts["dry_run"]:        cmd.append("--dry-run")
    if local_file:             cmd += ["--local", local_file]

    _run(cmd)


# ══════════════════════════════════════════════════════════════════════════════
# FLOW 2 — Collect only (use existing results file)
# ══════════════════════════════════════════════════════════════════════════════

def flow_collect_only():
    _clear()
    _banner()
    _section("Collect Results — use existing JSONL + results file")

    lang, version, year = _collect_lang_version_year()
    phases = _collect_phase()
    provider = _collect_provider_batch()

    _section("File Selection")
    input_file   = _ask("--input  JSONL filename (from data/batch_input/)", "")
    results_file = _ask("--results JSONL filename (from data/batch_output/)", "")

    phase_str = ",".join(str(p) for p in phases)
    cmd = [
        sys.executable, "batch_pipeline.py",
        "--lang",     lang,
        "--version",  version,
        "--year",     str(year),
        "--phase",    phase_str,
        "--provider", provider,
    ]
    if input_file:   cmd += ["--input",   input_file]
    if results_file: cmd += ["--results", results_file]

    _run(cmd)


# ══════════════════════════════════════════════════════════════════════════════
# FLOW 3 — Interactive / Overnight Critic (local Ollama)
# ══════════════════════════════════════════════════════════════════════════════

def flow_critic():
    _clear()
    _banner()
    _section("Critic — Interactive / Overnight Review (local or cloud)")

    lang, version, year = _collect_lang_version_year()

    modes = [
        ("overnight",    "overnight  — run all pending entries automatically"),
        ("interactive",  "interactive — confirm each entry manually"),
    ]
    mode = _menu("Mode", modes, back_label="Cancel")
    if mode == "0":
        return

    _section("Phase")
    print("  0.  Both phases (default)")
    print("  1.  Phase 1 only (linguistic)")
    print("  2.  Phase 2 only (content)")
    ph_raw = _ask("Phase", "0")

    _section("Provider")
    print("  auto   — auto-select from providers.yml (default)")
    print("  cloud  — force cloud provider")
    print("  local  — force local Ollama")
    provider = _ask("Provider", "auto")

    roles = [
        ("default",     "default reader persona"),
        ("elder",       "elder (pt only)"),
        ("charismatic", "charismatic (pt only)"),
        ("new_believer","new believer (pt only)"),
    ]
    role = _menu("Reader Role", roles, back_label="Use default")
    if role == "0":
        role = "default"

    start_date = _ask("--start-date YYYY-MM-DD (leave blank to start from beginning)", "")

    cmd = [
        sys.executable, "critic_v3.py",
        "--lang",     lang,
        "--version",  version,
        "--year",     str(year),
        "--mode",     mode,
        "--role",     role,
        "--provider", provider,
    ]
    if ph_raw in ("1", "2"):
        cmd += ["--phase", ph_raw]
    if start_date:
        cmd += ["--start-date", start_date]

    _run(cmd)


# ══════════════════════════════════════════════════════════════════════════════
# FLOW 4 — Review Flags Report
# ══════════════════════════════════════════════════════════════════════════════

def flow_review():
    _clear()
    _banner()
    _section("Review Flags — generate human-readable report")

    lang, version, year = _collect_lang_version_year()
    phases = _collect_phase()

    _section("Verdict filter")
    print("  1.  FLAG only (default)")
    print("  2.  ALL verdicts")
    verdict_raw = _ask("Choice", "1")
    verdict = "ALL" if verdict_raw == "2" else "FLAG"

    _section("File selection (optional — leave blank to auto-resolve)")
    input_file   = _ask("--input  JSONL filename", "")
    results_file = _ask("--results JSONL filename", "")

    phase_str = ",".join(str(p) for p in phases)
    cmd = [
        sys.executable, "review_flags.py",
        "--lang",    lang,
        "--version", version,
        "--year",    str(year),
        "--phase",   phase_str,
        "--verdict", verdict,
    ]
    if input_file:   cmd += ["--input",   input_file]
    if results_file: cmd += ["--results", results_file]

    _run(cmd)


# ══════════════════════════════════════════════════════════════════════════════
# FLOW 5 — Genome tools
# ══════════════════════════════════════════════════════════════════════════════

def flow_genome():
    _clear()
    _banner()
    genome_options = [
        ("view",    "View genome fragments"),
        ("report",  "Print audit report"),
        ("requeue", "Re-queue error entries"),
        ("list",    "List known source files"),
    ]
    choice = _menu("Genome & Audit", genome_options)
    if choice == "0":
        return

    if choice == "list":
        _run([sys.executable, "critic_v3.py", "--list-files"])
        return

    lang, version, year = _collect_lang_version_year()

    if choice == "view":
        _run([sys.executable, "critic_v3.py", "--genome", lang, version, str(year)])
    elif choice == "report":
        _run([sys.executable, "critic_v3.py",
              "--lang", lang, "--version", version, "--year", str(year), "--report"])
    elif choice == "requeue":
        _run([sys.executable, "critic_v3.py",
              "--lang", lang, "--version", version, "--year", str(year), "--requeue-errors"])


# ══════════════════════════════════════════════════════════════════════════════
# FLOW 6 — Genome A/B Comparison (with vs without genome)
# ══════════════════════════════════════════════════════════════════════════════

def flow_genome_ab():
    _clear()
    _banner()
    _section("Genome A/B — build batches with and without genome")
    print("  This builds TWO batch JSONLs for the same lang/version/year:")
    print("  A) without genome (baseline)  B) with genome (validator mode)")
    print("  Submit both, then compare flag rates in the reports.\n")

    lang, version, year = _collect_lang_version_year()
    phases = _collect_phase()
    provider = _collect_provider_batch()

    toggles = _toggle_menu("Options", [
        Toggle("skip_reviewed", "--skip-reviewed", False, "skip entries already in audit log"),
        Toggle("dry_run",       "--dry-run",       False, "build JSONL only, do not submit"),
    ])
    opts = {t.key: t.value for t in toggles}
    phase_str = ",".join(str(p) for p in phases)

    base_cmd = [
        sys.executable, "batch_pipeline.py",
        "--lang",     lang,
        "--version",  version,
        "--year",     str(year),
        "--phase",    phase_str,
        "--provider", provider,
    ]
    if opts["skip_reviewed"]: base_cmd.append("--skip-reviewed")
    if opts["dry_run"]:       base_cmd.append("--dry-run")

    print("\n  ── Step A: no-genome (baseline) ──────────────────────────")
    _run(base_cmd + ["--no-genome"])

    print("\n  ── Step B: with genome (validator mode) ──────────────────")
    _run(base_cmd)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN MENU
# ══════════════════════════════════════════════════════════════════════════════

_MAIN_OPTIONS = [
    ("batch",   "Generate batch & submit            (build → upload → submit → collect)"),
    ("collect", "Collect existing results            (use already-downloaded output)"),
    ("critic",  "Run critic interactively / overnight (local Ollama or cloud)"),
    ("review",  "Review flags report                 (generate human-readable summary)"),
    ("genome",  "Genome & audit tools                (view / report / requeue)"),
    ("ab",      "Genome A/B comparison               (with vs without genome — 2 batches)"),
]

_FLOW_MAP = {
    "batch":   flow_batch_pipeline,
    "collect": flow_collect_only,
    "critic":  flow_critic,
    "review":  flow_review,
    "genome":  flow_genome,
    "ab":      flow_genome_ab,
}


def main():
    while True:
        _clear()
        _banner()
        choice = _menu("Main Menu", _MAIN_OPTIONS, back_label="Exit")
        if choice == "0":
            print("\n  Goodbye.\n")
            break
        handler = _FLOW_MAP.get(choice)
        if handler:
            handler()


if __name__ == "__main__":
    main()
