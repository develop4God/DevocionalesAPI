"""
bible_text_normalizer.py
─────────────────────────
Python port of BibleTextNormalizer (devocional_nuevo/bible_reader_core,
lib/src/bible_text_normalizer.dart) — kept behavior-identical for the tags
that normalizer already handles (<S> Strong's numbers, <m> morphology
codes, &quot; entities, [bracketed] refs, circled-number footnote markers,
bullet markers).

Adds <n>...</n> and <f>...</f> unit-stripping on top: footnote/translator's-
note tags used by some MyBible-format DBs (e.g. KJV_en.SQLite3's Strong's
edition) that the Dart original wasn't written against, so its generic
tag-stripper alone would leave their wrapped text behind as stray content.

clean_resolved() handles text that has already gone through
verse_resolver.py's fetch_text(): its <S>/<m>/<n>/<f> tag *markers* are
already stripped, but for a Strong's-numbered DB the digits they wrapped
are left glued to the preceding word (e.g. "helper998") since that
resolver has no Strong's-specific handling. Strip those bare trailing
digit runs instead of the (by-then-absent) <S> tags.
"""

import re

_STRONG_TAG = re.compile(r"<S>\d+</S>")
_MORPH_TAG = re.compile(r"<m>[^<]*</m>")
_NOTE_TAG = re.compile(r"<n>.*?</n>")
_FOOTNOTE_TAG = re.compile(r"<f>.*?</f>")
_ANY_TAG = re.compile(r"<[^>]+>")
_BRACKETED = re.compile(r"\[[^\]]+\]")
_CIRCLED_MARKERS = re.compile(r"[①-⓿]")
_BULLET = re.compile(r"\s*•\s*")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?])")
_WHITESPACE = re.compile(r"\s+")
_BARE_STRONG_NUM = re.compile(r"(?<=[^\s\d])\d+")
_STANDALONE_NUM = re.compile(r"(?:^|\s+)\d+\b")


def clean(text: str | None) -> str:
    """Clean Bible verse text pulled from a MyBible-format SQLite DB."""
    if not text:
        return ""
    text = _STRONG_TAG.sub("", text)
    text = _MORPH_TAG.sub("", text)
    text = _NOTE_TAG.sub("", text)
    text = _FOOTNOTE_TAG.sub("", text)
    text = text.replace("&quot;", '"')
    text = _ANY_TAG.sub("", text)
    text = _BRACKETED.sub("", text)
    text = _CIRCLED_MARKERS.sub("", text)
    text = _BULLET.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


def clean_resolved(text: str | None) -> str:
    """Clean verse text already returned by VerseResolver.resolve() for a
    Strong's-numbered DB — strips bare digit runs left glued to words."""
    if not text:
        return ""
    text = _BARE_STRONG_NUM.sub("", text)
    text = _STANDALONE_NUM.sub("", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()
