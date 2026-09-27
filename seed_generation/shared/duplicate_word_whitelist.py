"""
duplicate_word_whitelist
──────────────────────────
Words allowed to repeat consecutively without tripping the "consecutive
duplicate word" check (liturgical phrases, direct scripture quotes like
the Arabic Trisagion "قدوس، قدوس، قدوس"). Data-driven so a new exception
is a JSON edit, not a code change — see duplicate_word_whitelist.json.

Reusable by any check that needs a per-language whitelist of this kind:
seed_content_validator.py and BATCH/batch_collect.py both use this.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

_WHITELIST_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "duplicate_word_whitelist.json"
)


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(_WHITELIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_duplicate_whitelist(lang: str) -> frozenset[str]:
    """Returns the lowercased set of words allowed to repeat consecutively
    for `lang` — the shared 'default' list plus any language-specific
    additions. Unknown languages get the default list alone."""
    data = _load()
    words = list(data.get("default", [])) + list(data.get(lang, []))
    return frozenset(w.lower() for w in words)
