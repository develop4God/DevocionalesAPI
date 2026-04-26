# GEP — Genome Evolution Protocol

## Philosophy
Not an editor. Not a theologian. A faithful reader who opens the app every morning.
If something feels wrong, they notice it. That reaction is the gold.

---

## Project Structure

```
GEP_Genome-Evolution-Protocol/
├── config/
│   └── providers.yml          ← all LLM provider definitions (API keys, models, limits)
├── data/
│   ├── audit/                 ← critic_audit_{lang}_{version}_{year}.jsonl
│   ├── batch_input/           ← batch_input_{lang}_{version}_{year}_p{N}.jsonl
│   ├── batch_output/          ← BIJOutputSet_{lang}_{version}_{year}_results.jsonl
│   ├── genomes/               ← genome_{lang}_{version}_{year}.json
│   ├── logs/                  ← run_log_{lang}_{version}_{year}.log
│   └── source/                ← Devocional_year_{year}_{lang}_{version}.json
├── backup-logs/               ← historical backups and deprecated files
├── Phase1_critic/             ← Phase 1 output samples
│
├── paths.py           ← SINGLE SOURCE OF TRUTH for all directory paths
├── audit.py           ← read/write JSONL audit log
├── batch_client.py    ← OpenAI-compatible batch API (upload/submit/poll/download)
├── batch_pipeline.py  ← full pipeline orchestrator (one command end-to-end)
├── build_batch.py     ← build JSONL batch file from devotional JSON
├── cloud_client.py    ← provider-agnostic API call routing (providers.yml driven)
├── collect_batch.py   ← parse batch results → write audit log
├── critic_v3.py       ← interactive/overnight CLI entry point
├── genome.py          ← GEP genome: load, absorb, persist, promote
├── models.py          ← all data structures (single source of truth)
├── models_helper.py   ← model utilities
├── ollama_client.py   ← local Ollama routing
├── prompts.py         ← LLM prompt builders (simulated reader persona)
├── runner.py          ← orchestrates interactive / overnight modes
├── seed_tl_genome.py  ← one-time genome seeding utility
├── source.py          ← fetch and parse devotional source JSON
└── test_pipeline.py   ← pipeline tests
```

---

## SOLID Architecture

| Principle | Applied |
|---|---|
| **S** — Single Responsibility | Each module has one job. `audit.py` only reads/writes logs. `batch_client.py` only talks to the batch API. |
| **O** — Open/Closed | Add providers in `config/providers.yml` — no code changes. |
| **L** — Liskov | `batch_pipeline.py` calls `process_results()` the same way `collect_batch` CLI does. |
| **I** — Interface Segregation | `BatchClient` exposes only `upload/submit/poll/download`. Orchestration is in `batch_pipeline.py`. |
| **D** — Dependency Inversion | All paths come from `paths.py`. All providers come from `providers.yml`. Nothing is hardcoded. |

---

## Workflow

### Option A — Full automated pipeline (recommended)

```bash
python3 batch_pipeline.py \
  --lang es --version RVR1960 --year 2025 \
  --phase 1 --provider dashscope \
  --local Devocional_year_2025_es_RVR1960.json
```

This single command:
1. **Builds** the JSONL from the source file
2. **Uploads** to DashScope
3. **Submits** the batch job
4. **Polls** until completion (every 30s by default)
5. **Downloads** results → `data/batch_output/`
6. **Collects** results → `data/audit/critic_audit_{lang}_{version}_{year}.jsonl`

### Option B — Step by step

```bash
# 1. Build JSONL only
python3 build_batch.py \
  --lang es --version RVR1960 --year 2025 \
  --phase 1 --provider dashscope \
  --local data/source/Devocional_year_2025_es_RVR1960.json
# Output: data/batch_input/batch_input_es_RVR1960_2025_p1.jsonl

# 2. Upload + submit + poll + download + collect
python3 batch_pipeline.py \
  --lang es --version RVR1960 --year 2025 \
  --phase 1 --provider dashscope \
  --input batch_input_es_RVR1960_2025_p1.jsonl
```

### Option C — Resume from existing files

```bash
# Resume from already-downloaded results (skip upload/submit/poll)
python3 batch_pipeline.py \
  --lang es --version RVR1960 --year 2025 \
  --phase 1 --provider dashscope \
  --input batch_input_es_RVR1960_2025_p1.jsonl \
  --results BIJOutputSet_es_RVR1960_2025_p1_results.jsonl
```

---

## Phases

| Phase | Model (DashScope) | Purpose | Genome Usage |
|---|---|---|---|
| `--phase 1` | `qwen-flash` | Linguistic scan — grammar, clarity, fidelity | ❌ No genome injection (clean signal) |
| `--phase 2` | `qwen-plus` | Theological content check — PAUSE detection | ✅ Genome-seeded prompts |
| `--phase 1,2` | Both | Combined in one request | ✅ Phase 2 only |

### Phase 1 Improvements (Recent)

**Key changes implemented:**
1. **No genome injection** — Phase 1 reads text fresh to avoid model confabulation
2. **Verbatim gate** — Filters hallucinated flags before they reach the audit log
3. **Fixed parser** — Handles Fireworks responses that start mid-`<think>` tag
4. **Source index** — Required for verbatim validation against original text

**Usage with verbatim gate:**
```bash
python3 collect_batch.py \
    --input  data/batch_input/batch_input_es_RVR1960_2025_p1.jsonl \
    --results Phase1_critic/p1_critic_es_RVR1960.jsonl \
    --lang es --version RVR1960 --year 2025 \
    --phase 1 \
    --source data/source/Devocional_year_2025_es_RVR1960.json
```

**Why these changes:**
- **Parser fix**: Fireworks responses often start mid-think, closing `</think>` only. Old regex `<think>.*?</think>` stripped nothing. New: split on `</think>` works correctly.
- **Verbatim gate**: Phase 1 models sometimes hallucinate quoted problems that don't exist in the source. Gate checks if `quoted_problem` exists verbatim in the source text before writing FLAG to audit.
- **No genome in Phase 1**: Genome patterns prime the model to find similar issues, increasing false positives. Phase 1 should provide clean, unbiased linguistic signals.

---

## Providers

All providers are defined in `config/providers.yml`. Add a new provider there — no code changes required.

```bash
# List providers and check API key status
python3 cloud_client.py --list

# Check today's token usage
python3 cloud_client.py --usage

# Test a provider (phase 1 or 2)
python3 cloud_client.py --test --phase 1
```

---

## Dry Run

Always preview before submitting:

```bash
python3 batch_pipeline.py \
  --lang es --version RVR1960 --year 2025 \
  --phase 1 --provider dashscope \
  --local Devocional_year_2025_es_RVR1960.json \
  --dry-run
```

---

## Interactive / Overnight Mode

For real-time provider routing (Groq → Cerebras → local fallback):

```bash
python3 critic_v3.py --lang tl --version ASND --year 2026
python3 critic_v3.py --lang tl --version ASND --year 2026 --overnight
```

---

## Environment Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Set API keys
export DASHSCOPE_API_KEY=sk-...
export GROQ_API_KEY=gsk_...
export FIREWORKS_API_KEY=fw_...
```

---

## Output Files

| Pattern | Location | Description |
|---|---|---|
| `batch_input_{lang}_{version}_{year}_p{N}.jsonl` | `data/batch_input/` | Batch request file sent to provider |
| `BIJOutputSet_{lang}_{version}_{year}_results.jsonl` | `data/batch_output/` | Raw provider responses |
| `critic_audit_{lang}_{version}_{year}.jsonl` | `data/audit/` | Final audit log (verdicts + reactions) |
| `genome_{lang}_{version}_{year}.json` | `data/genomes/` | Evolved pattern genome |
| `run_log_{lang}_{version}_{year}.log` | `data/logs/` | Interactive run logs |

  ├─ Phase 1 — Linguistic (qwen3:4b, ~15-20s)
  │    Native speaker scan: typos, grammar, repeated phrases, unnatural phrasing.
  │    Returns: CLEAN | FLAG
  │    Result injected into Phase 2 prompt as context.
  │
  └─ Phase 2 — Content coherence (qwen3:14b, ~100s)
       Simulated reader: prayer drift, register drift, hallucination,
       generic reflection, verse mismatch.
       Returns: OK | PAUSE
```

Both verdicts are stored in one audit record per entry.

**Phase 1 model is hardcoded** in `runner.py`:
```python
PHASE1_MODEL = "qwen3:4b"
```
`--model` CLI arg controls Phase 2 only.

---

## GEP Lifecycle

```
Run N:
  P1 scans for linguistic issues → CLEAN or FLAG (stored in audit)
  P2 reader reviews content      → OK or PAUSE

  PAUSE → quoted phrase + reasoning → GenomeFragment (CANDIDATE state)
          confidence grows with each new evidence date
          at confidence >= 0.7 → promoted to CONFIRMED
          CONFIRMED fragments injected into next run's prompt

Run N+1:
  genome-seeded prompt → reader arrives knowing confirmed patterns
  → fewer PAUSEs on already-known issues
  → new PAUSEs grow new fragments
```

---

## GEP Assets

### Gene (`GenomeFragment`)
One unit of accumulated reader knowledge.

| Field | Description |
|---|---|
| `id` | Unique fragment ID (`frag_xxxxxxxx`) |
| `category` | Pause category that triggered it |
| `pattern` | Reader's own words describing the issue |
| `example_quote` | Exact phrase from the entry that caused the pause |
| `evidence_dates` | All audit dates that confirmed this pattern |
| `confidence` | `0.4` seed → `+0.15` per new date → max `0.95` |
| `state` | `candidate` (< 0.7) → `confirmed` (≥ 0.7) |

**Promotion rule:** `candidate` → `confirmed` at `confidence >= 0.7`
(approximately 3 independent evidence dates).
Only `confirmed` fragments appear in **Phase 2** prompts (Phase 1 runs genome-free).

### Genome Corpus Scanner

The genome can now be used as a **deterministic Python search tool** across the full devotional corpus:

```python
from genome import ensure_genome, corpus_scan, print_corpus_scan_report
from pathlib import Path

# Load genome
genome = ensure_genome("es", "RVR1960", 2025)

# Scan source files for known genome patterns
hits = corpus_scan(
    genome=genome,
    source_paths=[
        Path("data/source/Devocional_year_2025_es_RVR1960.json"),
        Path("data/source/Devocional_year_2026_es_RVR1960.json"),
    ],
    categories=["grammar", "typo"],  # or None for all categories
)

# Print formatted report
print_corpus_scan_report(hits, genome)
```

**Output example:**
```
════════════════════════════════════════════════════════
  🧬 Genome Corpus Scan — 12 total hits
════════════════════════════════════════════════════════

  [typo] "compromizo"
  Pattern: Spanish typo - should be "compromiso"
  Hits: 3
    • 2025-03-15 [reflexion] es_RVR1960_2025-03-15
      ...de nuestro compromizo con Dios y con...
    • 2025-07-22 [oracion] es_RVR1960_2025-07-22
      ...renueva mi compromizo diario contigo...

  [grammar] "ha sido bendecidos"
  Pattern: Agreement error - "ha sido" (singular) + "bendecidos" (plural)
  Hits: 2
    • 2025-05-10 [reflexion] es_RVR1960_2025-05-10
      ...pueblo ha sido bendecidos por el Señor...
```

This replaces the old role of genome as a "hint" to the model in Phase 1. Now:
- **Phase 1**: No genome injection → clean linguistic signal
- **Phase 2**: Genome-seeded prompts → content coherence
- **Python scan**: Deterministic search across corpus → discover patterns at scale

### Capsule
A bundled, language-ready critic configuration.
Everything needed to spin up a fresh critic for a new language/version.

| Field | Description |
|---|---|
| `language` | Language code (`tl`, `ar`, `hi`, ...) |
| `country` | Native speaker origin (`Philippines`, `Lebanon`, ...) |
| `known_versions` | Bible versions for this language (`["ASND", "ADB"]`) |
| `field_labels` | Field name translations in the target language |
| `seed_fragments` | Pre-loaded genome fragments (empty for new languages) |
| `state` | `draft` → `validated` after first successful overnight run |

---

## Usage

### Standard runs

```bash
# Overnight batch — full two-phase run
python3 critic_v3.py --lang tl --version ASND --year 2026 --mode overnight

# Phase 1 only — fast linguistic scan, no content review
python critic_v3.py --lang tl --version ASND --year 2026 --phase 1

# Local file — no GitHub fetch
python critic_v3.py --lang pt --version ARC --year 2025 --local ./Devocional_year_2025_pt_ARC.json

# Interactive — entry by entry, approve/skip
python critic_v3.py --lang es --version NVI --year 2025 --mode interactive

# Resume from a specific date
python critic_v3.py --lang tl --version ASND --year 2026 --start-date 2026-06-01
```

### Inspection commands

```bash
# View genome — accumulated fragments with confidence and state
python critic_v3.py --genome tl ASND 2026

# Audit report — summary of OK / PAUSE / errors
python critic_v3.py --lang tl --version ASND --year 2026 --report

# List all known lang/version combinations
python critic_v3.py --list-files
```

---

## What to Expect Per Run

### Console output (overnight mode)
```
════════════════════════════════════════════════════════════
  🌙 OVERNIGHT RUN — 20260419_053935
  P1 model : qwen3:4b
  P2 model : qwen3:14b
  Pending  : 365 entries
  Genome   : 3 fragments
════════════════════════════════════════════════════════════

  [1/365] 2026-01-01 | tl_ASND_2026-01-01
  [P1] linguistic... ✅ CLEAN (18s)
  [P2] content...    ✅ OK | confidence: 0.95 | elapsed: 112s

  [2/365] 2026-01-02 | tl_ASND_2026-01-02
  [P1] linguistic... 🔤 FLAG (16s)
  [P2] content...
  [P1] FLAG | repeated phrase
  [P2] 🔶 PAUSE [prayer_drift] | confidence: 0.88 | elapsed: 98s
  pause   : "tagumpay sa trabaho"
  genome  : frag_a1b2c3d4
  ETA: ~14:22:00 for 363 remaining  (avg 114s/entry)
```

### End of run summary
```
  ✅ OK        : 287
  🔶 PAUSE (P2): 61
  🔤 FLAG  (P1): 38
  ⚠️  Errors   : 0
```

### Audit record (one JSONL line per entry)
```json
{
  "date": "2026-01-02",
  "verdict": "PAUSE",
  "reaction": "The prayer asked for career success...",
  "quoted_pause": "tagumpay sa trabaho",
  "category": "prayer_drift",
  "confidence": 0.88,
  "phase1_verdict": "FLAG",
  "phase1_issue": "Phrase repeated across paragraphs",
  "phase1_quoted": "upang hindi kayo madaig",
  "phase1_confidence": 0.85,
  "phase1_raw": "<think>...</think>",
  "genome_fragment_id": "frag_a1b2c3d4",
  "asset_id": "sha256:abc123..."
}
```

---

## Validation Commands

### After an overnight run — check results

```bash
# 1. Check run finished cleanly
tail -5 run_log_tl_ASND_2026.log

# 2. Quick audit summary — total, FLAG rate, PAUSE rate
python3 -c "
import json
from collections import Counter
records = []
with open('critic_audit_tl_ASND_2026.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            try: records.append(json.loads(line))
            except: pass
p2 = Counter(r.get('verdict') for r in records)
p1 = Counter(r.get('phase1_verdict') for r in records)
print(f'Total: {len(records)}')
print(f'P2 → {dict(p2)}')
print(f'P1 → {dict(p1)}')
"

# 3. Check no None phase1_verdicts remain (bad runs re-queued)
python3 -c "
import json
nones = []
with open('critic_audit_tl_ASND_2026.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            r = json.loads(line)
            if r.get('phase1_verdict') is None:
                nones.append(r['date'])
print(f'None verdicts: {len(nones)} → {nones}')
"

# 4. Export FLAG entries for human review
python3 -c "
import json
with open('critic_audit_tl_ASND_2026.jsonl') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        if r.get('phase1_verdict') == 'FLAG':
            print(f'{r[\"date\"]} | {r.get(\"phase1_issue\",\"\")[:80]} | \"{r.get(\"phase1_quoted\",\"\")}\"')
"

# 5. View genome state — candidate vs confirmed fragments
python critic_v3.py --genome tl ASND 2026
```

### Pipeline test (no Ollama needed)

```bash
python test_pipeline.py
```

Expected output:
```
  Results: 15 passed / 0 failed
  🎉 All checks passed — pipeline ready.
```

---

## Output Files

| File | Description |
|---|---|
| `critic_audit_{lang}_{version}_{year}.jsonl` | Audit log — one record per entry, all phase1+phase2 fields |
| `genome_{lang}_{version}_{year}.json` | GEP genome — fragments with state (candidate/confirmed) |
| `run_log_{lang}_{version}_{year}.log` | Full run log with ETA, per-entry timing, error details |

---

## Installed Models

| Model | Size | think | Speed | Role |
|---|---|---|---|---|
| `qwen3:4b` | 2.5GB | ✅ | ~18s | Phase 1 (hardcoded) |
| `qwen3:14b` | 9.3GB | ✅ | ~100s | Phase 2 default |
| `qwen3.5:27b` | 17GB | ✅ | overnight | Phase 2 heavy |
| `qwen2.5:3b` | 1.9GB | ❌ | ~1s | Too limited for nuanced rules |

> **Note:** `qwen2.5:3b` does NOT support `think=True` — will return Bad Request.
> All `qwen3:*` models support thinking mode.

---

## Pause Categories (Phase 2)

| Category | Meaning |
|---|---|
| `prayer_drift` | Prayer disconnected from verse/reflection theme |
| `register_drift` | Tone too academic or too casual for the reader |
| `generic_reflection` | Reflection could apply to any verse — no specific connection |
| `hallucination` | Invented attribution, citation, or detail |
| `verse_mismatch` | Quoted verse doesn't match the reference |
| `typo` | Spelling or grammar error (also caught by Phase 1) |
| `name_error` | Biblical name misspelled or wrong |
| `other` | Anything else that caused a reader pause |
