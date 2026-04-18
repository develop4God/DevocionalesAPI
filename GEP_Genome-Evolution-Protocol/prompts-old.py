"""
prompts.py — GEP Critic v3
Single responsibility: build LLM prompts for the simulated reader.
"""

from models import DevotionalEntry, Genome, PauseCategory

# ── Language persona labels ────────────────────────────────────────────────────

READER_PERSONAS = {
    "pt": "a Brazilian Christian who reads one devotional every morning on their phone",
    "es": "a Latin American Christian who reads one devotional every morning on their phone",
    "en": "an English-speaking Christian who reads one devotional every morning on their phone",
    "fr": "a French-speaking Christian who reads one devotional every morning on their phone",
    "de": "a German-speaking Christian who reads one devotional every morning on their phone",
    "hi": "a Hindi-speaking Christian who reads one devotional every morning on their phone",
    "ar": "an Arabic-speaking Christian who reads one devotional every morning on their phone",
    "ja": "a Japanese Christian who reads one devotional every morning on their phone",
    "zh": "a Chinese Christian who reads one devotional every morning on their phone",
    "tl": "a Filipino Christian who reads one devotional every morning on their phone",
}

# ── Pause categories for the reader ───────────────────────────────────────────

CATEGORY_HINTS = "\n".join([
    f"  - {c.value}" for c in PauseCategory
])

# ── Genome few-shot block ──────────────────────────────────────────────────────

def build_genome_block(genome: Genome | None) -> str:
    if not genome:
        return ""
    fragments = genome.high_confidence_fragments(threshold=0.6)
    if not fragments:
        return ""
    lines = ["### Patterns that have caused reader pauses before:\n"]
    for f in fragments[:5]:  # max 5 to keep prompt lean
        lines.append(
            f"- [{f.category.value}] \"{f.example_quote}\" "
            f"— {f.pattern} (seen {len(f.evidence_dates)}x)"
        )
    return "\n".join(lines) + "\n"


# ── Main prompt builders ───────────────────────────────────────────────────────

def build_system_prompt(lang: str, version: str, genome: Genome | None = None) -> str:
    persona = READER_PERSONAS.get(lang, "a Christian who reads one devotional every morning")
    genome_block = build_genome_block(genome)

    return f"""\
You are {persona}.
You just finished reading today's devotional from the {version} Bible.

Your only job: react honestly as a reader.

### How to react:
- If everything felt natural, clear, and spiritually coherent → respond with verdict OK.
- If ANYTHING made you pause — a typo, a name that looked wrong, a verse that didn't match \
what was quoted, a prayer about something different from the reflection, a phrase that felt \
copied from a textbook — respond with verdict PAUSE.

### If you say PAUSE:
- Quote the EXACT phrase that made you pause (copy it word for word).
- Say in one sentence why it felt wrong as a reader.
- Pick the best category from this list:
{CATEGORY_HINTS}

### Critical rules:
- You are NOT a theologian or editor. You are a reader.
- Do NOT invent problems. If nothing felt off, say OK.
- Do NOT comment on style preferences. Only flag things that would make a real reader \
distrust or be confused by the content.
- If the verse reference and quoted text match perfectly → that is fine, do not flag it.

{genome_block}
Return ONLY valid JSON. No markdown. No preamble.

Schema:
{{
  "verdict": "OK" | "PAUSE",
  "reaction": "One or two sentences: what you felt as a reader.",
  "quoted_pause": "The exact phrase that made you pause, or null if OK.",
  "category": "one of the category values above, or null if OK.",
  "confidence": 0.0 to 1.0
}}"""


def build_user_prompt(entry: DevotionalEntry) -> str:
    meditar_block = ""
    if entry.para_meditar:
        lines = ["\n--- PARA MEDITAR ---"]
        for ref in entry.para_meditar:
            lines.append(f"{ref.get('cita','')}: {ref.get('texto','')}")
        meditar_block = "\n".join(lines)

    return f"""\
Date: {entry.date}

--- VERSÍCULO ---
{entry.versiculo}

--- REFLEXÃO ---
{entry.reflexion}
{meditar_block}

--- ORAÇÃO ---
{entry.oracion}

How did you feel reading this? Return only the JSON verdict."""
