"""
runner.py — GEP Critic v3
Single responsibility: orchestrate review modes (interactive / overnight).
"""

from pathlib import Path

from audit import append_record, build_record, load_reviewed_dates, print_summary, audit_path
from genome import absorb_reaction, ensure_genome, save_genome
from models import DevotionalEntry, ReaderReaction, Verdict
from ollama_client import call_ollama, get_model_for_key
from prompts import build_system_prompt, build_user_prompt


def _process_entry(
    entry: DevotionalEntry,
    model: str,
    lang: str,
    version: str,
    year: int,
    genome,
) -> tuple[ReaderReaction | None, str | None, str | None]:
    """
    Calls the model, absorbs into genome.
    Returns (reaction, fragment_id, raw_error).
    """
    system = build_system_prompt(lang, version, genome)
    user   = build_user_prompt(entry)

    reaction, raw_error = call_ollama(model, system, user)
    if reaction is None:
        return None, None, raw_error

    genome, fragment_id = absorb_reaction(genome, reaction, entry.date, year)
    return reaction, fragment_id, None


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

        reaction, fragment_id, raw_error = _process_entry(
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

    print(f"\n  🌙 OVERNIGHT MODE — {len(pending)} entries")
    print(f"  🤖 Model: {model}")
    print(f"  🧬 Genome fragments loaded: {len(genome.fragments)}")
    print(f"  (Ctrl+C to stop cleanly)\n")

    try:
        for i, entry in enumerate(pending, 1):
            print(f"  [{i}/{len(pending)}] {entry.date}...", end=" ", flush=True)

            reaction, fragment_id, raw_error = _process_entry(
                entry, model, lang, version, year, genome
            )

            if reaction is None:
                print(f"⚠️  error")
                record = build_record(
                    entry.date, entry.id, lang, version,
                    action="error",
                    reaction=ReaderReaction(verdict=Verdict.OK, reaction="model error"),
                    raw_response=raw_error,
                )
                append_record(log_path, record)
                continue

            icon = "✅" if reaction.verdict == Verdict.OK else f"🔶 [{reaction.category.value if reaction.category else 'other'}]"
            print(icon)

            record = build_record(
                entry.date, entry.id, lang, version,
                action="overnight",
                reaction=reaction,
                genome_fragment_id=fragment_id,
            )
            append_record(log_path, record)

    except KeyboardInterrupt:
        print("\n\n  🛑 Interrupted. Progress saved.")

    print_summary(log_path)
