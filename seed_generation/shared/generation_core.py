"""
generation_core.py
───────────────────
Provider-agnostic shared logic for seed-driven devotional generation.

Extracted from the duplicated code in client_generate_from_seed.py,
client_generate_from_seed_claude.py, and test_generate_ollama.py:
DevotionalBuilder, checkpoint load/save/delete, and save_output were
each reimplemented identically in all three files. This module is the
single source of truth going forward.

Provider-specific generation (the actual model call) is NOT here — see
the Generator protocol below. Each provider implements generate(prompt)
-> str and plugs into this shared pipeline.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

_TAGS_MASTER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tags_master.json"
)
_tags_master_cache: dict | None = None


def _load_tags_master() -> dict:
    global _tags_master_cache
    if _tags_master_cache is None:
        with open(_TAGS_MASTER_PATH, encoding="utf-8") as f:
            _tags_master_cache = json.load(f)["tags"]
    return _tags_master_cache


def _normalize_tag(tag: str) -> str:
    return re.sub(r"[\s\-']", "", tag).lower()


# =============================================================================
# PROMPT + PARSING — verified working with the local Ollama/gemma4 pipeline
# (test_generate_ollama.py). This is the tested prompt, not pipeline_shared's
# newer, unvalidated-against-Ollama prompt — keep them separate.
# =============================================================================


def build_prompt(verse_cita: str, lang: str) -> str:
    return "\n\n".join(
        [
            f"You are a devoted biblical devotional writer. "
            f'Write a christian devotional in {lang.upper()} based on the key verse: "{verse_cita}".',
            "Write in a simple, warm, pastoral tone. "
            "State ideas affirmatively — express what is true and present, "
            "not what is absent, false, or being denied. "
            "Avoid 'not X, but Y' style contrast constructions,"
            "Return ONLY a valid JSON object with these exact keys:",
            f"- `reflexion`: contextualized reflection on the verse "
            f"(minimum 900 characters, in {lang}).",
            f"- `oracion`: Prayer on the devotional theme (minimum 150 words, 100% in {lang}), "
            f"MUST end with the standard closing phrase 'in the name of Jesus, amen', "
            f"written entirely in {lang} (do not mix in any English words). "
            f"Write this closing phrase exactly ONCE, as the very last words of the prayer.",
            f"RULES:\n"
            f"- ALL text MUST be 100% in {lang} — no language mixing.\n"
            f"- Do NOT include transliterations, romanizations, or text in parentheses.\n"
            f"- Feel free to open the reflection in a way that fits the verse's own tone, "
            f"rather than a standard formula.",
        ]
    )


def parse_content(raw_text: str) -> tuple[str, str]:
    """Extract (reflexion, oracion) from raw model output. Raises ValueError on failure."""
    raw = raw_text.strip().replace("```json", "").replace("```", "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model response")
    data = json.loads(match.group())
    reflexion = data.get("reflexion", "").strip()
    oracion = data.get("oracion", "").strip()
    if not reflexion or not oracion:
        raise ValueError("Empty reflexion or oracion in model response")
    return reflexion, oracion


# =============================================================================
# GENERATOR INTERFACE — the one seam providers plug into
# =============================================================================


class Generator(Protocol):
    """Anything that turns a prompt into raw model text implements this."""

    def generate(self, prompt: str) -> str: ...


# =============================================================================
# DEVOTIONAL BUILDER
# =============================================================================


class DevotionalValidationError(ValueError):
    pass


class DevotionalBuilder:
    def __init__(
        self, date_key: str, seed_entry: dict, master_lang: str, master_version: str
    ):
        self._date = date_key
        self._seed = seed_entry
        self._lang = master_lang
        self._version = master_version
        self._reflexion = ""
        self._oracion = ""

    def merge(self, reflexion: str, oracion: str) -> "DevotionalBuilder":
        self._reflexion = reflexion.strip()
        self._oracion = oracion.strip()
        return self

    def _build_versiculo(self) -> str:
        cita = self._seed["versiculo"]["cita"]
        texto = self._seed["versiculo"]["texto"]
        return cita + " " + self._version + ': "' + texto + '"'

    def _build_id(self) -> str:
        cita = self._seed["versiculo"]["cita"]
        id_part = re.sub(r"\s+", "", cita).replace(":", "")
        date_compact = self._date.replace("-", "")
        return id_part + self._version + date_compact

    def _extract_tags(self) -> list:
        tags = self._seed.get("tags", [])
        if not isinstance(tags, list) or not tags:
            return ["devotional", "fe"]
        if self._lang == "en":
            return tags
        tags_master = _load_tags_master()
        translated = []
        for tag in tags:
            entry = tags_master.get(_normalize_tag(tag))
            translated.append(
                entry[self._lang] if entry and self._lang in entry else tag
            )
        return translated

    def validate(self) -> None:
        errors = []
        if not self._reflexion:
            errors.append("reflexion empty")
        if not self._oracion:
            errors.append("oracion empty")
        if not self._seed.get("versiculo", {}).get("cita"):
            errors.append("cita missing")
        if not self._seed.get("versiculo", {}).get("texto"):
            errors.append("texto missing")
        if not self._seed.get("para_meditar"):
            errors.append("para_meditar empty")
        if errors:
            raise DevotionalValidationError("[" + self._date + "] " + "; ".join(errors))

    def build(self) -> dict:
        self.validate()
        return {
            "id": self._build_id(),
            "date": self._date,
            "language": self._lang,
            "version": self._version,
            "versiculo": self._build_versiculo(),
            "reflexion": self._reflexion,
            "para_meditar": self._seed["para_meditar"],
            "oracion": self._oracion,
            "tags": self._extract_tags(),
        }


# =============================================================================
# CHECKPOINT
# =============================================================================

_SAFE_IDENTIFIER_CHAR_RE = re.compile(r"[A-Za-z0-9._-]")


def slugify_identifier(value: str) -> str:
    """Turn an external value (e.g. a --model tag) into a filesystem-safe identifier.

    Each character outside [A-Za-z0-9._-] is replaced by its hex code point
    (e.g. ":" -> "-3a-"), not collapsed to a shared placeholder — collapsing
    distinct unsafe characters to the same replacement lets two different
    external values collide onto the same identifier (e.g. "a:b" and "a/b"
    both becoming "a-b"), which would silently merge two runs' checkpoints.
    This is the single point every external value must pass through before
    becoming part of a filename — do not string-interpolate a raw external
    value into a path anywhere else.
    """
    return "".join(
        c if _SAFE_IDENTIFIER_CHAR_RE.match(c) else f"-{ord(c):x}-" for c in value
    )


def pending_dates(all_dates: list[str], completed: dict) -> list[str]:
    """Dates from all_dates not yet present in completed, order preserved.

    Resume must be content-based (skip by key), not position-based (skip by
    count) — completed entries are not guaranteed to be an exact chronological
    prefix of all_dates across arbitrary runs.
    """
    return [d for d in all_dates if d not in completed]


@dataclass
class CheckpointStore:
    """Checkpoint persistence for a single generation run.

    One instance per (provider, seed) combination — pass a distinct
    checkpoint_file per provider so parallel runs don't collide.
    """

    checkpoint_file: str

    def load(self) -> dict | None:
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, encoding="utf-8") as f:
                    data = json.load(f)
                print(f"INFO: Checkpoint found — {data['completed_count']} dates done")
                return data
            except Exception as e:
                print(f"WARNING: Could not load checkpoint: {e}")
        return None

    def save(
        self,
        completed: dict,
        count: int,
        seed_path: str,
        lang: str,
        version: str,
        output_dir: str,
    ) -> None:
        data = {
            "completed": completed,
            "completed_count": count,
            "seed_path": seed_path,
            "master_lang": lang,
            "master_version": version,
            "output_dir": output_dir,
            "timestamp": datetime.now().isoformat(),
        }
        directory = os.path.dirname(self.checkpoint_file) or "."
        fd, tmp_path = tempfile.mkstemp(
            dir=directory, prefix=".checkpoint_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.checkpoint_file)
        except BaseException:
            os.remove(tmp_path)
            raise
        print(f"  checkpoint saved — {count} completed")

    def delete(self) -> None:
        if os.path.exists(self.checkpoint_file):
            try:
                os.remove(self.checkpoint_file)
            except Exception:
                pass


# =============================================================================
# OUTPUT
# =============================================================================


def save_output(
    completed: dict, lang: str, version: str, output_dir: str, suffix: str = ""
) -> str:
    """Write completed devotionals to a timestamped output JSON file.

    suffix: optional provider tag, e.g. "claude" or "ollama", inserted
    before the timestamp — mirrors the raw_<lang>_<version>_<provider>_<ts>.json
    naming already used by the provider-specific clients.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{suffix}" if suffix else ""
    filename = f"raw_{lang}_{version}{tag}_{ts}.json"
    path = os.path.join(output_dir, filename)
    nested = {lang: {date: [devo] for date, devo in completed.items()}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"data": nested}, f, ensure_ascii=False, indent=2)
    return path
