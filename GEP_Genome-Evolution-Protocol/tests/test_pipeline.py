"""
test_pipeline.py — GEP Critic v3 full pipeline test.
Runs overnight mode on 5 stub entries, then asserts every critical path.
No Ollama needed. No network. No file system side effects (temp dir).
"""
import json
import os
import sys
import tempfile
import traceback

# ── Patch working dir to temp so audit/genome files don't pollute project ────
_tmp = tempfile.mkdtemp(prefix="gep_critic_test_")
os.chdir(_tmp)

# ── Add test_pipeline to path ─────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit import audit_path, load_reviewed_dates
from genome import ensure_genome, get_saved_genomes
from models import AuditRecord, PauseCategory, Verdict
from runner import run_overnight
from source import get_sample_entries

LANG    = "es"
VERSION = "NVI"
YEAR    = 2025

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results = []

def check(name: str, condition: bool, detail: str = ""):
    icon = PASS if condition else FAIL
    results.append((icon, name, detail))
    print(f"  {icon}  {name}" + (f" — {detail}" if detail else ""))


# ════════════════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("  🧪 GEP Critic v3 — Pipeline Test")
print("  Entries: 5 | Lang: es | Version: NVI | Year: 2025")
print("═"*60)

# ── Run the overnight pipeline ────────────────────────────────────────────────
entries = get_sample_entries()
try:
    run_overnight(
        entries=entries,
        lang=LANG,
        version=VERSION,
        year=YEAR,
        model_key="auto",
        start_date=None,
    )
    run_ok = True
except Exception as e:
    traceback.print_exc()
    run_ok = False

check("Pipeline runs without exception", run_ok)

# ── Assert audit log was written ──────────────────────────────────────────────
log_path = audit_path(LANG, VERSION, YEAR)
check("Audit log file created", log_path.exists(), str(log_path))

records = []
if log_path.exists():
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

check("All 5 entries logged", len(records) == 5, f"found {len(records)}")

# ── Assert AuditRecord has phase1 fields ─────────────────────────────────────
sample = AuditRecord(
    date="2025-01-01", id="test", language="es", version="NVI",
    reviewed_at="2025-01-01T00:00:00", action="test",
    verdict=Verdict.OK, reaction="ok",
)
has_phase1_fields = all(
    hasattr(sample, f)
    for f in ["phase1_verdict", "phase1_issue", "phase1_quoted",
              "phase1_confidence", "phase1_raw"]
)
check("AuditRecord has all 5 phase1 fields", has_phase1_fields)

# ── Assert phase1 fields are persisted to JSONL ───────────────────────────────
phase1_written = all("phase1_verdict" in r for r in records)
check("phase1_verdict written to every JSONL record", phase1_written)

# ── Assert asset_id present on every record ───────────────────────────────────
check("asset_id present on every record", all("asset_id" in r for r in records))

# ── Assert at least one FLAG and one PAUSE in the run ─────────────────────────
has_flag  = any(r.get("phase1_verdict") == "FLAG"  for r in records)
has_pause = any(r.get("verdict")        == "PAUSE" for r in records)
check("At least one Phase 1 FLAG recorded",  has_flag)
check("At least one Phase 2 PAUSE recorded", has_pause)

# ── Assert generic_reflection is a valid PauseCategory ───────────────────────
try:
    cat = PauseCategory("generic_reflection")
    check("PauseCategory.GENERIC_REFLECTION resolves", cat == PauseCategory.GENERIC_REFLECTION)
except ValueError:
    check("PauseCategory.GENERIC_REFLECTION resolves", False, "ValueError — still missing from enum")

# ── Assert save_genome was called ─────────────────────────────────────────────
saved = get_saved_genomes()
check("save_genome was called at end of run", len(saved) > 0, f"calls: {len(saved)}")

# ── Assert genome accumulated fragments ──────────────────────────────────────
genome = ensure_genome(LANG, VERSION, YEAR)
check("Genome has at least 1 fragment", len(genome.fragments) >= 1,
      f"fragments: {len(genome.fragments)}, pauses: {genome.total_pauses}")

# ── Assert no already-reviewed entries are re-processed ─────────────────────
reviewed = load_reviewed_dates(log_path)
check("Skip-reviewed logic works (5 dates in log)", len(reviewed) == 5, f"dates: {len(reviewed)}")

# ── Assert confidence field stored for a PAUSE record ────────────────────────
pause_records = [r for r in records if r.get("verdict") == "PAUSE"]
conf_ok = all(isinstance(r.get("confidence"), float) for r in pause_records)
check("Confidence stored as float on PAUSE records", conf_ok)

# ── Assert run log was written ────────────────────────────────────────────────
run_log = log_path.parent / f"run_log_{LANG}_{VERSION}_{YEAR}.log"
check("Run log file created", run_log.exists(), str(run_log))

# ════════════════════════════════════════════════════════════════════════════
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'═'*60}")
print(f"  Results: {passed} passed / {failed} failed")
if failed == 0:
    print("  🎉 All checks passed — pipeline ready.")
else:
    print("  ⚠️  Fix failures before real run.")
    for icon, name, detail in results:
        if icon == FAIL:
            print(f"     → {name}" + (f": {detail}" if detail else ""))
print(f"{'═'*60}\n")

sys.exit(0 if failed == 0 else 1)
