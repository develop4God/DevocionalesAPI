# pipeline_shared.py
# Shared utilities for batch pipeline scripts
import json
import re
import unicodedata
from typing import Optional

# --- Constants ---
LITURGICAL_WHITELIST = frozenset({
    "heilig", "holy", "kadosh", "halleluja", "hosanna",
    "amen", "amén", "āmen", "aleluya", "panginoon",
})

# --- JSON Repair ---
def _extract_first_balanced_object(text: str) -> Optional[str]:
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i, c in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None

def repair_json(raw_text: str) -> Optional[dict]:
    text = re.sub(r'```(?:json)?\s*', '', raw_text)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE).strip()
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
        fixed = re.sub(r',(\s*[}\]])', r'\1', candidate)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        fixed2 = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', fixed)
        try:
            return json.loads(fixed2)
        except json.JSONDecodeError:
            pass
        fixed3 = re.sub(r',([\s]*[}\]])', r'\1', candidate)
        fixed3 = re.sub(r'(?<=: ")([^"]*?)\n([^"]*?)(?=")', r'\1\\n\2', fixed3)
        try:
            return json.loads(fixed3)
        except json.JSONDecodeError:
            pass
    reflexion_m = re.search(r'"reflexion"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    oracion_m = re.search(r'"oracion"\s*:\s*"((?:[^"\\]|\\.)*)"', text, re.DOTALL)
    if reflexion_m and oracion_m:
        try:
            return {
                "reflexion": reflexion_m.group(1).encode('raw_unicode_escape').decode('unicode_escape'),
                "oracion":   oracion_m.group(1).encode('raw_unicode_escape').decode('unicode_escape'),
            }
        except Exception:
            return {
                "reflexion": reflexion_m.group(1),
                "oracion":   oracion_m.group(1),
            }
    return None

# --- Prompt Builder ---
def build_prompt(verse_cita: str, lang: str, topic: str | None = None) -> str:
    parts = "\n\n".join([
        f"You are a warm, pastoral biblical devotional writer. "
        f"Write a personal, reader-focused devotional in {lang.upper()} "
        f"based on the key verse: \"{verse_cita}\". "
        f"Your tone is intimate and encouraging — speak directly to the reader as 'you'. "
        f"Every sentence must serve the reader's spiritual growth, not demonstrate theological knowledge.",

        "Return ONLY a valid JSON object with these exact keys:",

        f"- `reflexion`: A personal, warm reflection on the verse "
        f"(minimum 900 characters, maximum 1100 characters, approximately 300 words, in {lang}). "
        f"Structure: paragraph 1 — what the verse reveals about God; "
        f"paragraph 2 — what this means for the reader's daily life; "
        f"paragraph 3 — a practical encouragement or challenge for today. "
        f"NEVER include academic observations about theologians, scholars, churches, or history. "
        f"NEVER add filler sentences unrelated to the verse. "
        f"Every sentence must speak to the reader or illuminate the verse directly. "
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks. "
        f"Do NOT repeat the same sentence, phrase, or idea in different words.",

        f"- `oracion`: A personal prayer in first-person plural ('we'/'nos') "
        f"on the devotional theme (minimum 150 words, maximum 200 words, 100% in {lang}). "
        f"The prayer must flow naturally from the reflexion theme. "
        f"MUST end with 'in the name of Jesus, amen' correctly translated to {lang}. "
        f"End with exactly one Amen — never write Amen twice. "
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks. "
        f"Do NOT repeat the same sentence, phrase, or idea in different words.",

        f"RULES:\n"
        f"- ALL text MUST be 100% in {lang} — no language mixing.\n"
        f"- Do NOT include transliterations, romanizations, or text in parentheses.\n"
        f"- Do NOT write about what scholars, theologians, churches, or young people do — "
        f"write TO the reader about what God says and how it applies TODAY.\n"
        f"- Every sentence must introduce new content or a new perspective."
        + (f"\n- Suggested theme: {topic}." if topic else ""),
    ])
    return parts

# --- Prayer Endings ---
def _load_prayer_endings() -> dict:
    import os
    path = os.path.join(os.path.dirname(__file__), "prayer_endings.json")
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
    clean   = oracion.strip().rstrip(".!,;।").strip()
    words   = clean.split()
    for ending in endings:
        n    = len(ending.split())
        tail = " ".join(words[-n:]) if len(words) >= n else clean
        if _normalize_word(ending) == _normalize_word(tail):
            return True
    return False
