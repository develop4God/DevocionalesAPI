# DevocionalesAPI

This repository generates biblical devotionals using large language models. It contains server components for single-item generation and a provider-agnostic batch pipeline for yearly/devotional generation from seed files.

Quick links

- **GEP (Genome Evolution Protocol)**: [GEP_Genome-Evolution-Protocol/README.md](GEP_Genome-Evolution-Protocol/README.md) — Quality assurance system with two-phase validation
- Batch pipeline documentation: [seed_generation/README_BATCH_PIPELINE.md](seed_generation/README_BATCH_PIPELINE.md)
- Batch scripts and adapters: `seed_generation/`
- Server and client: `API_Server.py`, `API_Client.py`

Overview

- `API_Server.py`: FastAPI server that calls an LLM adapter to generate devotionals.
- `API_Client.py`: Simple client for iterative generation via the server.
- `seed_generation/`: Batch pipeline to submit seed JSON files to provider adapters, collect results, repair failures, and validate outputs.
- `GEP_Genome-Evolution-Protocol/`: Quality assurance critic system using simulated readers with evolving pattern genome.

Batch highlights

- Provider-agnostic submission via `batch_submit.py` and `provider_adapter.py`.
- `--dry-run`: write provider-agnostic prompt JSONL for review (no API calls).
- `--dry-run-full`: print the exact wire payload for one seed entry (no API calls) to inspect adapter formatting.

## GEP — Quality Assurance

The **Genome Evolution Protocol (GEP)** validates devotional quality through a two-phase process:

1. **Phase 1 (Linguistic)**: Fast scan for typos, grammar, repeated phrases, and unnatural phrasing
   - No genome injection for clean, unbiased signals
   - Verbatim gate filters hallucinated flags
   - Returns: `CLEAN` or `FLAG`

2. **Phase 2 (Content)**: Deep review for prayer drift, register issues, and hallucinations
   - Genome-seeded prompts with confirmed patterns
   - Simulated reader persona (language/culture-specific)
   - Returns: `OK` or `PAUSE`

**Recent improvements (2026-04):**
- Removed genome injection from Phase 1 to prevent model confabulation
- Added verbatim gate to filter hallucinated flags before audit log
- Fixed parser for Fireworks responses starting mid-`<think>` tag
- Added `corpus_scan()` for deterministic Python-based pattern search across full corpus

See [GEP_Genome-Evolution-Protocol/README.md](GEP_Genome-Evolution-Protocol/README.md) for full documentation.

Getting started

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Read the batch pipeline docs:
```bash
less seed_generation/README_BATCH_PIPELINE.md
```

If you want, I can convert other README files to English or add more examples.
