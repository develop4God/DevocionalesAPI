# DevocionalesAPI

This repository generates biblical devotionals using large language models. It contains server components for single-item generation and a provider-agnostic batch pipeline for yearly/devotional generation from seed files.

Quick links

- Batch pipeline documentation: [seed_generation/README_BATCH_PIPELINE.md](seed_generation/README_BATCH_PIPELINE.md)
- Batch scripts and adapters: `seed_generation/`
- Server and client: `API_Server.py`, `API_Client.py`

Overview

- `API_Server.py`: FastAPI server that calls an LLM adapter to generate devotionals.
- `API_Client.py`: Simple client for iterative generation via the server.
- `seed_generation/`: Batch pipeline to submit seed JSON files to provider adapters, collect results, repair failures, and validate outputs.

Batch highlights

- Provider-agnostic submission via `batch_submit.py` and `provider_adapter.py`.
- `--dry-run`: write provider-agnostic prompt JSONL for review (no API calls).
- `--dry-run-full`: print the exact wire payload for one seed entry (no API calls) to inspect adapter formatting.

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
