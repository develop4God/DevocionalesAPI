# pipeline_shared.py
# Shared utilities for batch pipeline scripts
import json
import re
import unicodedata
from typing import Optional

# --- Constants ---
LITURGICAL_WHITELIST = frozenset(
    {
        "heilig",
        "holy",
        "kadosh",
        "halleluja",
        "hosanna",
        "amen",
        "amén",
        "āmen",
        "aleluya",
        "panginoon",
    }
)


# --- JSON Repair ---
def _extract_first_balanced_object(text: str) -> Optional[str]:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, c in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if c == "\\" and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def repair_json(raw_text: str) -> Optional[dict]:
    # Strip chain-of-thought <think>...</think> blocks (Qwen3, DeepSeek-R1, etc.)
    text = re.sub(r"<think>[\s\S]*?</think>", "", raw_text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    candidate = _extract_first_balanced_object(text)
    if candidate:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        fixed = re.sub(r",(\s*[}\]])", r"\1", candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        fixed2 = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", fixed)
        try:
            return json.loads(fixed2)
        except json.JSONDecodeError:
            pass
        fixed3 = re.sub(r",([\s]*[}\]])", r"\1", candidate)
        fixed3 = re.sub(r'(?<=: ")([^"]*?)\n([^"]*?)(?=")', r"\1\\n\2", fixed3)
        try:
            return json.loads(fixed3)
        except json.JSONDecodeError:
            pass
    reflexion_m = re.search(r'"reflexion"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    oracion_m = re.search(r'"oracion"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if reflexion_m and oracion_m:
        try:
            return {
                "reflexion": reflexion_m.group(1)
                .encode("raw_unicode_escape")
                .decode("unicode_escape"),
                "oracion": oracion_m.group(1)
                .encode("raw_unicode_escape")
                .decode("unicode_escape"),
            }
        except Exception:
            return {
                "reflexion": reflexion_m.group(1),
                "oracion": oracion_m.group(1),
            }
    return None


def _lang_label(lang: str) -> str:
    return "Filipino (ISO 639-2 code: fil)" if lang == "fil" else lang.upper()


# --- Prompt Builder ---
def build_prompt(
    verse_cita: str, lang: str, topic: str | None = None, verse_texto: str | None = None
) -> str:
    """Single-message prompt (persona + instructions + verse, all inlined).

    Kept for callers that need one self-contained string (e.g. --dry-run's
    prompt preview). Fireworks batch submission uses build_system_prompt +
    build_user_prompt instead — see their docstrings for why.
    """
    lang_label = _lang_label(lang)
    return "\n\n".join(
        [_system_body(lang_label), _user_body(verse_cita, verse_texto, topic)]
    )


def build_system_prompt(lang: str) -> str:
    """
    The persona + output-contract instructions, identical for every day in a
    language's batch (no verse, no topic — those vary per day).

    Pass this as the batch job's own system_prompt (Fireworks' documented
    "System Prompt Optimization": a job-level system message injected into
    every row that doesn't carry its own — docs.fireworks.ai/guides/
    batch-inference, see FIREWORKS_BATCH_API.md) so the shared prefix is
    byte-identical across all 366 requests and prompt-caching applies,
    instead of re-sending the full instruction block on every line.
    """
    return _system_body(_lang_label(lang))


def build_user_prompt(
    verse_cita: str, verse_texto: str | None = None, topic: str | None = None
) -> str:
    """The per-day user message: only the verse (+ optional topic) — the one
    part of each request that actually differs from the cached system prefix."""
    return _user_body(verse_cita, verse_texto, topic)


def _system_body(lang_label: str) -> str:
    return "\n\n".join(
        [
            "You are a devoted biblical devotional writer. "
            f"Write a christian devotional in {lang_label} based on the key verse given below.",
            "Write in a simple, warm, pastoral tone. "
            "State ideas affirmatively — express what is true and present, "
            "not what is absent, false, or being denied. "
            "Avoid 'not X, but Y' style contrast constructions, "
            "Return ONLY a valid JSON object with these exact keys:",
            f"- `reflexion`: contextualized reflection on the verse "
            f"(minimum 900 characters, in {lang_label}).",
            f"- `oracion`: Prayer on the devotional theme (minimum 150 words, 100% in {lang_label}), "
            f"MUST end with the standard closing phrase 'in the name of Jesus, amen', "
            f"written entirely in {lang_label} (do not mix in any English words). "
            f"Write this closing phrase exactly ONCE, as the very last words of the prayer.",
            f"RULES:\n"
            f"- ALL text MUST be 100% in {lang_label} — no language mixing.\n"
            f"- Do NOT include transliterations, romanizations, or text in parentheses.",
        ]
    )


def _user_body(
    verse_cita: str, verse_texto: str | None, topic: str | None
) -> str:
    topic_line = f"\n- Suggested theme: {topic}." if topic else ""
    verse_block = f'"{verse_cita}"' if not verse_texto else f'"{verse_cita}"\n"{verse_texto}"'
    return f"Key verse: {verse_block}{topic_line}"


# --- Prayer Endings ---
def _load_prayer_endings() -> dict:
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "shared", "prayer_endings.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


def _normalize_word(word: str) -> str:
    return unicodedata.normalize("NFD", word).encode("ascii", "ignore").decode().lower()


def _check_prayer_ending(oracion: str, lang: str) -> bool:
    endings = _load_prayer_endings().get(lang, ["Amen"])
    clean = oracion.strip().rstrip(".!,;।").strip()
    words = clean.split()
    for ending in endings:
        n = len(ending.split())
        tail = " ".join(words[-n:]) if len(words) >= n else clean
        if _normalize_word(ending) == _normalize_word(tail):
            return True
    return False
