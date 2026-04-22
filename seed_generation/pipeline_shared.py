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
        f"You are a warm, theologian, pastoral biblical devotional writer. "
        f"Write a personal, reader-focused devotional in {lang.upper()} "
        f"based on the key verse: \"{verse_cita}\". "
        f"Your tone is warm and pastoral — use inclusive, communal language (we/us) whenever natural in {lang}, walking alongside the reader rather than addressing them from above. "
        f"Every sentence must serve the reader's spiritual growth, and explain the meaning without complex theological terms or jargon.",

        "Return ONLY a valid JSON object with these exact keys:",

        f"- `reflexion`: A warm, personal reflection (content MUST BE between 900 and 1100 characters, in {lang}). "
        f"Write exactly 4 paragraphs — no more, no less:\n"
        f"  Paragraph 1: Begin directly with what the verse reveals about God's character and heart toward the reader — do not quote or repeat the verse text itself.\n"
        f"  Paragraph 2: Explain what the central image or promise of the verse means practically — "
        f"what does 'staying connected', 'believing', 'trusting' actually look like on an ordinary day?\n"
        f"  Paragraph 3: Speak honestly about the gap — where do we fall short of this truth? "
        f"Name a real, recognizable struggle without being vague.\n"
        f"  Paragraph 4: Leave the reader with one specific truth or promise from the verse they can hold onto today — not a physical activity or object. The truth must arise directly from this verse, not a generic spiritual habit.\n"
        f"IMPORTANT: Before returning, count your paragraphs. If you have more or fewer than 4, rewrite until exactly 4 remain — no exceptions.\n"
        f"CHARACTER LIMIT: Count the characters in reflexion before returning. If it exceeds 1100 characters, rewrite each paragraph to be more concise — preserve the complete idea of each paragraph, never cut mid-thought.\n"
        f"NEVER include academic observations about theologians, scholars, churches, or history. "
        f"NEVER add filler sentences unrelated to the verse. "
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks. "
        f"Do NOT repeat the same sentence, phrase, or idea in different words.",

        f"- `oracion`: A prayer in first-person plural ('we'/'nos') "
        f"(minimum 120 words, maximum 160 words, 100% in {lang}). "
        f"Write exactly 2 paragraphs:\n"
        f"  Paragraph 1 — Confession: begin with 1-2 sentences of gratitude for what the verse reveals about God, "
        f"then honestly acknowledge before God where we fall short of the verse's truth (2-3 sentences). "
        f"Do not be vague — name the specific failure the reflexion identified.\n"
        f"  Paragraph 2 — Petition: ask God for what we specifically need to live this verse today (4-5 sentences). "
        f"Each sentence must express a distinct request — do not string multiple requests into one sentence.\n"
        f"The prayer must flow directly from the reflexion theme. "
        f"MUST end with 'in the name of Jesus, amen' correctly translated to {lang}. "
        f"End with exactly one Amen — never write Amen twice. "
        f"Do NOT repeat any word consecutively, even when separated by punctuation marks. "
        f"Do NOT repeat the same sentence, phrase, or idea in different words.",

        f"RULES:\n"
        f"- ALL text MUST be 100% in {lang} — no language mixing.\n"
        f"- Do NOT include transliterations, romanizations, or text in parentheses.\n"
        f"- Do NOT write about what scholars, theologians, churches, or young people do — "
        f"write TO the reader about what God says and how it applies TODAY.\n"
        f"- Every sentence must introduce new content or a new perspective.\n"
        f"- Do NOT pad sentences with adverbs. Let verbs and nouns carry the meaning.\n"
        f"- The oracion must express ideas in fresh language — never copy sentences verbatim from the reflexion.\n"
        f"- The confession in the prayer must name the same struggle identified in reflexion paragraph 3."
        f"Use strong verbs and specific nouns instead of adverbs — never modify a verb or adjective with an adverb"
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
