# DevocionalesAPI

This repository generates biblical devotionals using large language models. It is a provider-agnostic, seed-driven batch pipeline: seeds (verse citations, `para_meditar`, tags) are built ahead of time, then a provider (Ollama, Gemini, etc.) generates the devotional content (`reflexion` + `oracion`) for each entry, with checkpointing so runs can resume.

Quick links

- Batch pipeline documentation: [seed_generation/BATCH/README_BATCH_PIPELINE.md](seed_generation/BATCH/README_BATCH_PIPELINE.md)
- Pipeline code: `seed_generation/`

Overview

- `seed_generation/main.py`: Interactive launcher (no flags to memorize) — generate a new seed or resume content generation.
- `seed_generation/dashboard.py`: Auto-discovers every checkpoint under `seed_generation/data/output/*/`, shows progress (done/pending/total), and resumes a run by number.
- `seed_generation/generate_from_seed.py`: Core generator — takes a seed JSON file and produces devotional content via a provider. Also runnable directly with flags (`--seed`, `--lang`, `--version`, `--provider`, `--model`, `--limit`, `--resume`).
- `seed_generation/shared/`: Prompt building, response parsing, content assembly, and provider adapters shared across the pipeline.
- `seed_generation/data/`: Seed inputs and generated output/checkpoints, organized by language.

Getting started

1. Install dependencies:
```bash
uv sync
```

2. Run the interactive launcher:
```bash
python3 -m seed_generation.main
```

3. Or run generation directly:
```bash
python3 -m seed_generation.generate_from_seed \
  --seed seed_generation/2027/seeds/DE/seed_de_LU17_for_2027.json \
  --lang de --version LU17 --provider ollama --model gemma4:26b --resume
```

4. Read the batch pipeline docs for more detail:
```bash
less seed_generation/BATCH/README_BATCH_PIPELINE.md
```
