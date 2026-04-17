"""
runner.py — GEP Critic v3
Single responsibility: orchestrate review modes (interactive / overnight).
"""

import re
import time
from datetime import datetime, timezone
from pathlib import Path

from audit import append_record, build_record, load_reviewed_dates, print_summary, audit_path
from genome import absorb_reaction, ensure_genome, save_genome
from models import DevotionalEntry, ReaderReaction, Verdict
from ollama_client import call_ollama, get_model_for_key
from prompts import build_system_prompt, build_user_prompt


def _run_log_path(lang: str, version: str, year: int) -> Path:
    """Returns path for the run log file (human-readable, appended each run)."""
    return Path(f"run_log_{lang}_{version}_{year}.log")


def _log(run_log: Path, msg: str, also_print: bool = True):
    """Write a line to the run log and optionally print to stdout."""
    if also_print:
        print(msg)
    with open(run_log, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def _process_entry(
    entry: DevotionalEntry,
    model: str,
    lang: str,
    version: str,
    year: int,
    genome,
) -> tuple[ReaderReaction | None, str | None, str | None, float]:
    """
    Calls the model, absorbs into genome.
    Returns (reaction, fragment_id, raw_response, elapsed_s).
    """
    system = build_system_prompt(lang, version, genome)
    user   = build_user_prompt(entry)

    t0 = time.monotonic()
    reaction, raw_response = call_ollama(model, system, user, verbose=True)
    elapsed = time.monotonic() - t0

    if reaction is None:
        return None, None, raw_response, elapsed

    genome, fragment_id = absorb_reaction(genome, reaction, entry.date, year)
    return reaction, fragment_id, raw_response, elapsed


def _print_reaction(entry: DevotionalEntry, reaction: ReaderReaction, fragment_id: str | None):
    icon = "✅" if reaction.verdict == Verdict.OK else "🔶"
    print(f"\n{'─'*60}")
    print(f"  {icon}  {entry.date}  |  {entry.id}")
    print(f"  {reaction.reaction}")
    if reaction.quoted_pause:
        print(f"  Pause: \"{reaction.quoted_pause}\"")
        print(f"  Category: {reaction.category.value if reaction.category else '—'}")
        if fragment_id:
            print(f"  GEP fragment: {fragment_id}")
    print(f"{'─'*60}")


# ── Interactive mode ───────────────────────────────────────────────────────────

def run_interactive(
    entries: list[DevotionalEntry],
    lang: str,
    version: str,
    year: int,
    model_key: str,
    start_date: str | None,
):
    model    = get_model_for_key(model_key)
    log_path = audit_path(lang, version, year)
    genome   = ensure_genome(lang, version, year)
    reviewed = load_reviewed_dates(log_path)

    pending = [
        e for e in entries
        if e.date not in reviewed and (start_date is None or e.date >= start_date)
    ]

    if not pending:
        print("  ✅ All entries already reviewed.")
        print_summary(log_path)
        return

    print(f"\n  📋 {len(pending)} entries to review")
    print(f"  🤖 Model: {model}")
    print(f"  🧬 Genome fragments loaded: {len(genome.fragments)}")
    print(f"\n  Controls: [A]pprove  [S]kip  [Q]uit\n")

    for i, entry in enumerate(pending, 1):
        print(f"\n  [{i}/{len(pending)}] Processing {entry.date}...")

        reaction, fragment_id, raw_error, _elapsed = _process_entry(
            entry, model, lang, version, year, genome
        )

        if reaction is None:
            print(f"  ⚠️  Model error — {raw_error}")
            record = build_record(
                entry.date, entry.id, lang, version,
                action="error",
                reaction=ReaderReaction(verdict=Verdict.OK, reaction="model error"),
                raw_response=raw_error,
            )
            append_record(log_path, record)
            continue

        _print_reaction(entry, reaction, fragment_id)

        while True:
            choice = input("  Action? [A]pprove / [S]kip / [Q]uit : ").strip().lower()
            if choice in ("a", "s", "q"):
                break

        if choice == "q":
            print("\n  👋 Session ended. Progress saved.")
            break

        if choice == "a":
            record = build_record(
                entry.date, entry.id, lang, version,
                action="approved",
                reaction=reaction,
                genome_fragment_id=fragment_id,
            )
            append_record(log_path, record)
            print("  ✅ Approved and logged.")

    print_summary(log_path)


# ── Overnight mode ─────────────────────────────────────────────────────────────

def run_overnight(
    entries: list[DevotionalEntry],
    lang: str,
    version: str,
    year: int,
    model_key: str,
    start_date: str | None,
):
    model    = get_model_for_key(model_key)
    log_path = audit_path(lang, version, year)
    run_log  = _run_log_path(lang, version, year)
    genome   = ensure_genome(lang, version, year)
    reviewed = load_reviewed_dates(log_path)

    pending = [
        e for e in entries
        if e.date not in reviewed and (start_date is None or e.date >= start_date)
    ]

    run_start    = datetime.now(timezone.utc)
    run_start_ts = run_start.strftime("%Y-%m-%d %H:%M:%S UTC")
    run_tag      = run_start.strftime("%Y%m%d_%H%M%S")

    if not pending:
        print("  ✅ All entries already reviewed.")
        print_summary(log_path)
        return

    header = (
        f"\n{'═'*60}\n"
        f"  🌙 OVERNIGHT RUN — {run_tag}\n"
        f"  Started  : {run_start_ts}\n"
        f"  Lang     : {lang} | Version: {version} | Year: {year}\n"
        f"  Model    : {model}\n"
        f"  Pending  : {len(pending)} entries\n"
        f"  Genome   : {len(genome.fragments)} fragments\n"
        f"  Audit log: {log_path}\n"
        f"  Run log  : {run_log}\n"
        f"  (Ctrl+C to stop cleanly)\n"
        f"{'═'*60}"
    )
    _log(run_log, header)

    ok_count    = 0
    pause_count = 0
    error_count = 0
    run_t0      = time.monotonic()
    entry_times: list[float] = []  # for ETA estimation

    try:
        for i, entry in enumerate(pending, 1):
            entry_start    = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            entry_header   = (
                f"\n{'─'*60}\n"
                f"  [{i}/{len(pending)}] {entry.date} | {entry.id}\n"
                f"  Started: {entry_start}"
            )
            _log(run_log, entry_header)

            reaction, fragment_id, raw_response, elapsed = _process_entry(
                entry, model, lang, version, year, genome
            )

            # extract think size for log
            think_match = re.search(r'<think>(.*?)</think>', raw_response or '', re.DOTALL)
            think_info  = f"  thinking: {len(think_match.group(1)):,} chars\n" if think_match else ""

            if reaction is None:
                error_count += 1
                err_msg = (raw_response or "unknown error")[:400]
                _log(run_log, f"  ⚠️  ERROR ({elapsed:.1f}s)\n  {err_msg}")
                record = build_record(
                    entry.date, entry.id, lang, version,
                    action="error",
                    reaction=ReaderReaction(verdict=Verdict.OK, reaction="model error"),
                    raw_response=raw_response,
                )
                append_record(log_path, record)
                continue

            if reaction.verdict.value == "OK":
                ok_count += 1
                icon = "✅ OK"
            else:
                pause_count += 1
                cat  = reaction.category.value if reaction.category else "other"
                icon = f"🔶 PAUSE [{cat}]"

            result_lines = (
                f"  {icon} | confidence: {reaction.confidence:.2f} | elapsed: {elapsed:.1f}s\n"
                f"{think_info}"
                f"  reaction: {reaction.reaction[:200]}"
            )
            if reaction.quoted_pause:
                result_lines += f"\n  pause   : \"{reaction.quoted_pause[:120]}\""
            if fragment_id:
                result_lines += f"\n  genome  : {fragment_id}"

            _log(run_log, result_lines)

            record = build_record(
                entry.date, entry.id, lang, version,
                action="overnight",
                reaction=reaction,
                genome_fragment_id=fragment_id,
                raw_response=raw_response,
            )
            append_record(log_path, record)
            entry_times.append(elapsed)

            # ETA: rolling average of last 5 completed entries
            if entry_times:
                avg   = sum(entry_times[-5:]) / len(entry_times[-5:])
                remaining = len(pending) - i
                eta_s = int(avg * remaining)
                hh2, rem = divmod(eta_s, 3600)
                mm2, ss2 = divmod(rem, 60)
                _log(run_log, f"  ETA: ~{hh2:02d}:{mm2:02d}:{ss2:02d} for {remaining} remaining  (avg {avg:.0f}s/entry)")

    except KeyboardInterrupt:
        _log(run_log, "\n\n  🛑 Interrupted. Progress saved.")

    total_elapsed = time.monotonic() - run_t0
    mm, ss = divmod(int(total_elapsed), 60)
    hh, mm = divmod(mm, 60)
    footer = (
        f"\n{'═'*60}\n"
        f"  Run finished : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"  Total time   : {hh:02d}:{mm:02d}:{ss:02d}\n"
        f"  Processed    : {ok_count + pause_count + error_count} entries\n"
        f"  ✅ OK        : {ok_count}\n"
        f"  🔶 PAUSE     : {pause_count}\n"
        f"  ⚠️  Errors   : {error_count}\n"
        f"  Run log      : {run_log}\n"
        f"{'═'*60}"
    )
    _log(run_log, footer)
    print_summary(log_path)
