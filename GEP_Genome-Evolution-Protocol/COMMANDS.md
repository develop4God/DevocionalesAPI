# GEP Command Reference

All commands run from the project root with the venv active:
```bash
source .venv/bin/activate
```

---

## 1. Batch Pipeline — `batch_pipeline.py`

Full automated flow: build JSONL → upload → submit → poll → download → collect.

### 1.1 Full pipeline (build + submit)
```bash
# Phase 1 (linguistic) — DashScope
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 1 --provider dashscope

# Phase 1 — without genome (baseline / ablation)
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 1 --provider dashscope --no-genome

# Phase 2 (content coherence) — DashScope
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 2 --provider dashscope_batch_phase2

# Skip already reviewed entries
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 1 --provider dashscope --skip-reviewed

# Use local JSON instead of GitHub fetch
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 1 --provider dashscope \
    --local Devocional_year_2025_es_RVR1960.json
```

### 1.2 Dry run (build JSONL only, no upload)
```bash
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 1 --provider dashscope --dry-run
```

### 1.3 Use existing JSONL (skip build, go straight to upload)
```bash
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 1 --provider dashscope \
    --input batch_input_es_RVR1960_2025_p1_qwen-flash_20260425_013924.jsonl
```

### 1.4 Use existing results (skip upload/submit/poll/download, collect only)
```bash
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 1 --provider dashscope \
    --input  batch_input_es_RVR1960_2025_p1_qwen-flash_20260425_013924.jsonl \
    --results BIJOutputSet_es_RVR1960_2025_p1_qwen-flash_20260425_013924_results.jsonl
```

### 1.5 All languages — common batch commands
```bash
# Tagalog ASND 2026
python3 batch_pipeline.py --lang tl --version ASND --year 2026 --phase 1 --provider dashscope

# Portuguese ARC 2025
python3 batch_pipeline.py --lang pt --version ARC --year 2025 --phase 1 --provider dashscope

# English KJV 2025
python3 batch_pipeline.py --lang en --version KJV --year 2025 --phase 1 --provider dashscope

# Arabic NAV 2025
python3 batch_pipeline.py --lang ar --version NAV --year 2025 --phase 1 --provider dashscope

# German LU17 2025
python3 batch_pipeline.py --lang de --version LU17 --year 2025 --phase 1 --provider dashscope

# French LSG1910 2025
python3 batch_pipeline.py --lang fr --version LSG1910 --year 2025 --phase 1 --provider dashscope
```

---

## 2. Build Batch Only — `build_batch.py`

Build the JSONL file only (no upload). Useful for inspection before submitting.

```bash
# Phase 1 — DashScope
python3 build_batch.py --lang es --version RVR1960 --year 2025 --phase 1 --provider dashscope

# Phase 1 — without genome (baseline)
python3 build_batch.py --lang es --version RVR1960 --year 2025 --phase 1 \
    --provider dashscope --no-genome

# Phase 2 — Fireworks
python3 build_batch.py --lang es --version RVR1960 --year 2025 --phase 2 --provider fireworks

# Both phases in one request
python3 build_batch.py --lang es --version RVR1960 --year 2025 --phase 1,2

# Skip already reviewed
python3 build_batch.py --lang es --version RVR1960 --year 2025 --phase 1 --skip-reviewed

# Re-run specific entry IDs
python3 build_batch.py --lang es --version RVR1960 --year 2025 --phase 1 \
    --ids es_RVR1960_2025-01-05,es_RVR1960_2025-03-12

# Custom output path
python3 build_batch.py --lang es --version RVR1960 --year 2025 --phase 1 \
    --output data/batch_input/my_custom_batch.jsonl
```

---

## 3. Interactive / Overnight Critic — `critic_v3.py`

Runs the full two-phase review locally with Ollama (or cloud provider).

```bash
# Overnight mode (default — runs all pending entries)
python3 critic_v3.py --lang pt --version ARC --year 2025 --mode overnight

# Interactive mode (ask before each entry)
python3 critic_v3.py --lang es --version NVI --year 2025 --mode interactive

# Specific reader role (pt only)
python3 critic_v3.py --lang pt --version ARC --year 2025 --role elder
python3 critic_v3.py --lang pt --version ARC --year 2025 --role charismatic
python3 critic_v3.py --lang pt --version ARC --year 2025 --role new_believer

# Force cloud provider
python3 critic_v3.py --lang es --version RVR1960 --year 2025 --provider cloud

# Force local Ollama
python3 critic_v3.py --lang es --version RVR1960 --year 2025 --provider local

# Start from a specific date
python3 critic_v3.py --lang es --version RVR1960 --year 2025 --start-date 2025-06-01

# Phase 1 only (fast linguistic scan)
python3 critic_v3.py --lang es --version RVR1960 --year 2025 --phase 1

# Phase 2 only (content coherence)
python3 critic_v3.py --lang es --version RVR1960 --year 2025 --phase 2

# Use local JSON file
python3 critic_v3.py --lang pt --version ARC --year 2025 \
    --local ./Devocional_year_2025_pt_ARC.json
```

---

## 4. Reports & Audit — `review_flags.py`

Generate human-readable flag reports from batch results.

```bash
# Phase 1 flag report (auto-resolve latest files)
python3 review_flags.py --lang es --version RVR1960 --year 2025 --phase 1

# Phase 2 flag report
python3 review_flags.py --lang es --version RVR1960 --year 2025 --phase 2

# All verdicts (not just FLAG)
python3 review_flags.py --lang es --version RVR1960 --year 2025 --phase 1 --verdict ALL

# Point to specific files
python3 review_flags.py --lang es --version RVR1960 --year 2025 --phase 1 \
    --input  batch_input_es_RVR1960_2025_p1_qwen-flash_20260425_013924.jsonl \
    --results BIJOutputSet_es_RVR1960_2025_p1_qwen-flash_20260425_013924_results.jsonl

# Other languages
python3 review_flags.py --lang pt --version ARC --year 2025 --phase 1
python3 review_flags.py --lang tl --version ASND --year 2026 --phase 1
python3 review_flags.py --lang ar --version NAV --year 2025 --phase 1
```

---

## 5. Genome Commands — `critic_v3.py`

```bash
# View genome for a language/version
python3 critic_v3.py --genome es RVR1960 2025
python3 critic_v3.py --genome pt ARC 2025
python3 critic_v3.py --genome tl ASND 2026

# Print audit report
python3 critic_v3.py --lang pt --version ARC --year 2025 --report

# Re-queue error entries for re-processing
python3 critic_v3.py --lang pt --version ARC --year 2025 --requeue-errors

# List all known source files
python3 critic_v3.py --list-files
```

---

## 6. Seed Genome — `seed_tl_genome.py`

```bash
python3 seed_tl_genome.py
```

---

## 7. Collect Batch Results Manually — `collect_batch.py`

```bash
python3 collect_batch.py \
    --lang es --version RVR1960 --year 2025 \
    --input  data/batch_input/batch_input_es_RVR1960_2025_p1.jsonl \
    --results data/batch_output/BIJOutputSet_es_RVR1960_2025_p1_results.jsonl
```

---

## 8. Quick Comparison: With vs Without Genome

```bash
# ── Step A: baseline (no genome) ──────────────────────────────────────────────
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 1 --provider dashscope --no-genome --dry-run
# → produces: batch_input_es_RVR1960_2025_p1_<model>_<ts>.jsonl

# ── Step B: with genome (same entries) ────────────────────────────────────────
python3 batch_pipeline.py --lang es --version RVR1960 --year 2025 \
    --phase 1 --provider dashscope --dry-run

# Compare FLAG rates between the two result files using review_flags.py
python3 review_flags.py --lang es --version RVR1960 --year 2025 --phase 1 \
    --input  batch_input_es_RVR1960_2025_p1_<no-genome-ts>.jsonl \
    --results BIJOutputSet_es_RVR1960_2025_p1_<no-genome-ts>_results.jsonl

python3 review_flags.py --lang es --version RVR1960 --year 2025 --phase 1 \
    --input  batch_input_es_RVR1960_2025_p1_<genome-ts>.jsonl \
    --results BIJOutputSet_es_RVR1960_2025_p1_<genome-ts>_results.jsonl
```

---

## 9. Environment Setup

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Required environment variables (in .env or exported):
# GROQ_API_KEY=gsk_...
# FIREWORKS_API_KEY=fw_...
# DASHSCOPE_API_KEY=sk-...
# CEREBRAS_API_KEY=...  (optional)
```
