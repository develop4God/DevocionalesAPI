"""
paths.py — GEP central path configuration.
Single responsibility: define all data and config directory paths.

All modules import from here instead of constructing paths independently.
No other module should hardcode file system paths — import from here instead.

Environment overrides (optional):
    GEP_DATA_DIR    override the root data directory
    GEP_CONFIG_DIR  override the config directory
"""
import os
from pathlib import Path

# ── Root ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_DIR    = ROOT / os.environ.get("GEP_CONFIG_DIR", "config")
PROVIDERS_YML = CONFIG_DIR / "providers.yml"

# ── Data ──────────────────────────────────────────────────────────────────────
DATA_DIR         = ROOT / os.environ.get("GEP_DATA_DIR", "data")
AUDIT_DIR        = DATA_DIR / "audit"
BATCH_INPUT_DIR  = DATA_DIR / "batch_input"
BATCH_OUTPUT_DIR = DATA_DIR / "batch_output"
GENOMES_DIR      = DATA_DIR / "genomes"
LOGS_DIR         = DATA_DIR / "logs"
SOURCE_DIR       = DATA_DIR / "source"
REPORTS_DIR      = DATA_DIR / "reports"


def ensure_dirs() -> None:
    """Create all data directories if they don't exist (idempotent)."""
    for d in (
        CONFIG_DIR,
        AUDIT_DIR,
        BATCH_INPUT_DIR,
        BATCH_OUTPUT_DIR,
        GENOMES_DIR,
        LOGS_DIR,
        SOURCE_DIR,
        REPORTS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def resolve_local(file_arg: str) -> Path:
    """
    Resolve a --local / --input argument to an absolute Path.
    If the file doesn't exist at the given path, check SOURCE_DIR automatically.
    This lets callers pass just a filename without the full data/source/ prefix.
    """
    p = Path(file_arg)
    if p.exists():
        return p
    candidate = SOURCE_DIR / p.name
    if candidate.exists():
        return candidate
    # Return the original path so the caller gets a clear FileNotFoundError
    return p


def resolve_batch_input(file_arg: str) -> Path:
    """Resolve --input: checks BATCH_INPUT_DIR if the bare filename isn't found."""
    p = Path(file_arg)
    if p.exists():
        return p
    candidate = BATCH_INPUT_DIR / p.name
    if candidate.exists():
        return candidate
    return p


def resolve_batch_output(file_arg: str) -> Path:
    """Resolve --results: checks BATCH_OUTPUT_DIR if the bare filename isn't found."""
    p = Path(file_arg)
    if p.exists():
        return p
    candidate = BATCH_OUTPUT_DIR / p.name
    if candidate.exists():
        return candidate
    return p
