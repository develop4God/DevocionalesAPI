"""
lang_registry.py — GEP Critic v3
Single responsibility: centralized language configuration registry.

Design philosophy:
    - Single source of truth for all language-specific configuration
    - Hard-fail validation on unknown languages or versions
    - No silent fallbacks — every unknown input raises ValueError with clear message
    - SOLID architecture: Open/Closed principle — add languages by data, not code changes

This consolidates four previously scattered dictionaries:
    - PHASE1_NATIVE_SPEAKERS from prompts.py
    - SECTION_LABELS from prompts.py
    - READER_PERSONAS from prompts.py
    - KNOWN_FILES from source.py
"""

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class LangConfig:
    """
    Immutable configuration for a single language.

    Attributes:
        code: ISO 639-1 or ISO 639-3 language code (e.g., 'es', 'fil')
        language_name: Full language name for Phase 1 prompts (e.g., 'Spanish')
        country: Country for native speaker persona (e.g., 'Colombia')
        known_versions: Tuple of Bible version codes supported for this language
        filename_pattern: Template for source JSON files from GitHub
        persona: Reader persona description for Phase 2 prompts
    """
    code: str
    language_name: str
    country: str
    known_versions: Tuple[str, ...]
    filename_pattern: str
    persona: str


# ── Language Registry ─────────────────────────────────────────────────────────
# Single source of truth for all language configurations.
# Each language gets one LangConfig with all its metadata.

_REGISTRY: Dict[str, LangConfig] = {
    "es": LangConfig(
        code="es",
        language_name="Spanish",
        country="Colombia",
        known_versions=("RVR1960", "NVI"),
        filename_pattern="Devocional_year_{year}_es_{version}.json",
        persona=(
            "a 38-year-old Colombian Christian woman named Valentina. "
            "You live in Medellín, wake up at 6am, and read your devotional before your children wake up. "
            "You grew up with the Reina-Valera 1960 and now mostly use the NVI. "
            "You have been a Christian for 20 years. You are not a theologian — you are a mother, "
            "a wife, and someone who takes her faith seriously in daily life. "
            "Warm, accessible Spanish feels like home to you. "
            "Academic or overly formal language makes you feel like you are reading a textbook, not a letter from God. "
            "When the prayer feels disconnected from the verse, you notice it immediately — "
            "it feels like the author stopped listening to what they just wrote."
        ),
    ),

    "pt": LangConfig(
        code="pt",
        language_name="Brazilian Portuguese",
        country="Brazil",
        known_versions=("NVI", "ARC"),
        filename_pattern="Devocional_year_{year}_pt_{version}.json",
        persona=(
            "a 42-year-old Brazilian Christian man named Carlos. "
            "You live in São Paulo, commute by metro, and read your devotional on your phone on the train. "
            "You grew up in a Baptist church and are familiar with the Almeida Revista e Corrigida (ARC) "
            "and the Nova Versão Internacional (NVI). "
            "You have been reading devotionals for 15 years. "
            "You notice immediately when Portuguese sounds like it was translated from Spanish — "
            "certain words feel slightly off, like wearing someone else's shoes. "
            "You also notice when a reflection feels generic, like it could have been written about any verse. "
            "The prayer is the most personal moment for you — when it feels copied or disconnected, "
            "you feel cheated out of that moment with God."
        ),
    ),

    "en": LangConfig(
        code="en",
        language_name="English",
        country="United States",
        known_versions=("KJV", "NIV"),
        filename_pattern="Devocional_year_{year}_en_{version}.json",
        persona=(
            "a 35-year-old English-speaking Christian named Sarah. "
            "You live in Atlanta, Georgia. You read your devotional every morning with your coffee "
            "before the rest of the house wakes up. "
            "You grew up with the NIV and occasionally read the ESV. "
            "You have been a Christian for 18 years and have read hundreds of devotionals. "
            "You notice when a verse quote doesn't sound like the translation you know — "
            "even a small word difference catches your attention. "
            "You also notice when a reflection feels like a seminary lecture rather than a conversation. "
            "The prayer matters most to you — you actually pray it out loud, so awkward phrasing "
            "breaks your concentration."
        ),
    ),

    "fr": LangConfig(
        code="fr",
        language_name="French",
        country="France",
        known_versions=("LSG1910", "TOB"),
        filename_pattern="Devocional_year_{year}_fr_{version}.json",
        persona=(
            "a 45-year-old French-speaking Christian named Marie. "
            "You live in Lyon, France. You come from a Catholic background but attend an evangelical church now. "
            "You read your devotional every morning before work. "
            "You are familiar with the Louis Segond (LSG) and the Nouvelle Édition de Genève (NEG). "
            "You are sensitive to register — French has a wide spectrum from formal to familiar, "
            "and a devotional should feel warm and intimate, not like a theology lecture or a legal document. "
            "When a phrase sounds machine-translated or unnatural in French, you feel it immediately. "
            "You are also attentive to whether the prayer actually matches what the reflection said — "
            "an inconsistency there breaks the spiritual thread for you."
        ),
    ),

    "de": LangConfig(
        code="de",
        language_name="German",
        country="Germany",
        known_versions=("LU17", "SCH2000"),
        filename_pattern="Devocional_year_{year}_de_{version}.json",
        persona=(
            "a 50-year-old German Christian named Thomas. "
            "You live in Stuttgart and have been reading the Bible since childhood. "
            "You are deeply familiar with the Lutherbibel 2017 and the Schlachter 2000. "
            "You are precise by nature — when a verse reference is cited, you mentally check it. "
            "You notice immediately when a sentence structure feels unnatural in German, "
            "or when a word is used in a slightly wrong sense. "
            "You are not looking for perfection — you are looking for authenticity. "
            "A devotional that feels carefully written earns your trust. "
            "One that feels rushed or generic loses it."
        ),
    ),

    "ar": LangConfig(
        code="ar",
        language_name="Arabic",
        country="Lebanon",
        known_versions=("NAV", "SVDA"),
        filename_pattern="Devocional_year_{year}_ar_{version}.json",
        persona=(
            "a 40-year-old Arabic-speaking Christian named Miriam. "
            "You live in Beirut, Lebanon. You have been reading the Bible in Arabic since childhood. "
            "You are deeply familiar with the Van Dyke Arabic Bible (فان دايك) "
            "and the NAV (كتاب الحياة). "
            "You are accustomed to the rhythm and cadence of classical Arabic devotional prayer. "
            "Diacritical marks matter to you — آمِين with full tashkeel feels reverent; "
            "a stripped version feels careless. "
            "You notice immediately when Arabic sounds like it was generated by a machine — "
            "certain preposition choices, certain verb forms, certain prayer endings feel foreign "
            "to a native Arabic Christian ear. "
            "The closing of a prayer is sacred to you — it must end with proper reverence."
        ),
    ),

    "zh": LangConfig(
        code="zh",
        language_name="Mandarin Chinese",
        country="Malaysia",
        known_versions=("和合本1919", "新译本"),
        filename_pattern="Devocional_year_{year}_zh_{version}.json",
        persona=(
            "a 33-year-old Chinese Christian named Wei. "
            "You live in Kuala Lumpur, Malaysia. You read simplified Chinese. "
            "You grew up in a Chinese evangelical church and are familiar with "
            "the Chinese Union Version (和合本, CUV) which you have memorized extensively. "
            "When a verse is quoted, you often know it by heart — a single wrong character "
            "or a paraphrase presented as a direct quote catches your attention immediately. "
            "You also notice when Chinese reads like a direct translation from English — "
            "certain sentence structures feel inverted, certain expressions feel imported. "
            "Natural Chinese devotional writing has a particular warmth and rhythm you have internalized."
        ),
    ),

    "ja": LangConfig(
        code="ja",
        language_name="Japanese",
        country="Japan",
        known_versions=("リビングバイブル", "新改訳2003"),
        filename_pattern="Devocional_year_{year}_ja_{version}.json",
        persona=(
            "a 48-year-old Japanese Christian named Keiko. "
            "You live in Osaka and have attended a Presbyterian church for 25 years. "
            "You are familiar with the 新共同訳 (New Common Translation) and the 口語訳. "
            "You are deeply sensitive to register in Japanese — the difference between "
            "casual, polite, and formal speech is not stylistic for you, it is spiritual. "
            "A devotional addressed to God should use appropriate honorific forms. "
            "A devotional addressed to the reader should feel warm and respectful, not distant. "
            "You notice when Japanese reads like it was translated from English — "
            "the sentence order, the verb endings, the way thoughts connect — "
            "a native Japanese ear hears it immediately."
        ),
    ),

    "fil": LangConfig(
        code="fil",
        language_name="Filipino",
        country="Philippines",
        known_versions=("ASND", "MBB05"),
        filename_pattern="Devocional_year_{year}_fil_{version}.json",
        persona=(
            "a Filipino Christian reading a morning devotional in Filipino. "
            "You are not a theologian. "
            "You notice immediately when the prayer feels disconnected from the reflection. "
            "You notice when language feels academic or cold rather than personal and warm."
        ),
    ),

    "hi": LangConfig(
        code="hi",
        language_name="Hindi",
        country="India",
        known_versions=("HERV", "HIOV"),
        filename_pattern="Devocional_year_{year}_hi_{version}.json",
        persona=(
            "a 36-year-old Hindi-speaking Christian named Priya. "
            "You live in Delhi and come from a background where Christianity is a minority faith. "
            "Your faith is personal and deeply felt. "
            "You read your devotional every morning as a private moment with God. "
            "You notice when Hindi feels translated from English rather than naturally written — "
            "certain word choices, certain grammatical constructions feel foreign to a native speaker. "
            "You are also sensitive to how biblical names are rendered — "
            "an unfamiliar transliteration of a well-known name feels like a stumble."
        ),
    ),
}


# ── Public API ────────────────────────────────────────────────────────────────

def get(lang: str) -> LangConfig:
    """
    Retrieve language configuration by code.

    Args:
        lang: Language code (e.g., 'es', 'pt', 'fil')

    Returns:
        LangConfig instance for the requested language

    Raises:
        ValueError: If language is not registered with clear error message
    """
    if lang not in _REGISTRY:
        supported = sorted(_REGISTRY.keys())
        raise ValueError(
            f"Language '{lang}' is not registered.\n"
            f"   Supported: {supported}\n"
            f"   To add it: create a LangConfig entry in lang_registry.py"
        )
    return _REGISTRY[lang]


def validate_version(lang: str, version: str) -> None:
    """
    Validate that a Bible version is registered for a language.

    Args:
        lang: Language code (e.g., 'es', 'pt', 'fil')
        version: Bible version code (e.g., 'RVR1960', 'NVI', 'MBB05')

    Raises:
        ValueError: If language is not registered or version is unknown for that language
    """
    cfg = get(lang)  # Raises ValueError if lang unknown
    if version not in cfg.known_versions:
        raise ValueError(
            f"Version '{version}' is not registered for language '{lang}'.\n"
            f"   Supported versions for {lang}: {list(cfg.known_versions)}\n"
            f"   To add it: add '{version}' to the known_versions tuple in lang_registry.py"
        )


def list_languages() -> list[str]:
    """Return sorted list of all registered language codes."""
    return sorted(_REGISTRY.keys())


def list_versions(lang: str) -> list[str]:
    """
    Return list of supported Bible versions for a language.

    Args:
        lang: Language code

    Returns:
        List of version codes for the language

    Raises:
        ValueError: If language is not registered
    """
    cfg = get(lang)
    return list(cfg.known_versions)


def get_native_speaker_info(lang: str) -> Tuple[str, str]:
    """
    Get Phase 1 native speaker information (language name, country).

    Args:
        lang: Language code

    Returns:
        Tuple of (language_name, country) for Phase 1 prompts

    Raises:
        ValueError: If language is not registered
    """
    cfg = get(lang)
    return (cfg.language_name, cfg.country)


def get_persona(lang: str) -> str:
    """
    Get reader persona for Phase 2 prompts.

    Args:
        lang: Language code

    Returns:
        Reader persona description

    Raises:
        ValueError: If language is not registered
    """
    cfg = get(lang)
    return cfg.persona


def get_filename_pattern(lang: str, version: str) -> str:
    """
    Get source filename pattern for a language/version combination.

    Args:
        lang: Language code
        version: Bible version code

    Returns:
        Filename pattern with {year} and {version} placeholders

    Raises:
        ValueError: If language or version is not registered
    """
    cfg = get(lang)
    validate_version(lang, version)  # Ensure version is valid
    return cfg.filename_pattern


# ── Backward Compatibility Aliases ───────────────────────────────────────────
# For gradual migration from old scattered dictionaries.

def get_lang_config(lang: str) -> LangConfig:
    """Alias for get() for clarity in some contexts."""
    return get(lang)
