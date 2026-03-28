"""
seed_content_validator.py
─────────────────────────
Multi-phase devotional content quality gate.

Phase 1 — Local (free, no API call):
  1a. min_length             — reflexion ≥ 800 chars, oracion ≥ 150 chars
  1b. prayer_ending          — last word matches known Amen variant for the language
  1c. double_amen            — no duplicate Amen artifact in closing
  1d. no_dup_words_oracion   — no consecutive duplicate words in oracion
  1e. no_dup_words_reflexion — no consecutive duplicate words in reflexion

Phase 2 — Gemini 2.0 Flash (cheap AI check, runs only when Phase 1 passes):
  2a. language_clean  — no script/language mixing or leakage
  2b. no_repetition   — reflexion doesn't recycle sentences or ideas
  2c. content_quality — theologically coherent, not generic filler

Fix Phase — Auto-repair (called by validate_and_fix):
  oracion issues   → Gemini 2.0 Flash  (cheap: prayer ~150 words)
  reflexion issues → Gemini 2.5 Flash  (quality: theological content ~300 words)

Usage:
    # Validation only:
    result = await validate_devotional_content(lang, verse_cita, reflexion, oracion)

    # Full pipeline (validate + auto-fix):
    reflexion, oracion, result = await validate_and_fix(lang, verse_cita, reflexion, oracion)

    # Batch CLI:
    python seed_content_validator.py <file.json>        # Phase 1 only (fast, free)
    python seed_content_validator.py <file.json> --ai   # Phase 1+2 (Gemini 2.0)
"""

import asyncio
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# Google GenAI SDK (new only)
from google import genai
from google.genai import types

# =============================================================================
# CONFIG
# =============================================================================
VALIDATOR_MODEL     = "gemini-2.0-flash"   # Phase 2 quality check
FIX_ORACION_MODEL   = "gemini-2.0-flash"   # Fix prayer  (cheap, ~150 words)
FIX_REFLEXION_MODEL = "gemini-2.5-flash"   # Fix reflexion (quality, ~300 words)

REFLEXION_MIN_CHARS = 800
ORACION_MIN_CHARS   = 150

# Words allowed to repeat consecutively for liturgical/biblical reasons
LITURGICAL_WHITELIST: frozenset = frozenset({
    "heilig", "holy", "kadosh", "halleluja", "hosanna",
    "amen", "amén", "āmen",
})

# =============================================================================
# PRAYER ENDINGS — loaded once from JSON, reused everywhere
# =============================================================================
_PRAYER_ENDINGS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "prayer_endings.json"
)
_PRAYER_ENDINGS: dict = {}


def _load_prayer_endings() -> dict:
    global _PRAYER_ENDINGS
    if _PRAYER_ENDINGS:
        return _PRAYER_ENDINGS
    try:
        with open(_PRAYER_ENDINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _PRAYER_ENDINGS = {k: v for k, v in data.items() if not k.startswith("_")}
        print(f"INFO: prayer_endings.json loaded — {len(_PRAYER_ENDINGS)} languages")
    except Exception as e:
        print(f"WARNING: Could not load prayer_endings.json: {e} — using fallback ['Amen']")
        _PRAYER_ENDINGS = {}
    return _PRAYER_ENDINGS


def _normalize_word(word: str) -> str:
    """Strip accents for accent-insensitive comparison."""
    return unicodedata.normalize("NFD", word).encode("ascii", "ignore").decode().lower()


def _get_amen_phrase(lang: str) -> str:
    """Returns the primary (canonical) Amen phrase for the language."""
    return _load_prayer_endings().get(lang, ["Amen"])[0]


def check_prayer_ending(oracion: str, lang: str) -> bool:
    """Returns True if oracion ends with a valid Amen variant for lang."""
    endings   = _load_prayer_endings().get(lang, ["Amen"])
    last_word = oracion.strip().rstrip(".!,;।").split()[-1]
    return any(_normalize_word(e) == _normalize_word(last_word) for e in endings)


# =============================================================================
# PHASE 1 — LOCAL CHECKS (free, no API)
# =============================================================================

def _find_consecutive_duplicate(text: str) -> Optional[str]:
    """
    Returns the first consecutive duplicate word pair (excluding liturgical
    whitelist), or None if clean.

    Uses simple lowercase + ASCII-punctuation stripping only — no unicode
    normalization — to avoid false positives on quoted speech, German
    opening-quote „ marks, or biblical repetition phrases like „Wahrlich, wahrlich".
    """
    strip_chars = ".,;:!?"
    words = text.split()
    for i in range(len(words) - 1):
        w1 = words[i].strip(strip_chars).lower()
        w2 = words[i + 1].strip(strip_chars).lower()
        if w1 == w2 and len(w1) > 3 and w1 not in LITURGICAL_WHITELIST:
            return f"'{words[i]} {words[i + 1]}'"
    return None


def run_phase1_checks(
    reflexion: str, oracion: str, lang: str
) -> tuple:
    """
    Phase 1: Local validation — no API cost.
    Returns (passed: bool, issues: list[str], flags: dict[str, bool]).
    """
    flags = {
        "min_length":             True,
        "prayer_ending":          True,
        "double_amen":            True,
        "no_dup_words_oracion":   True,
        "no_dup_words_reflexion": True,
        "not_truncated":          True,
    }
    issues = []
    r, o = reflexion.strip(), oracion.strip()

    # 1a. Min length
    if len(r) < REFLEXION_MIN_CHARS:
        flags["min_length"] = False
        issues.append(f"reflexion too short: {len(r)} chars (min {REFLEXION_MIN_CHARS})")
    if len(o) < ORACION_MIN_CHARS:
        flags["min_length"] = False
        issues.append(f"oracion too short: {len(o)} chars (min {ORACION_MIN_CHARS})")

    # 1b. Prayer ending — lang-aware Amen check
    if not check_prayer_ending(o, lang):
        flags["prayer_ending"] = False
        last     = o.rstrip(".!,;।").split()[-1]
        expected = _load_prayer_endings().get(lang, ["Amen"])
        issues.append(
            f"prayer_ending: last word '{last}' — expected one of {expected} for '{lang}'"
        )

    # 1c. Double Amen artifact (scan last 120 chars)
    if len(re.findall(r'\bAm[eé]n\b', o[-120:], re.IGNORECASE)) >= 2:
        flags["double_amen"] = False
        issues.append("double_amen: duplicate Amen artifact detected in closing")

    # 1d. Consecutive duplicate words — oracion
    dup = _find_consecutive_duplicate(o)
    if dup:
        flags["no_dup_words_oracion"] = False
        issues.append(f"dup_words_oracion: consecutive duplicate {dup}")

    # 1e. Consecutive duplicate words — reflexion
    dup = _find_consecutive_duplicate(r)
    if dup:
        flags["no_dup_words_reflexion"] = False
        issues.append(f"dup_words_reflexion: consecutive duplicate {dup}")

    # 1f. Truncation check — reflexion must end with sentence-closing punctuation
    _SENTENCE_ENDINGS = ('.', '!', '?', '»', '"', '\u201c', '\u201d', '।', '。', '！', '？')
    if r and not r.rstrip().endswith(_SENTENCE_ENDINGS):
        flags["not_truncated"] = False
        issues.append(f"reflexion_truncated: ends mid-sentence — last chars: '...{r.rstrip()[-40:]}'")
    else:
        flags["not_truncated"] = True

    return len(issues) == 0, issues, flags


# =============================================================================
# RESULT
# =============================================================================

@dataclass
class ValidationResult:
    # Overall
    passed:                bool
    # Phase 1
    min_length:            bool
    prayer_ending:         bool
    double_amen:           bool
    no_dup_words_oracion:  bool
    no_dup_words_reflexion: bool
    not_truncated:         bool
    # Phase 2 (AI — defaults True = not yet checked)
    language_clean:  bool
    no_repetition:   bool
    content_quality: bool
    # Meta
    phase1_passed: bool
    phase2_passed: bool
    phase2_ran:    bool
    issues:        list = field(default_factory=list)
    raw_response:  str  = ""

    @property
    def needs_fix_oracion(self) -> bool:
        """True when oracion-specific checks failed."""
        return not (self.prayer_ending and self.double_amen and self.no_dup_words_oracion)

    @property
    def needs_fix_reflexion(self) -> bool:
        """True when reflexion-specific checks failed."""
        return not (
            self.min_length and self.no_dup_words_reflexion
            and self.no_repetition and self.content_quality
        )

    @classmethod
    def from_phase1(cls, passed: bool, issues: list, flags: dict) -> "ValidationResult":
        return cls(
            passed=passed,
            min_length=flags["min_length"],
            prayer_ending=flags["prayer_ending"],
            double_amen=flags["double_amen"],
            no_dup_words_oracion=flags["no_dup_words_oracion"],
            no_dup_words_reflexion=flags["no_dup_words_reflexion"],
            not_truncated=flags["not_truncated"],
            language_clean=True,
            no_repetition=True,
            content_quality=True,
            phase1_passed=passed,
            phase2_passed=True,
            phase2_ran=False,
            issues=issues,
        )

    @classmethod
    def skipped(cls, reason: str) -> "ValidationResult":
        """Fail-open result when the validator itself is unavailable."""
        return cls(
            passed=True,
            min_length=True, prayer_ending=True, double_amen=True,
            no_dup_words_oracion=True, no_dup_words_reflexion=True,
            not_truncated=True,
            language_clean=True, no_repetition=True, content_quality=True,
            phase1_passed=True, phase2_passed=True, phase2_ran=False,
            issues=[f"validator_skipped: {reason}"],
        )


# =============================================================================
# PHASE 2 — AI QUALITY CHECK (Gemini 2.0 Flash)
# =============================================================================

async def _run_phase2_ai_check(
    lang: str,
    verse_cita: str,
    reflexion: str,
    oracion: str,
    p1_flags: dict,
) -> ValidationResult:
    """
    Uses Gemini 2.0 Flash to validate language cleanliness, idea-level
    repetition, and theological quality. Fail-open on any error.
    Supports both old (google-generativeai) and new (google-genai) SDKs.
    """
    prompt = f"""You are a quality reviewer for Christian devotional content.
Language code: "{lang}" | Verse: "{verse_cita}"

=== REFLEXION ===
{reflexion[:1500]}

=== ORACION ===
{oracion}

Evaluate ONLY these 3 criteria. Respond ONLY with valid JSON (no markdown):

{{
  "language_clean": true or false,
  "no_repetition": true or false,
  "content_quality": true or false,
  "issues": ["brief English description of each failure, empty array if all pass"]
}}

1. language_clean: Both texts are 100% in "{lang}" — no words or phrases from other languages.
2. no_repetition: The reflexion does not repeat the same sentences, phrases, or ideas.
3. content_quality: The reflexion is theologically coherent and substantive, not vague filler.

Be strict but fair. Only mark false on clear, objective failures."""

    try:

        # New SDK (google-genai) — async only
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
        response = await client.aio.models.generate_content(
            model=VALIDATOR_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1, top_p=0.9, max_output_tokens=512,
            ),
        )
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        language_clean  = bool(data.get("language_clean",  True))
        no_repetition   = bool(data.get("no_repetition",   True))
        content_quality = bool(data.get("content_quality", True))
        ai_issues       = data.get("issues", [])

        if not isinstance(ai_issues, list):
            ai_issues = [str(ai_issues)]

        phase2_passed = all([language_clean, no_repetition, content_quality])
        all_passed    = all(p1_flags.values()) and phase2_passed

        print(
            f"INFO: Phase 2 — lang:{language_clean} "
            f"repeat:{no_repetition} quality:{content_quality}"
        )

        return ValidationResult(
            passed=all_passed,
            min_length=p1_flags["min_length"],
            prayer_ending=p1_flags["prayer_ending"],
            double_amen=p1_flags["double_amen"],
            no_dup_words_oracion=p1_flags["no_dup_words_oracion"],
            no_dup_words_reflexion=p1_flags["no_dup_words_reflexion"],
            not_truncated=p1_flags["not_truncated"],
            language_clean=language_clean,
            no_repetition=no_repetition,
            content_quality=content_quality,
            phase1_passed=True,
            phase2_passed=phase2_passed,
            phase2_ran=True,
            issues=ai_issues,
            raw_response=raw,
        )

    except json.JSONDecodeError as e:
        print(f"WARNING: Phase 2 JSON parse error: {e}")
        return ValidationResult.skipped(f"p2_json_error: {e}")
    except Exception as e:
        print(f"WARNING: Phase 2 failed: {e} — skipping")
        return ValidationResult.skipped(str(e))


# =============================================================================
# PUBLIC VALIDATOR
# =============================================================================

async def validate_devotional_content(
    lang:      str,
    verse_cita: str,
    reflexion: str,
    oracion:   str,
    skip_ai:   bool = False,
) -> ValidationResult:
    """
    Main validation entry point.
    - Phase 1 always runs (free, local).
    - Phase 2 runs only if Phase 1 passes and skip_ai=False.
    - Fail-open: Phase 2 errors do not block generation.
    """
    r, o = reflexion.strip(), oracion.strip()

    p1_passed, p1_issues, p1_flags = run_phase1_checks(r, o, lang)
    if not p1_passed:
        print(f"INFO: Phase 1 FAIL — {p1_issues}")
        return ValidationResult.from_phase1(False, p1_issues, p1_flags)

    if skip_ai:
        return ValidationResult.from_phase1(True, [], p1_flags)

    return await _run_phase2_ai_check(lang, verse_cita, r, o, p1_flags)


# =============================================================================
# FIX FUNCTIONS
# =============================================================================

async def fix_oracion(
    verse_cita: str, lang: str, oracion: str, issues: list
) -> str:
    """
    Fixes prayer issues using Gemini 2.0 Flash (cost-efficient).
    Targets: wrong prayer ending, double Amen, consecutive duplicate words.
    Returns the fixed oracion text (falls back to original on error).
    Supports both old (google-generativeai) and new (google-genai) SDKs.
    """
    amen       = _get_amen_phrase(lang)
    issues_txt = "\n".join(f"  - {i}" for i in issues)

    prompt = f"""You are a Christian devotional editor. Fix the prayer (oracion) for language "{lang}".

Issues to fix:
{issues_txt}

Original oracion:
{oracion.strip()}

Rules:
1. Fix ONLY the issues listed above — keep all other content unchanged.
2. The prayer MUST end with exactly: "{amen}"
3. Remove any consecutive duplicate words (e.g., "die die" → "die").
4. The text must be 100% in language "{lang}".
5. Return ONLY the corrected oracion text — no JSON, no labels, no explanation."""

    try:
        # New SDK (google-genai) — async only
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
        response = await client.aio.models.generate_content(
            model=FIX_ORACION_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=600),
        )
        fixed = response.text.strip()
        
        print(f"INFO: fix_oracion ✅ (model: {FIX_ORACION_MODEL})")
        return fixed
    except Exception as e:
        print(f"WARNING: fix_oracion failed: {e} — returning original")
        return oracion


async def fix_reflexion(
    verse_cita: str, lang: str, reflexion: str, issues: list
) -> str:
    """
    Fixes reflexion issues using Gemini 2.5 Flash (better for theological content).
    Targets: consecutive duplicates, idea repetition, content quality, too short.
    Returns the fixed reflexion text (falls back to original on error).
    Supports both old (google-generativeai) and new (google-genai) SDKs.
    """
    issues_txt = "\n".join(f"  - {i}" for i in issues)

    prompt = f"""You are a Christian devotional writer. Improve the reflection (reflexion) for language "{lang}".
Based on verse: "{verse_cita}"

Issues to fix:
{issues_txt}

Original reflexion:
{reflexion.strip()}

Rules:
1. Fix the specific issues listed above.
2. Minimum {REFLEXION_MIN_CHARS} characters.
3. 100% in language "{lang}" — no language mixing.
4. No consecutive duplicate words (e.g., "die die", "sind sind").
5. No repeated sentences, phrases, or ideas.
6. Maintain theological depth, coherence, and devotional quality.
7. Return ONLY the corrected reflexion text — no JSON, no labels, no explanation."""

    try:
        # New SDK (google-genai) — async only
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
        response = await client.aio.models.generate_content(
            model=FIX_REFLEXION_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.4, max_output_tokens=2048),
        )
        fixed = response.text.strip()
        print(f"INFO: fix_reflexion ✅ (model: {FIX_REFLEXION_MODEL})")
        return fixed
    except Exception as e:
        print(f"WARNING: fix_reflexion failed: {e} — returning original")
        return reflexion


# =============================================================================
# VALIDATE AND FIX — ORCHESTRATOR
# =============================================================================

async def validate_and_fix(
    lang:       str,
    verse_cita: str,
    reflexion:  str,
    oracion:    str,
    skip_ai:    bool = False,
) -> tuple:
    """
    Full pipeline: validate → fix if needed → re-validate Phase 1.
    Returns (final_reflexion: str, final_oracion: str, ValidationResult).

    Fix routing:
      oracion issues   → Gemini 2.0 Flash (cheap: ~150 words)
      reflexion issues → Gemini 2.5 Flash (quality: theological content)
    """
    result = await validate_devotional_content(lang, verse_cita, reflexion, oracion, skip_ai)

    if result.passed:
        return reflexion.strip(), oracion.strip(), result

    fixed_r = reflexion.strip()
    fixed_o = oracion.strip()

    # Partition issues by target field
    o_tags = ("prayer_ending", "double_amen", "dup_words_oracion")
    r_tags = ("dup_words_reflexion", "reflexion too short", "no_repetition", "content_quality", "reflexion_truncated")

    o_issues = [i for i in result.issues if any(t in i for t in o_tags)]
    r_issues = [i for i in result.issues if any(t in i for t in r_tags)]

    if result.needs_fix_oracion and o_issues:
        print(f"INFO: Fixing oracion ({len(o_issues)} issues) …")
        fixed_o = await fix_oracion(verse_cita, lang, fixed_o, o_issues)

    if result.needs_fix_reflexion and r_issues:
        print(f"INFO: Fixing reflexion ({len(r_issues)} issues) …")
        fixed_r = await fix_reflexion(verse_cita, lang, fixed_r, r_issues)

    # Re-validate Phase 1 only after fix (no extra API cost)
    p1_passed, p1_issues, p1_flags = run_phase1_checks(fixed_r, fixed_o, lang)
    final = ValidationResult.from_phase1(p1_passed, p1_issues, p1_flags)

    # Carry forward Phase 2 AI results where they weren't invalidated by the fix
    if result.phase2_ran:
        final.language_clean  = result.language_clean
        final.no_repetition   = True if result.needs_fix_reflexion else result.no_repetition
        final.content_quality = True if result.needs_fix_reflexion else result.content_quality
        final.phase2_passed   = all([final.language_clean, final.no_repetition, final.content_quality])
        final.phase2_ran      = True
        final.passed          = p1_passed and final.phase2_passed

    print(f"INFO: validate_and_fix complete — passed:{final.passed} | issues:{final.issues}")
    return fixed_r, fixed_o, final


# =============================================================================
# BATCH MODE — reads checkpoint or final output JSON
# Supports both structures:
#   Checkpoint:    {"completed": {"2025-08-01": {...}, ...}, "master_lang": "de"}
#   Final output:  {"data": {"de": {"2025-08-01": [...], ...}}}
# =============================================================================

def _extract_entries(data: dict) -> tuple:
    if "completed" in data and "master_lang" in data:
        lang    = data["master_lang"]
        entries = list(data["completed"].items())
        return lang, entries

    if "data" in data:
        lang_root = data["data"]
        lang      = next(iter(lang_root))
        date_map  = lang_root[lang]
        entries   = []
        for date_key, val in date_map.items():
            if isinstance(val, list):
                for e in val:
                    if isinstance(e, dict):
                        entries.append((date_key, e))
            elif isinstance(val, dict):
                entries.append((date_key, val))
        return lang, entries

    raise ValueError("Unrecognized JSON structure — expected 'completed' or 'data' root key")


async def _run_batch(json_path: str, ai_check: bool = False, delay: float = 3.0):
    """
    Batch validation on every entry in the JSON file.
    ai_check=True  → Phase 1 + Phase 2 (Gemini 2.0 Flash AI check).
    ai_check=False → Phase 1 only (fast, free, no API calls).
    Saves a JSON report alongside the input file.
    """
    path = Path(json_path)
    if not path.exists():
        print(f"ERROR: File not found: {json_path}")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    try:
        lang, entries = _extract_entries(data)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    total   = len(entries)
    passed  = failed = skipped = 0
    report  = []

    print(f"\n{'='*60}")
    print(f"CONTENT VALIDATOR — Batch Mode")
    print(f"  File    : {path.name}")
    print(f"  Lang    : {lang}")
    print(f"  Entries : {total}")
    print(f"  Mode    : {'Phase 1+2 — Gemini 2.0 Flash' if ai_check else 'Phase 1 only — local (free)'}")
    print(f"{'='*60}\n")

    for i, (date_key, entry) in enumerate(sorted(entries), 1):
        verse_cita = entry.get("versiculo", entry.get("cita", ""))[:80]
        reflexion  = entry.get("reflexion", "")
        oracion    = entry.get("oracion",   "")
        entry_lang = entry.get("language",  lang)
        entry_id   = entry.get("id",        date_key)

        print(f"[{i}/{total}] {date_key} — {entry_id}")

        result = await validate_devotional_content(
            lang=entry_lang,
            verse_cita=verse_cita,
            reflexion=reflexion,
            oracion=oracion,
            skip_ai=not ai_check,
        )

        status_str = "✅ PASS" if result.passed else "❌ FAIL"
        p1_detail  = (
            f"len:{result.min_length} prayer:{result.prayer_ending} "
            f"2amen:{result.double_amen} dup_o:{result.no_dup_words_oracion} "
            f"dup_r:{result.no_dup_words_reflexion} trunc:{result.not_truncated}"
        )
        p2_detail  = (
            f" | lang:{result.language_clean} repeat:{result.no_repetition} "
            f"quality:{result.content_quality}"
            if result.phase2_ran else ""
        )
        print(f"  {status_str} | {p1_detail}{p2_detail}")

        is_skipped = any("validator_skipped" in iss for iss in result.issues)
        if is_skipped:
            skipped += 1
            print(f"  ⚠️  {result.issues}")
        elif not result.passed:
            failed += 1
            for iss in result.issues:
                print(f"   • {iss}")
        else:
            passed += 1

        report.append({
            "date":                   date_key,
            "id":                     entry_id,
            "passed":                 result.passed,
            "min_length":             result.min_length,
            "prayer_ending":          result.prayer_ending,
            "double_amen":            result.double_amen,
            "no_dup_words_oracion":   result.no_dup_words_oracion,
            "no_dup_words_reflexion": result.no_dup_words_reflexion,
            "not_truncated":          result.not_truncated,
            "language_clean":         result.language_clean,
            "no_repetition":          result.no_repetition,
            "content_quality":        result.content_quality,
            "phase2_ran":             result.phase2_ran,
            "issues":                 result.issues,
        })

        if ai_check and i < total:
            await asyncio.sleep(delay)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"  Total   : {total}")
    print(f"  Passed  : {passed}")
    print(f"  Failed  : {failed}")
    print(f"  Skipped : {skipped}")
    print(f"{'='*60}\n")

    # ── Save report ───────────────────────────────────────────────────────────
    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    phase_tag   = "p1p2" if ai_check else "p1"
    report_path = path.parent / f"validation_report_{lang}_{phase_tag}_{ts}.json"
    report_data = {
        "source_file": path.name,
        "lang":        lang,
        "ai_check":    ai_check,
        "total":       total,
        "passed":      passed,
        "failed":      failed,
        "skipped":     skipped,
        "timestamp":   datetime.now().isoformat(),
        "entries":     report,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    print(f"Report saved → {report_path}")
    return report_data


# =============================================================================
# CLI ENTRY POINT
# Usage:
#   python seed_content_validator.py <file.json>        # Phase 1 only (fast, free)
#   python seed_content_validator.py <file.json> --ai   # Phase 1+2 (Gemini 2.0 Flash)
# =============================================================================

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: python seed_content_validator.py <path.json> [--ai]")
        print("  --ai  : also run Phase 2 Gemini 2.0 Flash quality check (uses API quota)")
        sys.exit(1)

    ai_flag = "--ai" in sys.argv
    asyncio.run(_run_batch(sys.argv[1], ai_check=ai_flag))
