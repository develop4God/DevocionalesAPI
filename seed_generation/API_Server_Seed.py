"""
API_Server_Seed.py
─────────────────────
Seed-driven devotional generation server — WITH content validator integration.

New in v2 vs v1:
  - Uses new google-genai SDK throughout (no google.generativeai)
  - Integrates seed_content_validator.validate_and_fix() after generation:
      * Phase 1 (local): length, prayer ending, double amen, dup words → auto-fix
      * Script validation: same as v1
  - On Phase 1 failure after auto-fix → retries full Gemini generation once more
  - MAX_CONTENT_RETRIES controls how many full regeneration attempts are made

Launch:
  uvicorn API_Server_Seed:app --host 0.0.0.0 --port 50002 --reload
"""

import json
import os
import re
import traceback
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import RetryError, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from gemini_rate_limiter import GeminiRateLimiter, GeminiRateLimiterError
from seed_content_validator import validate_and_fix, run_phase1_checks

# =============================================================================
# STARTUP
# =============================================================================
load_dotenv()

try:
    _GEMINI_API_KEY = os.environ["GOOGLE_API_KEY"]
except KeyError:
    raise ValueError("GOOGLE_API_KEY not set. Add it to your .env file.")

# Initialize google-genai client
_client = genai.Client(api_key=_GEMINI_API_KEY)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# GEMINI CONFIG (new google-genai SDK)
# =============================================================================
_GENERATION_CONFIG = types.GenerateContentConfig(
    temperature=0.7,
    top_p=0.95,
    top_k=64,
    max_output_tokens=8192,
)

# Generation model — best quality for devotional content
GENERATION_MODEL = "gemini-2.5-flash"

# How many full Gemini regeneration attempts before giving up
MAX_CONTENT_RETRIES = 2

# =============================================================================
# RATE LIMITER
# =============================================================================
_rate_limiter = GeminiRateLimiter(model=GENERATION_MODEL)

# =============================================================================
# SCRIPT VALIDATOR
# Language-aware: checks that Gemini responded in the correct script.
# Latin-script languages (de, es, en, fr, pt) are always valid — no check needed.
# =============================================================================
SCRIPT_RANGES = {
    "hi": (0x0900, 0x097F),   # Devanagari
    "ja": (0x3040, 0x30FF),   # Hiragana + Katakana
    "zh": (0x4E00, 0x9FFF),   # CJK Unified Ideographs
}

SCRIPT_THRESHOLD = 0.6


def validate_script(text: str, lang: str) -> tuple[bool, float]:
    if lang not in SCRIPT_RANGES:
        return True, 1.0
    lo, hi = SCRIPT_RANGES[lang]
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return False, 0.0
    ratio = sum(1 for c in alpha_chars if lo <= ord(c) <= hi) / len(alpha_chars)
    return ratio >= SCRIPT_THRESHOLD, ratio


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class SeedGenerateRequest(BaseModel):
    date:            str
    master_lang:     str
    master_version:  str
    versiculo_cita:  str
    topic:           Optional[str] = None


class CreativeContent(BaseModel):
    reflexion: str
    oracion:   str


class SeedGenerateResponse(BaseModel):
    status:       str
    date:         str
    lang:         str
    reflexion:    str
    oracion:      str
    val_issues:   list = []   # Phase 1 issues found + fixed (informational)


# =============================================================================
# GEMINI CALLER (new SDK)
# =============================================================================

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=10),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(Exception),
)
async def _call_gemini_raw(
    verse_cita: str, lang: str, topic: Optional[str] = None
) -> CreativeContent:
    """Raw Gemini generation — returns reflexion + oracion. No validation."""
    _rate_limiter.acquire()

    prompt_parts = "\n\n".join([
        f"You are a devoted biblical devotional writer. "
        f"Write a devotional in {lang.upper()} based on the key verse: \"{verse_cita}\".",

        "Return ONLY a valid JSON object with these exact keys:",

        f"- `reflexion`: Deep contextualized reflection on the verse "
        f"(minimum 900 characters, approximately 300 words, in {lang}). "
        f"Each paragraph must develop a distinct aspect of the verse.",
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks — "
        f"Never write patterns (example: 'word, word', 'word. Word', 'word; word').",
        f"- Do NOT repeat the same sentence, phrase, or idea in different words.\n"

        f"- `oracion`: Prayer on the devotional theme (minimum 150 words, 100% in {lang}). "
        f"MUST end with 'in the name of Jesus, amen' correctly translated to {lang}. "
        f"End with exactly one Amen — never write Amen twice.",
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks — "
        f"Never write patterns (example: 'word, word', 'word. Word', 'word; word').",
        f"- Do NOT repeat the same sentence, phrase, or idea in different words.\n"

        f"RULES:\n"
        f"- ALL text MUST be 100% in {lang} — no language mixing.\n"
        f"- Do NOT include transliterations, romanizations, or text in parentheses.\n"
        f"- Do NOT repeat any word consecutively.\n"
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks — "
        f"Never write patterns (example: 'word, word', 'word. Word', 'word; word').",
        f"- Do NOT repeat the same sentence, phrase, or idea in different words.\n"
        f"- Every sentence must introduce new content or a new perspective."
        + (f"\n- Suggested theme: {topic}." if topic else ""),
    ])

    print(f"DEBUG: Gemini call — verse: {verse_cita}, lang: {lang}")
    response = await _client.aio.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt_parts,
        config=_GENERATION_CONFIG,
    )
    raw  = response.text.strip().replace("```json", "").replace("```", "").strip()
    # Extract JSON object robustly — handles preamble from thinking models
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in Gemini response")
    data = json.loads(match.group())

    reflexion = data.get("reflexion", "").strip()
    oracion   = data.get("oracion",   "").strip()

    if not reflexion or not oracion:
        raise ValueError(f"Gemini returned empty reflexion or oracion for '{verse_cita}'")

    return CreativeContent(reflexion=reflexion, oracion=oracion)


async def _call_gemini_fix_script(
    verse_cita: str, lang: str,
    bad_reflexion: str, bad_oracion: str,
) -> CreativeContent:
    """Script-fix retry — rewrites content into the correct language script."""
    _rate_limiter.acquire()

    prompt = (
        f"The following devotional text is NOT written in {lang.upper()} script. "
        f"Rewrite it completely in {lang.upper()}. Keep the same devotional meaning.\n\n"
        f"Verse: \"{verse_cita}\"\n"
        f"Wrong reflexion: \"{bad_reflexion[:300]}\"\n"
        f"Wrong oracion:   \"{bad_oracion[:300]}\"\n\n"
        f"Return ONLY: {{\"reflexion\": \"...\", \"oracion\": \"...\"}}\n"
        f"oracion MUST end with the correct {lang.upper()} translation of "
        f"'in the name of Jesus, amen'.\n"
        f"ALL text MUST be 100% in {lang} — no language mixing.\n"
        f"Do NOT include transliterations, romanizations, or text in parentheses."
    )

    print(f"WARNING: Script fix retry — verse: {verse_cita}, lang: {lang}")
    response = await _client.aio.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
        config=_GENERATION_CONFIG,
    )
    raw  = response.text.strip().replace("```json", "").replace("```", "").strip()
    # Extract JSON object robustly — handles preamble from thinking models
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in Gemini response")
    data = json.loads(match.group())

    return CreativeContent(
        reflexion=data.get("reflexion", bad_reflexion).strip(),
        oracion=data.get("oracion",   bad_oracion).strip(),
    )


# =============================================================================
# GENERATE + VALIDATE + FIX PIPELINE
# =============================================================================

async def _generate_validated(
    verse_cita: str, lang: str, topic: Optional[str]
) -> tuple[str, str, list]:
    """
    Full pipeline:
      1. Generate with Gemini (up to MAX_CONTENT_RETRIES attempts)
      2. Script validation → script-fix retry if needed
      3. validate_and_fix() → Phase 1 local checks + auto-fix (skip AI to save quota)

    Returns (reflexion, oracion, val_issues_list).
    Raises HTTPException on unrecoverable failure.
    """
    last_issues = []

    for attempt in range(1, MAX_CONTENT_RETRIES + 1):
        print(f"INFO: Generation attempt {attempt}/{MAX_CONTENT_RETRIES}")

        # ── Step 1: Gemini generation ─────────────────────────────────────────
        content = await _call_gemini_raw(verse_cita, lang, topic)

        # ── Step 2: Script validation ──────────────────────────────────────────
        r_valid, r_ratio = validate_script(content.reflexion, lang)
        o_valid, o_ratio = validate_script(content.oracion,   lang)
        print(f"INFO: Script — reflexion:{r_ratio:.0%} oracion:{o_ratio:.0%}")

        if not r_valid or not o_valid:
            print(f"WARNING: Script check failed — running script-fix retry")
            try:
                content = await _call_gemini_fix_script(
                    verse_cita, lang, content.reflexion, content.oracion
                )
                r_valid, r_ratio = validate_script(content.reflexion, lang)
                o_valid, o_ratio = validate_script(content.oracion,   lang)
                print(f"INFO: After script fix — reflexion:{r_ratio:.0%} oracion:{o_ratio:.0%}")
                if not r_valid or not o_valid:
                    print(f"WARNING: Script fix still failed — will retry generation")
                    continue
            except Exception as e:
                print(f"ERROR: Script fix failed: {e} — will retry generation")
                continue

        # ── Step 3: Content validator + auto-fix (Phase 1 only, no extra AI) ──
        fixed_r, fixed_o, val_result = await validate_and_fix(
            lang=lang,
            verse_cita=verse_cita,
            reflexion=content.reflexion,
            oracion=content.oracion,
            skip_ai=True,   # skip Phase 2 AI check to preserve quota
        )

        last_issues = val_result.issues

        if val_result.passed:
            print(f"INFO: ✅ Content valid — attempt {attempt}")
            return fixed_r, fixed_o, last_issues

        print(
            f"WARNING: Phase 1 still failed after auto-fix "
            f"(attempt {attempt}) — issues: {val_result.issues}"
        )
        # Loop → regenerate from scratch

    # All attempts exhausted — return best effort with issues logged
    print(f"WARNING: All {MAX_CONTENT_RETRIES} attempts failed — returning last result")
    return fixed_r, fixed_o, last_issues


# =============================================================================
# FASTAPI APP
# =============================================================================
app = FastAPI(
    title="Devotional Seed Generator API v2",
    description="Seed-driven devotional server with content validator integration.",
    version="2.0.0",
)


@app.post("/generate_creative", response_model=SeedGenerateResponse)
async def generate_creative(request: SeedGenerateRequest):
    """
    Accepts pre-built verse data from generate_from_seed.py.
    Calls Gemini for reflexion + oracion, validates + auto-fixes content,
    retries generation if Phase 1 still fails after fix.
    Returns {reflexion, oracion, val_issues} — Python builds the rest.
    """
    print(f"\n--- {request.date} | {request.master_lang} | {request.versiculo_cita} ---")

    try:
        reflexion, oracion, val_issues = await _generate_validated(
            verse_cita=request.versiculo_cita,
            lang=request.master_lang,
            topic=request.topic,
        )

        if val_issues:
            print(f"INFO: Returned with residual issues (non-blocking): {val_issues}")

        return SeedGenerateResponse(
            status="success",
            date=request.date,
            lang=request.master_lang,
            reflexion=reflexion,
            oracion=oracion,
            val_issues=val_issues,
        )

    except HTTPException:
        raise
    except GeminiRateLimiterError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"QUOTA_ERROR: {str(e)}"
        )
    except RetryError as e:
        last = e.last_attempt.exception() if e.last_attempt else e
        print(f"ERROR: RetryError for {request.date}: {last}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Gemini retry exhausted: {str(last)}"
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Generation error: {str(e)}"
        )
