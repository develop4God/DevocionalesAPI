#!/usr/bin/env python3
"""
critic_loop_v2.py — Devotional Content Quality Critic (v2)
============================================================
Layer 2 content validator for devocionales-json.
Catches: typos, grammar, tone drift, prayer/reflection mismatch.

Changes from v1:
  - Prompt forces model to QUOTE the specific phrase it flags
  - If no concrete issue found → verdict MUST be OK (no bluffing)
  - suggested_fix triggered by verdict==ISSUES (not score<=3)
  - Diff validator: fix >85% similar to original → logged as bluff, discarded
  - --evolve flag: auto-loads real fixes from audit log as few-shot seeds
  - num_predict raised to 1500
  - 27b is now the overnight default

Modes:
  --mode interactive   : review entry by entry (7b, fast)
  --mode overnight     : batch all entries, save report (27b, slow)

Usage:
  python critic_loop_v2.py --lang pt --version ARC --year 2025 --mode overnight
  python critic_loop_v2.py --lang pt --version NVI --year 2025 --evolve
  python critic_loop_v2.py --lang es --version NVI --year 2025 --mode overnight --model 27b
  python critic_loop_v2.py --list-files

Source of Truth: GitHub raw (develop4God/devocionales-json)
Audit log:       ./critic_audit_{lang}_{version}_{year}.jsonl  (local, resumable)
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/develop4God/devocionales-json"
    "/refs/heads/main"
)

KNOWN_FILES = {
    ("pt", "NVI"):        "Devocional_year_{year}_pt_NVI.json",
    ("pt", "ARC"):        "Devocional_year_{year}_pt_ARC.json",
    ("en", "KJV"):        "Devocional_year_{year}_en_KJV.json",
    ("en", "NIV"):        "Devocional_year_{year}_en_NIV.json",
    ("es", "NVI"):        "Devocional_year_{year}_es_NVI.json",
    ("fr", "LSG1910"):    "Devocional_year_{year}_fr_LSG1910.json",
    ("fr", "TOB"):        "Devocional_year_{year}_fr_TOB.json",
    ("de", "LU17"):       "Devocional_year_{year}_de_LU17.json",
    ("de", "SCH2000"):    "Devocional_year_{year}_de_SCH2000.json",
    ("hi", "HERV"):       "Devocional_year_{year}_hi_HERV.json",
    ("hi", "HIOV"):       "Devocional_year_{year}_hi_HIOV.json",
    ("ar", "NAV"):        "Devocional_year_{year}_ar_NAV.json",
    ("ar", "SVDA"):       "Devocional_year_{year}_ar_SVDA.json",
    ("tl", "ADB"):        "Devocional_year_{year}_tl_ADB.json",
    ("tl", "ASND"):       "Devocional_year_{year}_tl_ASND.json",
    ("ja", "リビングバイブル"):  "Devocional_year_{year}_ja_リビングバイブル.json",
    ("ja", "新改訳2003"):     "Devocional_year_{year}_ja_新改訳2003.json",
    ("zh", "和合本1919"):     "Devocional_year_{year}_zh_和合本1919.json",
    ("zh", "新译本"):         "Devocional_year_{year}_zh_新译本.json",
}

OLLAMA_URL = "http://localhost:11434/api/generate"

MODELS = {
    "7b":  "qwen2.5:7b",
    "27b": "qwen3.5:27b",
}

CONTENT_FIELDS = ["versiculo", "reflexion", "oracion"]

# Similarity threshold: fixes above this ratio are considered bluffs
BLUFF_THRESHOLD = 0.85

# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
You are a Senior {lang_label} Christian Editor and Theological Auditor.
Your task: audit devotional text for content quality — typos, grammar errors,
tone problems, and disconnection between the verse, reflection, and prayer.

### STRICT RULES:
1. GRAMMAR: Flag typos, broken sentences, unnatural phrasing for a native speaker.
2. TONE: Flag language that is too casual, too academic, or not evangelical.
3. COHERENCE: Flag if the prayer or reflection does not connect to the verse theme.
4. EVIDENCE REQUIRED: If you flag ISSUES, you MUST quote the exact phrase that has the problem.
   Do not flag vague impressions. If you cannot quote a specific broken phrase → verdict is OK.
5. NO BLUFFING: If the text is acceptable, say OK. Do not invent problems.
6. SCORES: 5=Perfect (no changes needed), 4=Minor issue found with specific fix,
   3=Clear problem that needs rewrite, 2=Serious flaw, 1=Wrong language or theological error.
   Reserve 4+ only when you can quote the exact problem phrase.

{few_shot_block}

Return ONLY valid JSON. No markdown, no preamble.

Schema:
{{
  "reasoning": "Step-by-step analysis. For each issue found, quote the exact phrase.",
  "verdict": "OK" | "ISSUES",
  "grammar": {{"score": 1-5, "notes": "quote the problem phrase or null"}},
  "tone": {{"score": 1-5, "notes": "quote the problem phrase or null"}},
  "coherence": {{"score": 1-5, "notes": "specific mismatch description or null"}},
  "overall_notes": "One sentence summary.",
  "suggested_fix": "Full corrected oracion if verdict==ISSUES, otherwise null"
}}"""

FEW_SHOT_HEADER = "### EXAMPLES OF GOOD VERDICTS:\n"

FEW_SHOT_EXAMPLE_TEMPLATE = """\
--- EXAMPLE {n} ---
Input oracion:
{original}

Correct verdict:
{verdict_json}
"""

USER_PROMPT_TEMPLATE = """\
Review this {lang_label} devotional entry ({version} Bible version).
Date: {date}

--- VERSICULO ---
{versiculo}

--- REFLEXION ---
{reflexion}

--- ORACION ---
{oracion}

Return only the JSON verdict. Quote exact phrases when flagging issues."""

LANG_LABELS = {
    "pt": "Brazilian Portuguese",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "hi": "Hindi",
    "ar": "Arabic",
    "ja": "Japanese",
    "zh": "Chinese",
    "tl": "Filipino (Tagalog)",
}

# ── Diff Validator ────────────────────────────────────────────────────────────

def similarity(a: str, b: str) -> float:
    """Returns 0.0–1.0 similarity ratio between two strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip(), b.strip()).ratio()


def validate_fix(original_oracion: str, fix: str) -> tuple[bool, float]:
    """
    Returns (is_bluff, ratio).
    is_bluff=True means the fix is too similar to original to be real.
    """
    ratio = similarity(original_oracion, fix)
    return ratio >= BLUFF_THRESHOLD, ratio

# ── Evolve: load few-shot seeds from audit log ────────────────────────────────

def load_evolve_seeds(log_path: Path, source_entries: dict, lang: str) -> list[dict]:
    """
    Loads real fix examples from the audit log where:
    - verdict == ISSUES
    - suggested_fix exists
    - fix is NOT a bluff (similarity < BLUFF_THRESHOLD)
    Returns list of {original, verdict_json} dicts for few-shot injection.
    """
    seeds = []
    if not log_path.exists():
        return seeds

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                v = rec.get("verdict", {})
                if v.get("verdict") != "ISSUES":
                    continue
                fix = v.get("suggested_fix")
                if not fix or fix in ("Não disponível", "null", ""):
                    continue

                date = rec["date"]
                day_entries = source_entries.get(date, [])
                if not day_entries:
                    continue
                original_oracion = day_entries[0].get("oracion", "")
                if not original_oracion:
                    continue

                is_bluff, ratio = validate_fix(original_oracion, fix)
                if is_bluff:
                    continue  # skip bluffs silently

                # Build a clean verdict JSON for the few-shot
                verdict_json = {
                    "reasoning": v.get("reasoning", ""),
                    "verdict": "ISSUES",
                    "grammar": v.get("grammar", {}),
                    "tone": v.get("tone", {}),
                    "coherence": v.get("coherence", {"score": 4, "notes": None}),
                    "overall_notes": v.get("overall_notes", ""),
                    "suggested_fix": fix,
                }
                seeds.append({
                    "original": original_oracion,
                    "verdict_json": json.dumps(verdict_json, ensure_ascii=False, indent=2),
                    "date": date,
                    "ratio": ratio,
                })
            except (json.JSONDecodeError, KeyError):
                continue

    # Max 3 seeds, prefer lowest similarity (most different = clearest rewrite)
    seeds.sort(key=lambda x: x["ratio"])
    return seeds[:3]


def build_few_shot_block(seeds: list[dict]) -> str:
    if not seeds:
        return ""
    block = FEW_SHOT_HEADER
    for i, seed in enumerate(seeds, 1):
        block += FEW_SHOT_EXAMPLE_TEMPLATE.format(
            n=i,
            original=seed["original"],
            verdict_json=seed["verdict_json"],
        )
    return block

# ── GitHub Fetch ──────────────────────────────────────────────────────────────

def build_url(lang: str, version: str, year: int) -> str:
    key = (lang, version)
    filename = KNOWN_FILES.get(key, f"Devocional_year_{year}_{lang}_{version}.json")
    return f"{GITHUB_RAW_BASE}/{filename.format(year=year)}"


def fetch_remote(url: str) -> dict:
    print(f"  📡 Fetching: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "critic-loop/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTP {e.code}: {url}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"  ❌ Network error: {e.reason}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"  ❌ Invalid JSON from remote: {e}")
        sys.exit(1)


def extract_entries(data: dict, lang: str) -> list[tuple[str, dict]]:
    try:
        date_map = data["data"][lang]
    except KeyError:
        print(f"  ❌ Could not find data.{lang} in remote JSON")
        sys.exit(1)

    entries = []
    for date_key, date_value in date_map.items():
        if isinstance(date_value, list):
            for entry in date_value:
                if isinstance(entry, dict):
                    entries.append((date_key, entry))
    entries.sort(key=lambda x: x[0])
    return entries

# ── Audit Log ─────────────────────────────────────────────────────────────────

def audit_path(lang: str, version: str, year: int) -> Path:
    return Path(f"critic_audit_{lang}_{version}_{year}.jsonl")


def load_reviewed_dates(log_path: Path) -> set[str]:
    reviewed = set()
    if not log_path.exists():
        return reviewed
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                reviewed.add(rec["date"])
            except (json.JSONDecodeError, KeyError):
                pass
    return reviewed


def append_audit(log_path: Path, record: dict):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize_nulls(obj):
    """Recursively replace string 'null' with actual None."""
    if isinstance(obj, dict):
        return {k: _sanitize_nulls(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nulls(i) for i in obj]
    if obj == "null":
        return None
    return obj

# ── Ollama ────────────────────────────────────────────────────────────────────

def call_ollama(model: str, system: str, user: str) -> dict | None:
    payload = {
        "model": model,
        "prompt": user,
        "system": system,
        "stream": False,
        "format": "json",         # forces valid JSON output regardless of model
        "options": {
            "temperature": 0.1,   # lower = more consistent, less hallucination
            "num_predict": 1500,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            raw_text = result.get("response", "").strip()
            # Strip markdown fences
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            parsed = json.loads(raw_text.strip())
            # Fix model outputting "null" as string instead of null
            return _sanitize_nulls(parsed)
    except urllib.error.URLError as e:
        print(f"  ⚠️  Ollama unreachable: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        print(f"  ⚠️  Model returned invalid JSON: {e}")
        return None


def build_prompts(entry: dict, lang: str, version: str,
                  few_shot_block: str) -> tuple[str, str]:
    lang_label = LANG_LABELS.get(lang, lang)
    system = SYSTEM_PROMPT_TEMPLATE.format(
        lang_label=lang_label,
        few_shot_block=few_shot_block,
    )
    user = USER_PROMPT_TEMPLATE.format(
        lang_label=lang_label,
        version=version,
        date=entry.get("date", "?"),
        versiculo=entry.get("versiculo", ""),
        reflexion=entry.get("reflexion", ""),
        oracion=entry.get("oracion", ""),
    )
    return system, user

# ── Display ───────────────────────────────────────────────────────────────────

def print_verdict(date_key: str, entry: dict, verdict: dict,
                  fix_quality: str | None = None):
    v = verdict.get("verdict", "?")
    icon = "✅" if v == "OK" else "⚠️ "
    grammar   = verdict.get("grammar", {})
    tone      = verdict.get("tone", {})
    coherence = verdict.get("coherence", {})
    fix       = verdict.get("suggested_fix") or ""

    print(f"\n{'─'*60}")
    print(f"  {icon}  {date_key}  |  {entry.get('id','?')}")
    print(f"  Grammar   [{grammar.get('score','?')}/5]: {grammar.get('notes') or ''}")
    print(f"  Tone      [{tone.get('score','?')}/5]: {tone.get('notes') or ''}")
    print(f"  Coherence [{coherence.get('score','?')}/5]: {coherence.get('notes') or ''}")
    if verdict.get("overall_notes"):
        print(f"  Summary: {verdict['overall_notes']}")
    if fix:
        if fix_quality == "bluff":
            print(f"  ⚠️  Fix discarded (identical to original — model bluffed)")
        else:
            print(f"  💡 Fix: {fix[:300]}{'...' if len(fix) > 300 else ''}")
    print(f"{'─'*60}")


def print_overnight_summary(log_path: Path):
    if not log_path.exists():
        print("  No audit log found.")
        return

    total = ok = issues = bluffs = errors = 0
    issue_list = []

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                total += 1
                v = rec.get("verdict", {})
                verdict = v.get("verdict", "ERROR") if isinstance(v, dict) else "ERROR"
                fq = rec.get("fix_quality")
                if verdict == "OK":
                    ok += 1
                elif verdict == "ISSUES":
                    issues += 1
                    if fq == "bluff":
                        bluffs += 1
                    issue_list.append((rec["date"], fq))
                else:
                    errors += 1
            except (json.JSONDecodeError, KeyError):
                errors += 1

    real_issues = [(d, fq) for d, fq in issue_list if fq != "bluff"]

    print(f"\n{'═'*60}")
    print(f"  📊 OVERNIGHT REPORT — {log_path.name}")
    print(f"{'═'*60}")
    print(f"  Total reviewed : {total}")
    print(f"  ✅ OK          : {ok}")
    print(f"  ⚠️  Issues      : {issues} ({bluffs} bluffs discarded)")
    print(f"  ❌ Errors      : {errors}")
    if real_issues:
        print(f"\n  Entries needing real review ({len(real_issues)}):")
        for d, _ in real_issues:
            print(f"    • {d}")
    print(f"{'═'*60}\n")

# ── Interactive Mode ──────────────────────────────────────────────────────────

def run_interactive(entries: list, lang: str, version: str,
                    model: str, log_path: Path, start_date: str | None,
                    few_shot_block: str):
    reviewed = load_reviewed_dates(log_path)
    pending = [
        (d, e) for d, e in entries
        if d not in reviewed and (start_date is None or d >= start_date)
    ]

    if not pending:
        print("  ✅ All entries already reviewed.")
        return

    print(f"\n  📋 {len(pending)} entries to review")
    print(f"  📝 Audit log: {log_path}")
    print(f"  🤖 Model: {model}")
    print(f"\n  Controls: [A]pprove  [S]kip  [Q]uit\n")

    done = 0
    for date_key, entry in pending:
        done += 1
        print(f"\n  [{done}/{len(pending)}] Processing {date_key}...")

        system, user = build_prompts(entry, lang, version, few_shot_block)
        verdict = call_ollama(model, system, user)

        if verdict is None:
            print("  ⚠️  Model error — skipping")
            continue

        # Validate fix
        fix_quality = None
        fix = verdict.get("suggested_fix")
        if fix and verdict.get("verdict") == "ISSUES":
            original_oracion = entry.get("oracion", "")
            is_bluff, ratio = validate_fix(original_oracion, fix)
            if is_bluff:
                fix_quality = "bluff"
                verdict["suggested_fix"] = None  # discard

        print_verdict(date_key, entry, verdict, fix_quality)

        while True:
            choice = input("  Action? [A]pprove / [S]kip / [Q]uit : ").strip().lower()
            if choice in ("a", "s", "q"):
                break

        if choice == "q":
            print("\n  👋 Session ended. Progress saved.")
            break

        if choice == "a":
            record = {
                "date": date_key,
                "id": entry.get("id", ""),
                "lang": lang,
                "version": version,
                "reviewed_at": datetime.utcnow().isoformat(),
                "action": "approved",
                "fix_quality": fix_quality,
                "verdict": verdict,
            }
            append_audit(log_path, record)
            print("  ✅ Approved and logged.")
        elif choice == "s":
            print("  ⏭  Skipped.")

    print(f"\n  Session complete. Audit log: {log_path}")

# ── Overnight Mode ────────────────────────────────────────────────────────────

def run_overnight(entries: list, lang: str, version: str,
                  model: str, log_path: Path, start_date: str | None,
                  few_shot_block: str):
    reviewed = load_reviewed_dates(log_path)
    pending = [
        (d, e) for d, e in entries
        if d not in reviewed and (start_date is None or d >= start_date)
    ]

    if not pending:
        print("  ✅ All entries already reviewed.")
        print_overnight_summary(log_path)
        return

    print(f"\n  🌙 OVERNIGHT MODE — {len(pending)} entries")
    print(f"  🤖 Model: {model}")
    print(f"  📝 Audit log: {log_path}")
    print(f"  (Press Ctrl+C to stop cleanly)\n")

    done = 0
    try:
        for date_key, entry in pending:
            done += 1
            print(f"  [{done}/{len(pending)}] {date_key}...", end=" ", flush=True)

            system, user = build_prompts(entry, lang, version, few_shot_block)
            verdict = call_ollama(model, system, user)

            if verdict is None:
                print("⚠️  model error — skipped")
                continue

            # Validate fix
            fix_quality = None
            fix = verdict.get("suggested_fix")
            if fix and verdict.get("verdict") == "ISSUES":
                original_oracion = entry.get("oracion", "")
                is_bluff, ratio = validate_fix(original_oracion, fix)
                if is_bluff:
                    fix_quality = "bluff"
                    verdict["suggested_fix"] = None
                    print(f"⚠️  bluff", end=" ")

            v = verdict.get("verdict", "ERROR")
            icon = "✅" if v == "OK" else "⚠️ "
            print(icon)

            record = {
                "date": date_key,
                "id": entry.get("id", ""),
                "lang": lang,
                "version": version,
                "reviewed_at": datetime.utcnow().isoformat(),
                "action": "overnight",
                "fix_quality": fix_quality,
                "verdict": verdict,
            }
            append_audit(log_path, record)

    except KeyboardInterrupt:
        print("\n\n  🛑 Interrupted. Progress saved.")

    print_overnight_summary(log_path)

# ── List Files ────────────────────────────────────────────────────────────────

def list_files():
    print("\n  Available lang/version combinations:\n")
    print(f"  {'LANG':<6} {'VERSION':<18} {'FILENAME PATTERN'}")
    print(f"  {'─'*6} {'─'*18} {'─'*40}")
    for (lang, version), pattern in sorted(KNOWN_FILES.items()):
        print(f"  {lang:<6} {version:<18} {pattern}")
    print()

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Devotional Content Quality Critic v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python critic_loop_v2.py --lang pt --version ARC --year 2025 --mode overnight\n"
            "  python critic_loop_v2.py --lang es --version NVI --year 2025 --mode overnight --model 27b\n"
            "  python critic_loop_v2.py --lang pt --version NVI --year 2025 --evolve\n"
            "  python critic_loop_v2.py --list-files\n"
        ),
    )
    parser.add_argument("--lang",       help="Language code (e.g. pt, en, es)")
    parser.add_argument("--version",    help="Bible version (e.g. NVI, ARC, KJV)")
    parser.add_argument("--year",       type=int, help="Year (2025 or 2026)")
    parser.add_argument("--mode",       choices=["interactive", "overnight"],
                        default="interactive")
    parser.add_argument("--model",      choices=["7b", "27b"], default="27b",
                        help="Ollama model (default: 27b for overnight)")
    parser.add_argument("--start-date", dest="start_date",
                        help="Skip entries before this date (YYYY-MM-DD)")
    parser.add_argument("--evolve",     action="store_true",
                        help="Load real fixes from audit log as few-shot seeds")
    parser.add_argument("--list-files", action="store_true",
                        help="List all known lang/version combinations and exit")

    args = parser.parse_args()

    if args.list_files:
        list_files()
        return

    missing = [f for f, v in [("--lang", args.lang), ("--version", args.version),
                                ("--year", args.year)] if not v]
    if missing:
        parser.error(f"Required: {', '.join(missing)}")

    model_name = MODELS[args.model]
    log_path   = audit_path(args.lang, args.version, args.year)
    url        = build_url(args.lang, args.version, args.year)

    print(f"\n{'═'*60}")
    print(f"  📖 Devotional Content Critic v2")
    print(f"  Lang: {args.lang} | Version: {args.version} | Year: {args.year}")
    print(f"  Mode: {args.mode} | Model: {model_name}")
    if args.evolve:
        print(f"  🧬 Evolve: ON")
    print(f"{'═'*60}")

    # Fetch remote JSON
    data    = fetch_remote(url)
    entries = extract_entries(data, args.lang)
    print(f"  ✅ Loaded {len(entries)} entries from remote")

    # Build few-shot block
    few_shot_block = ""
    if args.evolve:
        source_entries = data["data"][args.lang]
        seeds = load_evolve_seeds(log_path, source_entries, args.lang)
        if seeds:
            few_shot_block = build_few_shot_block(seeds)
            print(f"  🧬 Loaded {len(seeds)} evolve seeds from audit log")
        else:
            print(f"  🧬 No valid seeds found yet — running without few-shot")

    # Run mode
    if args.mode == "interactive":
        run_interactive(entries, args.lang, args.version,
                        model_name, log_path, args.start_date, few_shot_block)
    else:
        run_overnight(entries, args.lang, args.version,
                      model_name, log_path, args.start_date, few_shot_block)


if __name__ == "__main__":
    main()
