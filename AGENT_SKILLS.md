# Agent Skills — Seed Client-Server: Bypassing Gemini, Using Claude

## Context

This document is addressed to the coding agent for the next session.
It describes the seed-driven devotional generation pipeline and explains exactly
**how to replace the Gemini API call with an internal Claude (Anthropic) call**
so the generation runs without any Google API dependency.

---

## 1. Pipeline Overview

```
seed_extractor_fetch.py          API_Server_Seed.py          client_generate_from_seed.py
  (extract + translate)    →   (reflexion + oracion LLM)   →    (merge + build final JSON)
  [seed_ar_NAV_2025.json]        [POST /generate_creative]        [raw_ar_NAV_<ts>.json]
```

**What each component owns:**

| Component | Owns |
|-----------|------|
| `seed_extractor_fetch.py` | Bible verse text, book citations in target language, translated tags |
| `API_Server_Seed.py` | **Gemini call** → `reflexion` (≥900 chars) + `oracion` (≥150 words) |
| `client_generate_from_seed.py` | ID, date, language, version, versiculo field, para_meditar, final JSON structure, checkpoint |

The **only part that calls the LLM** is `API_Server_Seed.py` inside the
`_call_gemini_raw()` async function (lines ~132–193).
Everything else is pure Python logic.

---

## 2. What to Change to Use Claude Instead of Gemini

### 2a. Server-side replacement (`API_Server_Seed.py`)

**Current Gemini call (the function to replace):**

```python
# File: seed_generation/API_Server_Seed.py

from google import genai
from google.genai import types

_client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
GENERATION_MODEL = "gemini-2.5-flash"

async def _call_gemini_raw(verse_cita, lang, topic=None) -> CreativeContent:
    _rate_limiter.acquire()
    # ... builds prompt_parts ...
    response = await _client.aio.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt_parts,
        config=_GENERATION_CONFIG,
    )
    raw = response.text.strip()...
```

**Replace with a Claude call:**

```python
# Install: pip install anthropic
import anthropic

_claude_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
CLAUDE_MODEL   = "claude-opus-4-5"   # or claude-sonnet-4-5 for faster/cheaper

async def _call_gemini_raw(verse_cita, lang, topic=None) -> CreativeContent:
    """Replacement: Claude instead of Gemini — same contract, same return type."""
    prompt = build_devotional_prompt(verse_cita, lang, topic)  # same prompt logic

    # Synchronous Claude call (wrap with asyncio.to_thread for async server)
    import asyncio
    message = await asyncio.to_thread(
        _claude_client.messages.create,
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    # JSON extraction — same logic already in the file
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in Claude response")
    data      = json.loads(match.group())
    reflexion = data.get("reflexion", "").strip()
    oracion   = data.get("oracion",   "").strip()
    if not reflexion or not oracion:
        raise ValueError(f"Claude returned empty reflexion or oracion for '{verse_cita}'")
    return CreativeContent(reflexion=reflexion, oracion=oracion)
```

**Environment variable:**
- Remove: `GOOGLE_API_KEY`
- Add:    `ANTHROPIC_API_KEY`

**Remove unused imports/dependencies:**
- Remove `from google import genai`, `from google.genai import types`
- Remove `from gemini_rate_limiter import GeminiRateLimiter` (Claude has its own rate limits; use `time.sleep` or the Anthropic SDK's built-in retry logic via `max_retries`)
- Add `import anthropic`

---

### 2b. Client-side replacement (standalone, no server)

`client_generate_from_seed.py` currently calls the FastAPI server via HTTP:

```python
API_URL = "http://127.0.0.1:50002/generate_creative"

response = requests.post(API_URL, json=payload, timeout=REQUEST_TIMEOUT)
```

To **bypass both the FastAPI server AND Gemini**, call Claude directly from the client:

```python
# File: seed_generation/client_generate_from_seed.py  (modified)

import anthropic, asyncio, json, re

_claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
CLAUDE_MODEL = "claude-opus-4-5"   # adjust as needed

def generate_reflexion_oracion(verse_cita: str, lang: str, topic: str | None = None) -> dict:
    """Direct Claude call — replaces the HTTP POST to API_Server_Seed.py."""
    prompt = _build_prompt(verse_cita, lang, topic)
    msg = _claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw   = msg.content[0].text.strip()
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    data  = json.loads(match.group())
    return {"reflexion": data["reflexion"].strip(), "oracion": data["oracion"].strip()}

def _build_prompt(verse_cita: str, lang: str, topic: str | None) -> str:
    topic_line = f"\n- Suggested theme: {topic}." if topic else ""
    return (
        f"You are a devoted biblical devotional writer. "
        f"Write a devotional in {lang.upper()} based on the key verse: \"{verse_cita}\".\n\n"
        f"Return ONLY a valid JSON object with these exact keys:\n"
        f"- `reflexion`: Deep reflection (minimum 900 characters, in {lang}). "
        f"Each paragraph develops a distinct aspect of the verse.\n"
        f"- `oracion`: Prayer (minimum 150 words, 100% in {lang}). "
        f"MUST end with 'in the name of Jesus, amen' translated to {lang}. "
        f"Exactly one Amen.\n"
        f"RULES:\n"
        f"- ALL text MUST be 100% in {lang} — no language mixing.\n"
        f"- Do NOT repeat any word consecutively.\n"
        f"- Every sentence must introduce new content.{topic_line}"
    )
```

**In the generation loop**, replace:

```python
# OLD — HTTP call to FastAPI/Gemini server
response = requests.post(API_URL, json=payload, timeout=REQUEST_TIMEOUT)
data = response.json()
reflexion = data["reflexion"]
oracion   = data["oracion"]
```

with:

```python
# NEW — direct Claude call, no server needed
result    = generate_reflexion_oracion(verse_cita, master_lang, topic)
reflexion = result["reflexion"]
oracion   = result["oracion"]
```

---

## 3. Dependency Changes

| | Gemini (current) | Claude (replacement) |
|---|---|---|
| Package | `google-genai` | `anthropic` |
| API key env var | `GOOGLE_API_KEY` | `ANTHROPIC_API_KEY` |
| Rate limiter | `gemini_rate_limiter.py` (custom) | Built-in via `max_retries=` in Anthropic SDK or `time.sleep(1)` between calls |
| Async support | Native async (`aio.models`) | Use `asyncio.to_thread()` for the sync Anthropic SDK, or use `anthropic.AsyncAnthropic` |
| Retries | `tenacity` decorator | `anthropic.Anthropic(max_retries=3)` or keep `tenacity` |

Install: `pip install anthropic`

---

## 4. Prompt Contract (unchanged)

The prompt structure and expected JSON output contract **do not change** when swapping models.
Both Gemini and Claude must return:

```json
{
  "reflexion": "<string, min 900 chars in target language>",
  "oracion":   "<string, min 150 words in target language, ends with 'amen'>"
}
```

The post-generation validators in `seed_content_validator.py` and `validate_script()` in
`API_Server_Seed.py` work on the **output string** — they are model-agnostic and require
**zero changes**.

---

## 5. Script Validation for Arabic (ar)

The `API_Server_Seed.py` `SCRIPT_RANGES` dict currently covers `hi`, `ja`, `zh`.
Arabic (U+0600–U+06FF) is missing. Add it if you need script-level validation for Arabic:

```python
SCRIPT_RANGES = {
    "hi": (0x0900, 0x097F),
    "ja": (0x3040, 0x30FF),
    "zh": (0x4E00, 0x9FFF),
    "ar": (0x0600, 0x06FF),   # ← add this line
}
```

Also add `"ar"` to the `LATIN_LANGS` exclusion set in `seed_extractor_fetch.py`
and `extract_seed.py` where present — Arabic is NOT latin-script:

```python
LATIN_LANGS = {"en", "es", "pt", "fr", "de"}  # do NOT add ar here; it needs script check
```

---

## 6. Files to Modify (summary)

| File | Change |
|------|--------|
| `seed_generation/API_Server_Seed.py` | Replace `_call_gemini_raw()` with Claude call; swap env var; remove google-genai imports |
| `seed_generation/client_generate_from_seed.py` | Add `generate_reflexion_oracion()` using `anthropic`; replace `requests.post()` call |
| `seed_generation/requirements.txt` | Replace `google-genai` with `anthropic` |
| `seed_generation/API_Server_Seed.py` | Add `"ar": (0x0600, 0x06FF)` to `SCRIPT_RANGES` |

---

## 7. Running the Full Arabic Pipeline (end-to-end)

```bash
# Step 1 — Seeds already generated (see seeds/2025/ and seeds/2026/)
# seed_ar_NAV_2025_<ts>.json  →  365 entries  ✅
# seed_ar_NAV_2026_<ts>.json  →  365 entries  ✅

# Step 2 — Start the generation server (Gemini or Claude version)
uvicorn API_Server_Seed:app --host 127.0.0.1 --port 50002

# Step 3 — Run the client against the seed
python client_generate_from_seed.py
# → select the ar NAV seed file
# → select output folder
# → generates raw_ar_NAV_<ts>.json  (reflexion + oracion for each date)
```

To run fully offline / without a server (Claude-direct mode):

```bash
export ANTHROPIC_API_KEY=sk-ant-...

# No uvicorn server needed — calls Claude directly
python client_generate_from_seed_claude.py \
       --seed  seeds/2025/seed_ar_NAV_2025_<ts>.json \
       --lang  ar \
       --version NAV \
       --output 2025
```

---

## 8. Known Data Fixes Applied to `seed_extractor_fetch.py`

Two bugs were found and fixed during Arabic seed generation:

### 8a. `bible_books.json` SOT — "Lamentations\n" key has trailing newline

The remote `bible_books.json` SOT file has `"Lamentations\n"` (with a literal newline in the
JSON key) which causes the book lookup to fail for "Lamentations 3:22-23" references.

**Fix applied in `load_books_sot()`:**
```python
# Strip whitespace from keys — the SOT JSON has some keys like "Lamentations\n"
_books_sot_cache = {
    name.strip(): entry["book_number"]
    for name, entry in data["books"].items()
}
```

### 8b. NAV Arabic Bible DB — Revelation 22 only has 17 verses (missing 18–21)

The NAV (`الترجمة العربية الجديدة`) SQLite database is missing Revelation 22:18–21.
The fallback version SVDA (`ترجمة سميث وفاندايك`) has all 21 verses.

**Fix applied in `run()`:** Added automatic fallback DB support.
When `lang_entry` is passed and a `fallback_version` is configured in the versions index,
a second DB is opened. If the primary DB misses a verse, the fallback DB is tried automatically.

For Arabic: `primary=NAV → fallback=SVDA`.

The affected date `2026-05-17` (Revelation 22:20) now resolves via SVDA and is logged with
`[fallback: SVDA]` in the console output.

**To regenerate seeds with these fixes:**
```bash
python seed_extractor_fetch.py --year 2025 --lang ar --version NAV --output seeds/2025
python seed_extractor_fetch.py --year 2026 --lang ar --version NAV --output seeds/2026
# Both now produce 365 entries, 0 errors, 0 violations
```

