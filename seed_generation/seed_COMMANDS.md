# seed_generation Command Reference

All commands run from the `seed_generation/` folder with the venv active:
```bash
source .venv/bin/activate
```

---

## Pipeline Overview

Two-step flow for generating devotionals from a seed file:

```
Step 1 — batch_claude_submit.py   → submits seed entries to Anthropic Batch API
Step 2 — batch_claude_collect.py  → polls, collects results, builds devotional JSON
```

Critic loop runs independently against an already-generated devotional file.

---

## 1. Submit Batch — `batch_claude_submit.py` (Step 1/2)

```bash
# Full submit — required args: --seed, --lang, --version, --output
python3 batch_claude_submit.py \
    --seed path/to/seed_fil_MBB05_2025.json \
    --lang fil \
    --version MBB05 \
    --output output/fil_MBB05_2025/

# Custom Claude model (default: claude-sonnet)
python3 batch_claude_submit.py \
    --seed path/to/seed_fil_MBB05_2025.json \
    --lang fil --version MBB05 --output output/fil_MBB05_2025/ \
    --model claude-opus-4-5

# Start from a specific date (skip earlier entries)
python3 batch_claude_submit.py \
    --seed path/to/seed_fil_MBB05_2025.json \
    --lang fil --version MBB05 --output output/fil_MBB05_2025/ \
    --start-date 2025-06-01

# Limit number of entries submitted (useful for testing)
python3 batch_claude_submit.py \
    --seed path/to/seed_fil_MBB05_2025.json \
    --lang fil --version MBB05 --output output/fil_MBB05_2025/ \
    --limit 10

# Dry run — build prompts only, no API calls, no upload
python3 batch_claude_submit.py \
    --seed path/to/seed_fil_MBB05_2025.json \
    --lang fil --version MBB05 --output output/fil_MBB05_2025/ \
    --dry-run

# Dry run full — print exact wire payload for first entry (no API call)
python3 batch_claude_submit.py \
    --seed path/to/seed_fil_MBB05_2025.json \
    --lang fil --version MBB05 \
    --dry-run-full
```

> After a successful submit, a `batch_state_*.json` file is written to the
> output directory. This is required for Step 2.

---

## 2. Collect Results — `batch_claude_collect.py` (Step 2/2)

```bash
# Auto-find latest batch state file (default)
python3 batch_claude_collect.py

# Point to a specific state file
python3 batch_claude_collect.py \
    --state output/fil_MBB05_2025/batch_state_fil_MBB05_<ts>.json

# Custom poll interval in seconds (default: 60)
python3 batch_claude_collect.py \
    --state output/fil_MBB05_2025/batch_state_fil_MBB05_<ts>.json \
    --poll-interval 30
```

> Collect polls until the batch is complete, then builds the final devotional JSON.

---

## 3. Critic Loop — `critic_loop_v2.py`

Runs quality review against a generated devotional file using a local Ollama model.

```bash
# Overnight mode — runs all entries unattended (default model: 27b)
python3 critic_loop_v2.py \
    --lang fil --version MBB05 --year 2025 --mode overnight

# Interactive mode — ask before each entry
python3 critic_loop_v2.py \
    --lang fil --version MBB05 --year 2025 --mode interactive

# Use 7b model (faster, less thorough)
python3 critic_loop_v2.py \
    --lang fil --version MBB05 --year 2025 --mode overnight --model 7b

# Start from a specific date
python3 critic_loop_v2.py \
    --lang fil --version MBB05 --year 2025 \
    --start-date 2025-06-01

# Evolve mode — load real fixes from audit log as few-shot seeds
python3 critic_loop_v2.py \
    --lang pt --version NVI --year 2025 --evolve

# List all known lang/version combinations
python3 critic_loop_v2.py --list-files
```

---

## 4. Validate Content — `seed_content_validator.py`

Validates a seed or generated devotional file for structural and content issues.

```bash
python3 seed_content_validator.py path/to/file.json
```

---

## 5. Repair Failed Entries — `batch_repair_failed.py`

Re-submits entries that failed or were skipped in a previous batch run.

```bash
python3 batch_repair_failed.py \
    --state output/fil_MBB05_2025/batch_state_fil_MBB05_<ts>.json
```

---

## 6. Common Language Examples

```bash
# Filipino MBB05 2025
python3 batch_claude_submit.py --seed seeds/seed_fil_MBB05_2025.json \
    --lang fil --version MBB05 --output output/fil_MBB05_2025/

# Filipino ASND 2026
python3 batch_claude_submit.py --seed seeds/seed_fil_ASND_2026.json \
    --lang fil --version ASND --output output/fil_ASND_2026/

# Portuguese ARC 2025
python3 batch_claude_submit.py --seed seeds/seed_pt_ARC_2025.json \
    --lang pt --version ARC --output output/pt_ARC_2025/

# Arabic NAV 2025
python3 batch_claude_submit.py --seed seeds/seed_ar_NAV_2025.json \
    --lang ar --version NAV --output output/ar_NAV_2025/
```

---

## 7. Environment Setup

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Required .env variables:
# ANTHROPIC_API_KEY=sk-ant-...

# Optional (for critic_loop_v2 with local Ollama):
# Ollama running locally with gemma2:27b or gemma2:7b pulled
```
