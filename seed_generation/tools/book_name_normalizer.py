"""Data-driven book-title sanitation for Bible database citations.

Each language has a JSON file in ``book_name_sanitizers/``.  A configuration
maps canonical MyBible book numbers to the display title used in seeds.  The
resolver supplies the language and book number; this module has no SQLite or
reference-parsing responsibilities.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CONFIG_DIR = Path(__file__).with_name("book_name_sanitizers")


@lru_cache(maxsize=None)
def _load_language(language: str) -> dict[str, str]:
    path = _CONFIG_DIR / f"{language.lower()}.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as source:
        return json.load(source).get("book_names", {})


def sanitize_book_name(raw_name: str, book_number: int, language: str | None) -> str:
    """Return the configured readable title, or preserve ``raw_name``.

    ``language`` is deliberately optional so existing callers remain safe
    while a new language configuration is being introduced.
    """
    if not language:
        return raw_name
    return _load_language(language).get(str(book_number), raw_name)
