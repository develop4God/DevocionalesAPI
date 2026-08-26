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
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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
    def __init__(self, date_key: str, seed_entry: dict, master_lang: str, master_version: str):
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
        if isinstance(tags, list) and tags:
            return tags
        return ["devotional", "fe"]

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
        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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
