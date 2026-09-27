"""
language_length_bounds
───────────────────────
Per-language content length bounds (reflexion/oracion character counts) for
devotional generation. Decoupled from generation_core.py so each language's
calibrated bounds live in their own file here, and adding a new language
never requires editing another language's file or this registry.

To add a language: create <lang>.py next to this file (see ar.py for the
shape — LANGS tuple + BOUNDS). It is auto-discovered on import. Any
language without its own file falls back to LATIN_DEFAULT.
"""

from __future__ import annotations

import importlib
import pkgutil

from .base import LATIN_DEFAULT, LengthBounds

_REGISTRY: dict[str, LengthBounds] = {}
for _finder, _name, _ in pkgutil.iter_modules(__path__):
    if _name in ("base",):
        continue
    _module = importlib.import_module(f".{_name}", __name__)
    for _lang in _module.LANGS:
        _REGISTRY[_lang] = _module.BOUNDS


def get_length_bounds(lang: str) -> LengthBounds:
    return _REGISTRY.get(lang, LATIN_DEFAULT)
