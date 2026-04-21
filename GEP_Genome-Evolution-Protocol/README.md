# GEP Critic v3 — Simulated Reader

## Philosophy
Not an editor. Not a theologian. A faithful reader who opens the app every morning.
If something feels wrong, they notice it. That reaction is the gold.

---

## Architecture (SOLID)

```
critic_v3.py       ← entry point, CLI only
runner.py          ← orchestrates interactive / overnight modes
prompts.py         ← builds LLM prompts (simulated reader persona)
ollama_client.py   ← Ollama API calls, retry logic, think=True streaming
source.py          ← fetches and parses devotional JSON
audit.py           ← reads/writes JSONL audit log
genome.py          ← GEP genome: load, absorb, persist, promote
models.py          ← all data structures (single source of truth)
```

---

## Two-Phase Flow

Each entry is processed in two independent phases:

```
Entry
  │
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
Only `confirmed` fragments appear in future prompts.

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
