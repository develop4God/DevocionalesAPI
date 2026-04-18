"""
models.py — GEP Critic v3
Data structures. Single source of truth.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Verdict(str, Enum):
    OK = "OK"
    PAUSE = "PAUSE"   # reader paused — something felt off


class PauseCategory(str, Enum):
    VERSE_MISMATCH   = "verse_mismatch"    # quoted verse doesn't match reference
    TYPO             = "typo"              # spelling / grammar error
    NAME_ERROR       = "name_error"        # biblical name misspelled or wrong
    PRAYER_DRIFT     = "prayer_drift"      # prayer disconnected from verse/reflection
    HALLUCINATION      = "hallucination"       # invented detail (attribution, citation)
    REGISTER_DRIFT     = "register_drift"      # tone too academic or too casual
    GENERIC_REFLECTION = "generic_reflection"  # reflection could apply to any verse
    OTHER              = "other"


@dataclass
class ReaderReaction:
    """Raw output from the simulated reader LLM call."""
    verdict: Verdict
    reaction: str                          # natural language: what the reader felt
    quoted_pause: Optional[str] = None    # exact phrase that caused a pause
    category: Optional[PauseCategory] = None
    confidence: float = 1.0               # 0.0–1.0, populated by validator


@dataclass
class DevotionalEntry:
    """One devotional entry from the source JSON."""
    date: str
    id: str
    language: str
    version: str
    versiculo: str
    reflexion: str
    oracion: str
    para_meditar: list = field(default_factory=list)
    tags: list = field(default_factory=list)


@dataclass
class AuditRecord:
    """One row in the audit JSONL log."""
    date: str
    id: str
    language: str
    version: str
    reviewed_at: str
    action: str                            # "overnight" | "interactive" | "approved" | "skipped"
    verdict: Verdict
    reaction: str
    quoted_pause: Optional[str] = None
    category: Optional[str] = None
    confidence: float = 1.0
    genome_fragment_id: Optional[str] = None   # if this reaction produced a genome fragment
    raw_response: Optional[str] = None         # stored on model error for debugging
    phase1_verdict:    Optional[str] = None
    phase1_issue:      Optional[str] = None
    phase1_quoted:     Optional[str] = None
    phase1_confidence: Optional[float] = None
    phase1_raw:        Optional[str] = None


@dataclass
class GenomeFragment:
    """
    One unit of accumulated reader knowledge.
    GEP asset: grows over runs, seeds future critics.
    """
    id: str
    language: str
    version: str
    category: PauseCategory
    pattern: str                           # description of what triggers a reader pause
    example_quote: str                     # the phrase that caused the first pause
    evidence_dates: list[str]             # audit dates that confirmed this pattern
    confidence: float                      # grows as more evidence accumulates
    created_at: str
    updated_at: str


@dataclass
class Genome:
    """
    The accumulated knowledge base for a given lang/version.
    GEP genome: loaded at start of each run, updated at end.
    """
    language: str
    version: str
    genome_version: str
    fragments: list[GenomeFragment] = field(default_factory=list)
    total_entries_reviewed: int = 0
    total_pauses: int = 0
    updated_at: str = ""

    def fragment_by_category(self, cat: PauseCategory) -> list[GenomeFragment]:
        return [f for f in self.fragments if f.category == cat]

    def high_confidence_fragments(self, threshold: float = 0.7) -> list[GenomeFragment]:
        return [f for f in self.fragments if f.confidence >= threshold]
