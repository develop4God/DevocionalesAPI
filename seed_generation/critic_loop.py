#!/usr/bin/env python3
"""
critic_loop.py — Devotional Content Quality Critic
====================================================
Uses a local Ollama model to review devotional entries for:
  - Grammar / fluency (native-level)
  - Theological tone (evangelical Christian)

Modes:
  --mode interactive   : review entry by entry (uses 7b model, fast)
  --mode overnight     : batch all entries, save report (uses 27b, slow)

Usage:
  python critic_loop.py --lang pt --version NVI --year 2025
  python critic_loop.py --lang pt --version ARC --year 2026 --mode overnight
  python critic_loop.py --lang pt --version NVI --year 2025 --start-date 2025-03-01
  python critic_loop.py --list-files

Source of Truth: GitHub raw (develop4God/devocionales-json)
Audit log:       ./critic_audit_{lang}_{version}_{year}.jsonl  (local, resumable)
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/develop4God/devocionales-json"
    "/refs/heads/main"
)

# Map lang+version → filename (handles non-ASCII version names like ja, zh)
# Pattern: Devocional_year_{year}_{lang}_{version}.json
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

# Ollama endpoint
OLLAMA_URL = "http://localhost:11434/api/generate"

MODELS = {
    "7b":  "qwen2.5-coder:7b",
    "27b": "qwen3.5:27b",
}

# Fields the critic reads (content fields only)
CONTENT_FIELDS = ["versiculo", "reflexion", "oracion"]

# ── Critic Prompt ─────────────────────────────────────────────────────────────




SYSTEM_PROMPT_TEMPLATE = """You are a Senior {lang_label} Christian Editor and Theological Auditor.
Your role is to critically audit the provided text to ensure it meets high-quality standards.

### THE CRITICAL PROTOCOL:
1. GRAMMAR / FLUENCY: Check for native-level flow. Flag stiff phrasing or "AI-sounding" repetition.
2. THEOLOGICAL TONE: Ensure the register is reverent and evangelical. Flag cold, academic, or overly casual language.
3. ANALYSIS: You must analyze the text step-by-step before deciding the scores.

Return ONLY valid JSON. 

Schema:
{{
  "reasoning": "A step-by-step analysis of the text's strengths and weaknesses. Look for at least one subtle area for improvement, even in good texts.",
  "verdict": "OK" | "ISSUES",
  "grammar": {{
    "score": 1-5,
    "notes": "brief note or null"
  }},
  "tone": {{
    "score": 1-5,
    "notes": "brief note or null"
  }},
  "overall_notes": "Summary of the findings.",
  "suggested_fix": "Full corrected version if score <= 3, otherwise null"
}}

Scores: 5=Perfect, 4=Minor polish needed, 3=Acceptable but generic, 2=Needs rewrite, 1=Theological or linguistic failure."""

USER_PROMPT_TEMPLATE = """Review this {lang_label} devotional entry ({version} Bible version).
Date: {date}

--- VERSICULO ---
{versiculo}

--- REFLEXION ---
{reflexion}

--- ORACION ---
{oracion}

Return only the JSON verdict."""

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

# ── GitHub Fetch ──────────────────────────────────────────────────────────────

def build_url(lang: str, version: str, year: int) -> str:
    key = (lang, version)
    if key not in KNOWN_FILES:
        # fallback: construct directly
        filename = f"Devocional_year_{year}_{lang}_{version}.json"
    else:
        filename = KNOWN_FILES[key].format(year=year)
    return f"{GITHUB_RAW_BASE}/{filename}"


def fetch_remote(url: str) -> dict:
    print(f"  📡 Fetching: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "critic-loop/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
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
    """Returns list of (date_key, entry_dict) sorted by date."""
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
    """Returns set of date strings already in the audit log."""
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

# ── Ollama ────────────────────────────────────────────────────────────────────

def call_ollama(model: str, system: str, user: str) -> dict | None:
    payload = {
        "model": model,
        "prompt": user,
        "system": system,
        "stream": False,
        "options": {
            "temperature": 0.2,   # low temp = consistent critic
            "num_predict": 512,
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            raw_text = result.get("response", "").strip()
            # Strip markdown fences if model adds them
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            return json.loads(raw_text.strip())
    except urllib.error.URLError as e:
        print(f"  ⚠️  Ollama unreachable: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        print(f"  ⚠️  Model returned invalid JSON: {e}")
        return None


def build_prompts(entry: dict, lang: str, version: str) -> tuple[str, str]:
    lang_label = LANG_LABELS.get(lang, lang)
    system = SYSTEM_PROMPT_TEMPLATE.format(lang_label=lang_label)
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

def print_verdict(date_key: str, entry: dict, verdict: dict):
    v = verdict.get("verdict", "?")
    icon = "✅" if v == "OK" else "⚠️ "
    grammar = verdict.get("grammar", {})
    tone    = verdict.get("tone", {})
    g_score = grammar.get("score", "?")
    t_score = tone.get("score", "?")
    g_note  = grammar.get("notes") or ""
    t_note  = tone.get("notes") or ""
    overall = verdict.get("overall_notes") or ""
    fix     = verdict.get("suggested_fix") or ""

    print(f"\n{'─'*60}")
    print(f"  {icon}  {date_key}  |  Entry: {entry.get('id','?')}")
    print(f"  Grammar [{g_score}/5]: {g_note}")
    print(f"  Tone    [{t_score}/5]: {t_note}")
    if overall:
        print(f"  Summary: {overall}")
    if fix:
        print(f"  💡 Fix: {fix}")
    print(f"{'─'*60}")


def print_overnight_summary(log_path: Path):
    if not log_path.exists():
        print("  No audit log found.")
        return

    total = ok = issues = errors = 0
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
                if verdict == "OK":
                    ok += 1
                elif verdict == "ISSUES":
                    issues += 1
                    issue_list.append(rec["date"])
                else:
                    errors += 1
            except (json.JSONDecodeError, KeyError):
                errors += 1

    print(f"\n{'═'*60}")
    print(f"  📊 OVERNIGHT REPORT — {log_path.name}")
    print(f"{'═'*60}")
    print(f"  Total reviewed : {total}")
    print(f"  ✅ OK          : {ok}")
    print(f"  ⚠️  Issues      : {issues}")
    print(f"  ❌ Errors      : {errors}")
    if issue_list:
        print(f"\n  Entries needing review:")
        for d in issue_list:
            print(f"    • {d}")
    print(f"{'═'*60}\n")

# ── Interactive Mode ──────────────────────────────────────────────────────────

def run_interactive(entries: list, lang: str, version: str,
                    model: str, log_path: Path, start_date: str | None):
    reviewed = load_reviewed_dates(log_path)
    pending = [
        (d, e) for d, e in entries
        if d not in reviewed and (start_date is None or d >= start_date)
    ]

    total_pending = len(pending)
    if total_pending == 0:
        print("  ✅ All entries already reviewed. Nothing to do.")
        return

    print(f"\n  📋 {total_pending} entries to review")
    print(f"  📝 Audit log: {log_path}")
    print(f"  🤖 Model: {model}")
    print(f"\n  Controls: [A]pprove  [S]kip  [Q]uit\n")

    done = 0
    for date_key, entry in pending:
        done += 1
        print(f"\n  [{done}/{total_pending}] Processing {date_key}...")

        system, user = build_prompts(entry, lang, version)
        verdict = call_ollama(model, system, user)

        if verdict is None:
            print("  ⚠️  Model error — skipping this entry")
            continue

        print_verdict(date_key, entry, verdict)

        # User decision
        while True:
            choice = input("  Action? [A]pprove / [S]kip / [Q]uit : ").strip().lower()
            if choice in ("a", "s", "q"):
                break
            print("  Please press A, S, or Q.")

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
                "verdict": verdict,
            }
            append_audit(log_path, record)
            print("  ✅ Approved and logged.")

        elif choice == "s":
            print("  ⏭  Skipped (not logged).")

    print(f"\n  Session complete. Audit log: {log_path}")

# ── Overnight Mode ────────────────────────────────────────────────────────────

def run_overnight(entries: list, lang: str, version: str,
                  model: str, log_path: Path, start_date: str | None):
    reviewed = load_reviewed_dates(log_path)
    pending = [
        (d, e) for d, e in entries
        if d not in reviewed and (start_date is None or d >= start_date)
    ]

    total = len(pending)
    if total == 0:
        print("  ✅ All entries already reviewed.")
        print_overnight_summary(log_path)
        return

    print(f"\n  🌙 OVERNIGHT MODE — {total} entries")
    print(f"  🤖 Model: {model}")
    print(f"  📝 Audit log: {log_path}")
    print(f"  (Press Ctrl+C to stop cleanly)\n")

    done = 0
    issues_found = 0

    try:
        for date_key, entry in pending:
            done += 1
            print(f"  [{done}/{total}] {date_key}...", end=" ", flush=True)

            system, user = build_prompts(entry, lang, version)
            verdict = call_ollama(model, system, user)

            if verdict is None:
                print("⚠️  model error — skipped")
                continue

            v = verdict.get("verdict", "ERROR")
            icon = "✅" if v == "OK" else "⚠️ "
            print(icon)

            if v == "ISSUES":
                issues_found += 1

            record = {
                "date": date_key,
                "id": entry.get("id", ""),
                "lang": lang,
                "version": version,
                "reviewed_at": datetime.utcnow().isoformat(),
                "action": "overnight",
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
        description="Devotional Content Quality Critic — AI-powered loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python critic_loop.py --lang pt --version NVI --year 2025\n"
            "  python critic_loop.py --lang pt --version ARC --year 2026 --mode overnight\n"
            "  python critic_loop.py --lang pt --version NVI --year 2025 --start-date 2025-06-01\n"
            "  python critic_loop.py --list-files\n"
        ),
    )
    parser.add_argument("--lang",       help="Language code (e.g. pt, en, de)")
    parser.add_argument("--version",    help="Bible version (e.g. NVI, ARC, KJV)")
    parser.add_argument("--year",       type=int, help="Year (2025 or 2026)")
    parser.add_argument("--mode",       choices=["interactive", "overnight"],
                        default="interactive", help="Review mode (default: interactive)")
    parser.add_argument("--model",      choices=["7b", "27b"], default="7b",
                        help="Ollama model to use (default: 7b)")
    parser.add_argument("--start-date", dest="start_date",
                        help="Skip entries before this date (YYYY-MM-DD)")
    parser.add_argument("--list-files", action="store_true",
                        help="List all known lang/version combinations and exit")

    args = parser.parse_args()

    if args.list_files:
        list_files()
        return

    # Validate required args
    missing = [f for f, v in [("--lang", args.lang), ("--version", args.version),
                               ("--year", args.year)] if not v]
    if missing:
        parser.error(f"Required: {', '.join(missing)}")

    model_name = MODELS[args.model]
    log_path   = audit_path(args.lang, args.version, args.year)
    url        = build_url(args.lang, args.version, args.year)

    print(f"\n{'═'*60}")
    print(f"  📖 Devotional Content Critic")
    print(f"  Lang: {args.lang} | Version: {args.version} | Year: {args.year}")
    print(f"  Mode: {args.mode} | Model: {model_name}")
    print(f"{'═'*60}")

    # Fetch remote JSON
    data    = fetch_remote(url)
    entries = extract_entries(data, args.lang)
    print(f"  ✅ Loaded {len(entries)} entries from remote")

    # Run selected mode
    if args.mode == "interactive":
        run_interactive(entries, args.lang, args.version,
                        model_name, log_path, args.start_date)
    else:
        run_overnight(entries, args.lang, args.version,
                      model_name, log_path, args.start_date)


if __name__ == "__main__":
    main()
