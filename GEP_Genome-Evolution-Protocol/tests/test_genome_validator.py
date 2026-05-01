"""
test_genome_validator.py — GEP Critic v3 genome validator tests.

Validates:
    - Pattern matching in entries
    - Validation report generation
    - File validation
    - Edge cases and error handling
"""

import sys
import tempfile
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from genome_validator import (
    validate_entry, validate_entries, validate_file,
    PatternMatch, ValidationReport
)
from models import DevotionalEntry, Genome, GenomeFragment, PauseCategory, GeneState
from datetime import datetime

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results = []


def check(name: str, condition: bool, detail: str = ""):
    """Record test result."""
    icon = PASS if condition else FAIL
    results.append((icon, name, detail))
    print(f"  {icon}  {name}" + (f" — {detail}" if detail else ""))


# ════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  🧪 GEP Critic v3 — Genome Validator Tests")
print("═" * 60)

# ── Test 1: Pattern matching in single entry ─────────────────────────────────
print("\n[1] Testing pattern matching in single entry...")

# Create a test entry
entry = DevotionalEntry(
    date="2025-01-01",
    id="test_001",
    language="es",
    version="NVI",
    versiculo="Juan 3:16",
    reflexion="Dios nos ama profundamente profundamente y desea lo mejor.",
    oracion="Señor, gracias por tu amor incondicional.",
    para_meditar=[],
    tags=[]
)

# Create a test genome with a repetition pattern
fragment = GenomeFragment(
    id="frag_001",
    language="es",
    version="NVI",
    category=PauseCategory.REPETITION,
    pattern="profundamente profundamente",
    example_quote="profundamente profundamente",
    evidence_dates=["2025-01-01", "2025-01-02"],
    confidence=0.8,
    state=GeneState.CONFIRMED,
    created_at=datetime.now().isoformat(),
    updated_at=datetime.now().isoformat()
)

genome = Genome(
    language="es",
    version="NVI",
    genome_version="1.0",
    fragments=[fragment],
    total_entries_reviewed=10,
    total_pauses=2,
    updated_at=datetime.now().isoformat()
)

matches = validate_entry(entry, genome, confidence_threshold=0.6)
check("validate_entry returns matches", len(matches) > 0, f"found {len(matches)}")
check("  Match has correct entry_id", matches[0].entry_id == "test_001" if matches else False)
check("  Match has correct category", matches[0].category == "repetition" if matches else False)

# ── Test 2: No matches when pattern doesn't exist ────────────────────────────
print("\n[2] Testing no matches when pattern doesn't exist...")

clean_entry = DevotionalEntry(
    date="2025-01-02",
    id="test_002",
    language="es",
    version="NVI",
    versiculo="Salmos 23:1",
    reflexion="El Señor es mi pastor, nada me falta.",
    oracion="Gracias Señor por tu cuidado.",
    para_meditar=[],
    tags=[]
)

matches = validate_entry(clean_entry, genome, confidence_threshold=0.6)
check("No matches for clean entry", len(matches) == 0, f"found {len(matches)}")

# ── Test 3: Validation report generation ─────────────────────────────────────
print("\n[3] Testing validation report generation...")

entries = [entry, clean_entry]
report = validate_entries(entries, genome, "es", "NVI", 2025, confidence_threshold=0.6)

check("Report has correct total_entries", report.total_entries == 2)
check("Report has correct entries_with_matches", report.entries_with_matches == 1)
check("Report has correct total_matches", report.total_matches > 0)
check("Report categories_found has repetition", "repetition" in report.categories_found)

# ── Test 4: Summary generation ────────────────────────────────────────────────
print("\n[4] Testing summary generation...")

summary = report.summary()
check("Summary is non-empty", len(summary) > 0)
check("Summary contains 'Validation Report'", "Validation Report" in summary)
check("Summary contains category info", "repetition" in summary.lower())

# ── Test 5: File validation ───────────────────────────────────────────────────
print("\n[5] Testing file validation...")

# Create temporary JSON file
temp_data = {
    "data": {
        "es": {
            "2025-01-01": {
                "id": "test_001",
                "language": "es",
                "version": "NVI",
                "versiculo": "Juan 3:16",
                "reflexion": "Dios nos ama profundamente profundamente y desea lo mejor.",
                "oracion": "Señor, gracias por tu amor.",
                "para_meditar": [],
                "tags": []
            }
        }
    }
}

with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
    json.dump(temp_data, f)
    temp_path = f.name

try:
    file_report = validate_file(temp_path, genome, "es", confidence_threshold=0.6)
    check("File validation succeeds", file_report is not None)
    check("  File report has matches", file_report.total_matches > 0)
finally:
    Path(temp_path).unlink()

# ── Test 6: Edge cases ─────────────────────────────────────────────────────────
print("\n[6] Testing edge cases...")

# Empty genome
empty_genome = Genome(
    language="es",
    version="NVI",
    genome_version="1.0",
    fragments=[],
    total_entries_reviewed=0,
    total_pauses=0,
    updated_at=datetime.now().isoformat()
)

matches = validate_entry(entry, empty_genome, confidence_threshold=0.6)
check("No matches with empty genome", len(matches) == 0)

# None genome
matches = validate_entry(entry, None, confidence_threshold=0.6)
check("No matches with None genome", len(matches) == 0)

# Low confidence threshold
low_conf_fragment = GenomeFragment(
    id="frag_002",
    language="es",
    version="NVI",
    category=PauseCategory.TYPO,
    pattern="test pattern",
    example_quote="test quote",
    evidence_dates=["2025-01-01"],
    confidence=0.3,  # Below threshold
    state=GeneState.CANDIDATE,
    created_at=datetime.now().isoformat(),
    updated_at=datetime.now().isoformat()
)

low_conf_genome = Genome(
    language="es",
    version="NVI",
    genome_version="1.0",
    fragments=[low_conf_fragment],
    total_entries_reviewed=1,
    total_pauses=1,
    updated_at=datetime.now().isoformat()
)

matches = validate_entry(entry, low_conf_genome, confidence_threshold=0.6)
check("No matches for low confidence fragments", len(matches) == 0)

# ── Test 7: Multiple pattern categories ───────────────────────────────────────
print("\n[7] Testing multiple pattern categories...")

multi_entry = DevotionalEntry(
    date="2025-01-03",
    id="test_003",
    language="es",
    version="NVI",
    versiculo="Juan 3:16",
    reflexion="Dios nos ama profundamente profundamente. La soteriológica paradigma universal.",
    oracion="Señor, gracias gracias por tu amor.",
    para_meditar=[],
    tags=[]
)

multi_fragments = [
    GenomeFragment(
        id="frag_rep",
        language="es",
        version="NVI",
        category=PauseCategory.REPETITION,
        pattern="profundamente profundamente",
        example_quote="profundamente profundamente",
        evidence_dates=["2025-01-01"],
        confidence=0.8,
        state=GeneState.CONFIRMED,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    ),
    GenomeFragment(
        id="frag_reg",
        language="es",
        version="NVI",
        category=PauseCategory.REGISTER_DRIFT,
        pattern="soteriológica.*paradigma",
        example_quote="soteriológica paradigma universal",
        evidence_dates=["2025-01-02"],
        confidence=0.7,
        state=GeneState.CONFIRMED,
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat()
    ),
]

multi_genome = Genome(
    language="es",
    version="NVI",
    genome_version="1.0",
    fragments=multi_fragments,
    total_entries_reviewed=10,
    total_pauses=5,
    updated_at=datetime.now().isoformat()
)

matches = validate_entry(multi_entry, multi_genome, confidence_threshold=0.6)
check("Multiple patterns detected", len(matches) >= 2, f"found {len(matches)}")

categories = set(m.category for m in matches)
check("  Both categories found", len(categories) >= 2, f"categories: {categories}")

# ════════════════════════════════════════════════════════════════════════════
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'═' * 60}")
print(f"  Results: {passed} passed / {failed} failed")
if failed == 0:
    print("  🎉 All tests passed — genome validator is working correctly.")
else:
    print("  ⚠️  Fix failures before deployment.")
    for icon, name, detail in results:
        if icon == FAIL:
            print(f"     → {name}" + (f": {detail}" if detail else ""))
print(f"{'═' * 60}\n")

sys.exit(0 if failed == 0 else 1)
