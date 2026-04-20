"""
runner.py — GEP Critic v3
Single responsibility: orchestrate review modes (interactive / overnight).

Two-phase flow per entry:
  Phase 1 — qwen3:4b, fast linguistic check (~15-20s)
  Phase 2 — qwen3:14b, thinking mode content check (~100s)
  Phase 1 result injected into Phase 2 prompt.
  Both verdicts stored in one audit record.
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from audit import append_record, build_record, load_reviewed_dates, print_summary, audit_path
from genome import absorb_reaction, ensure_genome, save_genome
from models import DevotionalEntry, ReaderReaction, Verdict
from cloud_client import call_ollama, get_model_for_key, _unwrap_text
from prompts import (
    build_phase1_system, build_phase1_user,
    build_phase2_system, build_phase2_user,
)

# Phase 1 always uses the fast model
PHASE1_MODEL = "qwen3:4b"


def _run_log_path(lang: str, version: str, year: int) -> Path:
    return Path(f"run_log_{lang}_{version}_{year}.log")


def _log(run_log: Path, msg: str, also_print: bool = True):
    if also_print:
        print(msg)
    with open(run_log, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def _normalize_phase1(data: dict) -> dict:
    """
    Canonicalise Phase-1 JSON keys.
    Models sometimes return alternate field names; we normalise to:
      verdict    → "CLEAN" | "FLAG"
      issue      → human-readable description of the problem
      quoted     → exact phrase that triggered the flag
      confidence → 0.0–1.0 float
    """
    # Infer verdict from problem-indicating keys when absent
    if "verdict" not in data:
        problem_keys = {"quoted_problem", "problem", "error", "issue", "type"}
        data["verdict"] = "FLAG" if problem_keys & data.keys() else "CLEAN"

    # Normalise verdict to CLEAN/FLAG (some models emit OK/PASS instead of CLEAN)
    v = (data.get("verdict") or "CLEAN").strip().upper()
    if v in ("OK", "PASS", "NO_ISSUE", "NONE"):
        v = "CLEAN"
    elif v in ("ERROR", "PROBLEM", "ISSUE"):
        v = "FLAG"
    data["verdict"] = v

    # Map alternate field names to canonical ones
    if "quoted_problem" in data and "quoted" not in data:
        data["quoted"] = data.pop("quoted_problem")
    if "type" in data and "issue" not in data:
        data["issue"] = data.pop("type")
    if "problem" in data and "issue" not in data:
        data["issue"] = data.pop("problem")
    if "error" in data and "issue" not in data:
        data["issue"] = data.pop("error")
    if "description" in data and "issue" not in data:
        data["issue"] = data.pop("description")
    if "quote" in data and "quoted" not in data:
        data["quoted"] = data.pop("quote")
    if "excerpt" in data and "quoted" not in data:
        data["quoted"] = data.pop("excerpt")

    return data


def _parse_phase1(raw: str) -> dict | None:
    """Parse Phase 1 JSON from raw response. Accepts any model output format."""
    text = _unwrap_text(raw)

    # 1. Strict JSON parse on the full cleaned text
    try:
        parsed = json.loads(text)
        # Model returned a plain JSON string (e.g. "No repeated phrase found") → CLEAN
        if isinstance(parsed, str):
            tu = parsed.upper()
            if re.search(r'\bFLAG\b', tu) or re.search(r'\bPAUSE\b', tu):
                return {"verdict": "FLAG", "issue": parsed, "quoted": None, "confidence": 0.7}
            return {"verdict": "CLEAN", "issue": None, "quoted": None, "confidence": 1.0}
        return _normalize_phase1(parsed)
    except json.JSONDecodeError:
        pass

    # 2. Find any JSON object in the text (model may embed JSON inside prose)
    #    — broadened to any object with at least one recognisable key
    known_keys = {"verdict", "issue", "quoted", "confidence",
                  "quoted_problem", "type", "problem", "error"}
    for m in re.finditer(r'\{[^{}]+\}', text, re.DOTALL):
        try:
            candidate = json.loads(m.group())
            if known_keys & candidate.keys():
                return _normalize_phase1(candidate)
        except json.JSONDecodeError:
            continue

    # 3. Prose keyword fallback (model answered entirely in natural language)
    tu = text.upper()
    if re.search(r'\bFLAG\b', tu):
        issue_m  = re.search(r'(?:issue|problem|error|found|type)[:\s]+([^\n.]+)', text, re.IGNORECASE)
        quoted_m = re.search(r'"([^"]{3,80})"', text)
        return {
            "verdict":    "FLAG",
            "issue":      issue_m.group(1).strip() if issue_m else "Linguistic issue (prose response)",
            "quoted":     quoted_m.group(1) if quoted_m else "",
            "confidence": 0.85,
        }
    if (re.search(r'\bCLEAN\b', tu) or re.search(r'\bOK\b', tu)
            or re.search(r'no\s+(repeated|issue|problem|error|phrase|flag)\b', tu)
            or re.search(r'nothing\s+(wrong|found|flagged)\b', tu)
            or re.search(r'not\s+found\b', tu)):
        return {"verdict": "CLEAN", "issue": None, "quoted": None, "confidence": 1.0}

    # Last resort — store what the model said as CLEAN with low confidence.
    # Never return None so the entry is always logged and the run never stops.
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    issue_text = (lines[0] if lines else text[:200]) + " [fallback: unrecognised format]"
    return {"verdict": "CLEAN", "issue": issue_text, "quoted": None, "confidence": 0.4}


def _process_entry(
    entry: DevotionalEntry,
    model: str,
    lang: str,
    version: str,
    year: int,
    genome,
    verbose: bool = True,
    phase: int = 0,
) -> tuple[ReaderReaction | None, str | None, str | None, float, dict | None, str | None]:
    """
    Two-phase processing per entry.
    Returns (reaction, fragment_id, phase2_raw, elapsed_total, phase1_result, phase1_raw).
    """
    total_t0 = time.monotonic()

    # ── Phase 1 — Linguistic ───────────────────────────────────────────────
    p1_system = build_phase1_system(lang)
    p1_user   = build_phase1_user(entry, lang)

    if verbose:
        print("  [P1] linguistic...", end=" ", flush=True)

    p1_t0 = time.monotonic()
    _p1_rxn, p1_raw = call_ollama(PHASE1_MODEL, p1_system, p1_user, verbose=True, think=True, phase=1)
    p1_elapsed = time.monotonic() - p1_t0
    # Only parse the raw string when the provider actually returned a response;
    # if _p1_rxn is None the call failed and p1_raw is an error message, not JSON.
    phase1_result = _parse_phase1(p1_raw or "") if (_p1_rxn is not None and p1_raw) else None

    if verbose and phase1_result:
        icon = "🔤 FLAG" if phase1_result.get("verdict") == "FLAG" else "✅ CLEAN"
        print(f"{icon} ({p1_elapsed:.0f}s)")
    elif verbose and _p1_rxn is None:
        print(f"⚠️  P1 ERROR ({p1_elapsed:.0f}s)")
    elif verbose:
        print(f"⚠️  parse error ({p1_elapsed:.0f}s)")

    # ── Phase 2 — Content coherence ────────────────────────────────────────
    if phase == 1:
        elapsed_total = time.monotonic() - total_t0
        return None, None, None, elapsed_total, phase1_result, p1_raw
    p2_system = build_phase2_system(lang, version, genome, phase1_result)
    p2_user   = build_phase2_user(entry, lang)

    if verbose:
        print("  [P2] content...", end=" ", flush=True)

    p2_t0 = time.monotonic()
    reaction, p2_raw = call_ollama(model, p2_system, p2_user, verbose=verbose)
    p2_elapsed = time.monotonic() - p2_t0

    elapsed_total = time.monotonic() - total_t0

    if reaction is None:
        return None, None, p2_raw, elapsed_total, phase1_result, p1_raw

    genome, fragment_id = absorb_reaction(genome, reaction, entry.date, year)
    return reaction, fragment_id, p2_raw, elapsed_total, phase1_result, p1_raw


def _print_reaction(entry: DevotionalEntry, reaction: ReaderReaction, fragment_id: str | None, phase1_result: dict | None):
    p2_icon = "✅" if reaction.verdict == Verdict.OK else "🔶"
    p1_icon = ""
    if phase1_result:
        p1_icon = "  🔤 FLAG" if phase1_result.get("verdict") == "FLAG" else "  ✅ CLEAN"

    print(f"\n{'─'*60}")
    print(f"  {p2_icon}  {entry.date}  |  {entry.id}{p1_icon}")
    print(f"  {reaction.reaction}")
    if reaction.quoted_pause:
        print(f"  Pause   : \"{reaction.quoted_pause}\"")
        print(f"  Category: {reaction.category.value if reaction.category else '—'}")
        if fragment_id:
            print(f"  Genome  : {fragment_id}")
    if phase1_result and phase1_result.get("verdict") == "FLAG":
        print(f"  P1 issue: {phase1_result.get('issue')}")
        print(f"  P1 quote: \"{phase1_result.get('quoted')}\"")
    print(f"{'─'*60}")


# ── Interactive mode ───────────────────────────────────────────────────────────

def run_interactive(
    entries: list[DevotionalEntry],
    lang: str,
    version: str,
    year: int,
    model_key: str,
    start_date: str | None,
    phase: int = 0,
):
    model    = get_model_for_key(model_key)
    # When local backend is active, providers.yml is the SOT for model selection
    from cloud_client import providers_for_phase, settings as _settings
    if _settings().get("default_backend") == "local":
        _p2 = providers_for_phase(2)
        if _p2:
            model = _p2[0]["model"]
    # DEBUG — confirm model SOT
    _p1_dbg = providers_for_phase(1)
    _p2_dbg = providers_for_phase(2)
    _p1_think = _p1_dbg[0].get("thinking_mode", {}).get("supported", False) if _p1_dbg else "?"
    _p2_think = _p2_dbg[0].get("thinking_mode", {}).get("supported", False) if _p2_dbg else "?"
    print(f"  [DEBUG] P1 -> {_p1_dbg[0]['model']} | think={_p1_think}")
    print(f"  [DEBUG] P2 -> {model} | think={_p2_think}")
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
    print(f"  🤖 P1 model: {PHASE1_MODEL}  |  P2 model: {model}")
    print(f"  🧬 Genome fragments loaded: {len(genome.fragments)}")
    print(f"\n  Controls: [A]pprove  [S]kip  [Q]uit\n")

    for i, entry in enumerate(pending, 1):
        print(f"\n  [{i}/{len(pending)}] Processing {entry.date}...")

        reaction, fragment_id, p2_raw, elapsed, phase1_result, p1_raw = _process_entry(
            entry, model, lang, version, year, genome, verbose=True, phase=phase
        )

        if reaction is None:
            print(f"  ⚠️  Phase 2 model error — {p2_raw}")
            action_type = ("error_tech" if any(x in (p2_raw or "") for x in ["429", "HTTP", "timeout", "URLError", "OSError"]) else "error_parse")
            record = build_record(
                entry.date, entry.id, lang, version,
                action=action_type,
                reaction=ReaderReaction(verdict=Verdict.OK, reaction="model error"),
                raw_response=p2_raw,
                phase1_verdict=(phase1_result or {}).get("verdict"),
                phase1_issue=(phase1_result or {}).get("issue"),
                phase1_quoted=(phase1_result or {}).get("quoted"),
                phase1_confidence=(phase1_result or {}).get("confidence"),
                phase1_raw=p1_raw,
            )
            append_record(log_path, record)
            continue

        _print_reaction(entry, reaction, fragment_id, phase1_result)

        while True:
            choice = input("  Action? [A]pprove / [S]kip / [Q]uit : ").strip().lower()
            if choice in ("a", "s", "q"):
                break

        if choice == "q":
            print("\n  👋 Session ended. Progress saved.")
            break

        if choice == "a":
            action_type = "flagged" if reaction.verdict == Verdict.PAUSE else "reviewed"
            record = build_record(
                entry.date, entry.id, lang, version,
                action=action_type,
                reaction=reaction,
                genome_fragment_id=fragment_id,
                phase1_verdict=(phase1_result or {}).get("verdict"),
                phase1_issue=(phase1_result or {}).get("issue"),
                phase1_quoted=(phase1_result or {}).get("quoted"),
                phase1_confidence=(phase1_result or {}).get("confidence"),
                phase1_raw=p1_raw,
            )
            append_record(log_path, record)
            print("  ✅ Approved and logged.")

    save_genome(genome, year)
    print_summary(log_path)

def run_overnight(
    entries: list[DevotionalEntry],
    lang: str,
    version: str,
    year: int,
    model_key: str,
    start_date: str | None,
    phase: int = 0,
):
    model    = get_model_for_key(model_key)
    # When local backend is active, providers.yml is the SOT for model selection
    from cloud_client import providers_for_phase, settings as _settings
    if _settings().get("default_backend") == "local":
        _p2 = providers_for_phase(2)
        if _p2:
            model = _p2[0]["model"]
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
        f"  P1 model : {PHASE1_MODEL}\n"
        f"  P2 model : {model}\n"
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
    p1_flag_count = 0
    error_count = 0
    run_t0      = time.monotonic()
    entry_times: list[float] = []

    try:
        for i, entry in enumerate(pending, 1):
            entry_start  = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            entry_header = (
                f"\n{'─'*60}\n"
                f"  [{i}/{len(pending)}] {entry.date} | {entry.id}\n"
                f"  Started: {entry_start}"
            )
            _log(run_log, entry_header)

            reaction, fragment_id, p2_raw, elapsed, phase1_result, p1_raw = _process_entry(
                entry, model, lang, version, year, genome, verbose=True, phase=phase
            )

            # Phase 1 summary
            p1_verdict = (phase1_result or {}).get("verdict", "error")
            p1_info = f"  [P1] {p1_verdict}"
            if p1_verdict == "FLAG":
                p1_flag_count += 1
                p1_info += f" | {(phase1_result or {}).get('issue','')[:80]}"

            # ── Save P1 thinking + response to log (file only) ──────────────
            if p1_raw:
                p1_think_m = re.search(r'<think>(.*?)</think>', p1_raw, re.DOTALL)
                if p1_think_m:
                    p1_think_text = p1_think_m.group(1).strip()
                    _log(run_log,
                         f"  [P1 THINKING {len(p1_think_text):,} chars]\n{p1_think_text}",
                         also_print=False)
                p1_clean = re.sub(r'<think>.*?</think>', '', p1_raw, flags=re.DOTALL).strip()
                if p1_clean and p1_verdict != "error":
                    _log(run_log, f"  [P1 RESPONSE]\n{p1_clean}", also_print=False)

            # ── P2 thinking extraction ──────────────────────────────────────
            p2_think_m = re.search(r'<think>(.*?)</think>', p2_raw or '', re.DOTALL)
            p2_think_text = p2_think_m.group(1).strip() if p2_think_m else ""
            think_info = f"  thinking: {len(p2_think_text):,} chars\n" if p2_think_text else ""


            if reaction is None:
                error_count += 1
                if phase == 1:
                    _log(run_log, f"{p1_info}\n  [P1-only] ({elapsed:.1f}s)")
                else:
                    err_msg = (p2_raw or "unknown error")[:800]
                    _log(run_log, f"{p1_info}\n  ⚠️  P2 ERROR ({elapsed:.1f}s)\n  {err_msg}")
                action_type = ("error_tech" if any(x in (p2_raw or "") for x in ["429", "HTTP", "timeout", "URLError", "OSError"]) else "error_parse")
                record = build_record(
                    entry.date, entry.id, lang, version,
                    action=action_type,
                    reaction=ReaderReaction(verdict=Verdict.OK, reaction="model error"),
                    raw_response=p2_raw,
                    phase1_verdict=(phase1_result or {}).get("verdict"),
                    phase1_issue=(phase1_result or {}).get("issue"),
                    phase1_quoted=(phase1_result or {}).get("quoted"),
                    phase1_confidence=(phase1_result or {}).get("confidence"),
                    phase1_raw=p1_raw,
                )
                append_record(log_path, record)
                continue

            if reaction.verdict.value == "OK":
                ok_count += 1
                p2_icon = "✅ OK"
                action_type = "reviewed"
            else:
                pause_count += 1
                cat = reaction.category.value if reaction.category else "other"
                p2_icon = f"🔶 PAUSE [{cat}]"
                action_type = "flagged"

            # Save P2 thinking + clean response to log (file only)
            if p2_think_text:
                _log(run_log,
                     f"  [P2 THINKING {len(p2_think_text):,} chars]\n{p2_think_text}",
                     also_print=False)
            p2_clean = re.sub(r'<think>.*?</think>', '', p2_raw or '', flags=re.DOTALL).strip()
            if p2_clean:
                _log(run_log, f"  [P2 RESPONSE]\n{p2_clean}", also_print=False)

            result_lines = (
                f"{p1_info}\n"
                f"  [P2] {p2_icon} | confidence: {reaction.confidence:.2f} | elapsed: {elapsed:.1f}s\n"
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
                action=action_type,
                reaction=reaction,
                genome_fragment_id=fragment_id,
                raw_response=p2_raw,
                phase1_verdict=(phase1_result or {}).get("verdict"),
                phase1_issue=(phase1_result or {}).get("issue"),
                phase1_quoted=(phase1_result or {}).get("quoted"),
                phase1_confidence=(phase1_result or {}).get("confidence"),
                phase1_raw=p1_raw,
            )
            append_record(log_path, record)
            entry_times.append(elapsed)

            if entry_times:
                avg       = sum(entry_times[-5:]) / len(entry_times[-5:])
                remaining = len(pending) - i
                eta_s     = int(avg * remaining)
                hh2, rem  = divmod(eta_s, 3600)
                mm2, ss2  = divmod(rem, 60)
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
        f"  🔶 PAUSE (P2): {pause_count}\n"
        f"  🔤 FLAG  (P1): {p1_flag_count}\n"
        f"  ⚠️  Errors   : {error_count}\n"
        f"  Run log      : {run_log}\n"
        f"{'═'*60}"
    )
    _log(run_log, footer)
    save_genome(genome, year)
    print_summary(log_path)
