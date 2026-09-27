"""
language_length_bounds
───────────────────────
Per-language content length bounds (reflexion/oracion character counts) for
devotional generation. Decoupled from generation_core.py so each language's
calibrated bounds live in their own file here, and adding a new language
never requires editing another language's file.

To add a language: create <lang>.py next to this file (see ar.py for the
shape — LANGS tuple + BOUNDS), then register it below. Any language not
registered falls back to LATIN_DEFAULT.
"""

from __future__ import annotations

from . import ar
from .base import LATIN_DEFAULT, LengthBounds

_REGISTRY: dict[str, LengthBounds] = {}
for _module in (ar,):
    for _lang in _module.LANGS:
        _REGISTRY[_lang] = _module.BOUNDS


def get_length_bounds(lang: str) -> LengthBounds:
    return _REGISTRY.get(lang, LATIN_DEFAULT)
