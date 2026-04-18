# GEP Critic v3 — Simulated Reader

## Philosophy
Not an editor. Not a theologian. A faithful reader who opens the app every morning.
If something feels wrong, they notice it. That reaction is the gold.

## Architecture (SOLID)

```
critic_v3.py       ← entry point, CLI only
runner.py          ← orchestrates interactive / overnight modes
prompts.py         ← builds LLM prompts (simulated reader persona)
ollama_client.py   ← Ollama API calls, retry logic
source.py          ← fetches and parses devotional JSON
audit.py           ← reads/writes JSONL audit log
genome.py          ← GEP genome: load, absorb, persist
models.py          ← all data structures (single source of truth)
```

## GEP Lifecycle

```
Run N:
  reader reviews entries
  → OK:    logged, skipped by genome
  → PAUSE: quoted phrase + reasoning → genome fragment
           confidence grows with each new evidence date
           fragment appears in next run's prompt as known pattern

Run N+1:
  genome-seeded prompt → reader arrives knowing prior patterns
  → fewer pauses on already-known issues
  → new pauses grow new fragments
```

## Usage

```bash
# Overnight batch (recommended — 27b model)
python critic_v3.py --lang pt --version ARC --year 2025 --mode overnight

# Local file (faster, no GitHub fetch)
python critic_v3.py --lang pt --version ARC --year 2025 --local ./Devocional_year_2025_pt_ARC.json

# Interactive (entry by entry, approve/skip)
python critic_v3.py --lang es --version NVI --year 2025 --mode interactive

# View genome
python critic_v3.py --genome pt ARC 2025

# Audit report only
python critic_v3.py --lang pt --version ARC --year 2025 --report

# List all lang/version combinations
python critic_v3.py --list-files
```

## Output files

| File | Description |
|---|---|
| `critic_audit_{lang}_{version}_{year}.jsonl` | Audit log — one record per entry |
| `genome_{lang}_{version}_{year}.json` | GEP genome — accumulated reader knowledge |

## Pause categories

| Category | Meaning |
|---|---|
| `verse_mismatch` | Quoted verse doesn't match the reference |
| `typo` | Spelling or grammar error |
| `name_error` | Biblical name misspelled or wrong |
| `prayer_drift` | Prayer disconnected from verse/reflection |
| `hallucination` | Invented attribution, citation, or detail |
| `register_drift` | Tone too academic or too casual for the reader |
| `other` | Anything else that caused a pause |
