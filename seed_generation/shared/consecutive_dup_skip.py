"""
consecutive_dup_skip
──────────────────────
Single source of truth for the "consecutive duplicate word" content check,
shared by seed_content_validator.py, BATCH/batch_collect.py, and
scripts/validate_devocional_gui.py — previously each had its own inline
copy of this logic (and, briefly, two different skip-list JSON files).

Skip-list data lives in consecutive_dup_skip.json, keyed by language code
plus an "any" key that applies everywhere (liturgical/rhetorical words like
"amen", "holy", "قدوس"). Add a new exception there, not in this file.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Optional

_SKIP_LIST_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "consecutive_dup_skip.json"
)

_SENT_END_PUNCT = frozenset({".", "!", "?", ":", "»", "”"})
_STRIP_CHARS = ".,;:!?،؛؟‘’“”"


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(_SKIP_LIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_consecutive_dup_skip(lang: str) -> frozenset[str]:
    """Returns the lowercased skip-list for `lang` — the shared 'any' list
    plus any language-specific additions. Unknown languages get 'any' alone."""
    data = _load()
    words = list(data.get("any", [])) + list(data.get(lang, []))
    return frozenset(w.lower() for w in words)


def find_consecutive_duplicate(text: str, lang: str) -> Optional[str]:
    """
    Returns the first consecutive duplicate word pair (as a quoted string),
    or None if clean.

    - Skips repetition across a sentence boundary (e.g. "love. Love,") —
      that's rhetorical anaphora, not a generation artifact.
    - Skips words in the per-language skip list (liturgical/scriptural
      repetition, e.g. Trisagion "holy, holy, holy").
    - Strips a leading Arabic 'و' (and) conjunction before the skip-list
      comparison, so "وقدوس وقدوس" (and-holy and-holy) still matches the
      skip-listed "قدوس" without needing a separate JSON entry per prefix.
    """
    skip_list = get_consecutive_dup_skip(lang)
    words = text.split()
    for i in range(len(words) - 1):
        raw1 = words[i]
        if raw1 and raw1[-1] in _SENT_END_PUNCT:
            continue
        w1 = raw1.strip(_STRIP_CHARS).lower()
        w2 = words[i + 1].strip(_STRIP_CHARS).lower()
        w1_bare = w1[1:] if w1.startswith("و") and len(w1) > 4 else w1
        if (
            w1 == w2
            and len(w1) > 3
            and w1 not in skip_list
            and w1_bare not in skip_list
        ):
            return f"'{raw1} {words[i + 1]}'"
    return None
