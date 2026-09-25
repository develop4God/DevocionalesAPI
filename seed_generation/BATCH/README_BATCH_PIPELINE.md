
# DevocionalesAPI — Batch Pipeline (provider-agnostic)

See main project README: [README.md](../../README.md)

This directory contains the **hosted batch-API** generation path: it submits an entire seed file as one async batch job to a provider's native batch API (or fires parallel async requests for providers without one), then polls and collects results. Provider and model are selected via a pluggable adapter layer (`provider_adapter.py`) configured in `providers.yml`.

This is a different strategy from `seed_generation/generate_from_seed.py` at the repo root of `seed_generation/`, which generates entries one-by-one via direct/synchronous calls (e.g. Ollama, Gemini sync) with per-entry checkpointing and resume. Use this `BATCH/` pipeline when submitting to a provider with a real batch API (cheaper, async, higher latency); use `generate_from_seed.py` for local/interactive or synchronous-provider runs.

All commands below assume you run from inside `seed_generation/BATCH/`.

Key points

- `batch_submit.py` — provider-agnostic batch submitter (STEP 1).
  - `--provider` and `--model` select which adapter and model alias to use (see `providers.yml` for available providers/models, or run `--list-providers`).
  - `--dry-run`: builds provider-agnostic JSONL prompts into the output directory (no API calls).
  - `--dry-run-full`: prints the exact wire payload that would be sent to the provider for the first seed entry (no API calls, no uploads). Useful to inspect adapter formatting.
  - `--list-providers`: lists all configured providers and models from `providers.yml`, no API call.

- `submit_batch()` in `batch_submit.py` builds `BatchRequest` objects and calls the adapter's `submit()` method. The adapter abstracts provider details (Fireworks, Gemini, Anthropic, etc.).

- `batch_collect.py` — STEP 2: poll a submitted batch job and collect results once complete.
- `batch_repair.py` — repair/regenerate failed entries from a batch run.
- `providers.yml` — single source of truth for provider config (API key env var, batch strategy, models, costs, defaults). Add a new provider here with zero code changes.
- `pipeline_shared.py` — shared helpers used across submit/collect/repair.
- `batch_common/` — shared batch-state and file-format utilities.
- `FIREWORKS_BATCH_API.md` — notes specific to the Fireworks batch API integration.

CLI examples

Dry-run (write agnostic prompts to output dir):
```bash
cd seed_generation/BATCH
python batch_submit.py \
  --seed ../2025/seeds/FIL/seed_fil_ASND_for_2025.json \
  --lang fil --version ASND --output ../2025/yearly_devotionals/FIL \
  --dry-run
```

Dry-run full (show exact provider wire payload for 1 entry — no API call):
```bash
cd seed_generation/BATCH
python batch_submit.py \
  --seed ../2025/seeds/FIL/seed_fil_ASND_for_2025.json \
  --lang fil --version ASND \
  --provider gemini --model gemini-2.5-flash \
  --dry-run-full
```

List available providers and models:
```bash
cd seed_generation/BATCH
python batch_submit.py --list-providers
```

Submit (real run — submits via configured provider adapter, costs money):
```bash
cd seed_generation/BATCH
python batch_submit.py \
  --seed ../2025/seeds/FIL/seed_fil_ASND_for_2025.json \
  --lang fil --version ASND --output ../2025/yearly_devotionals/FIL \
  --provider gemini --model gemini-2.5-flash
```

Collect results once the batch job is complete:
```bash
cd seed_generation/BATCH
python batch_collect.py --state batch_state_fil_ASND_gemini_<timestamp>.json
```

Notes and recommendations

- `--dry-run-full` does not write files and does not require `--output`.
- If an adapter does not implement `_to_jsonl_line()`, `--dry-run-full` falls back to printing the agnostic prompt and a warning — inspect that adapter's `submit()` method manually.
- State file saved as: `batch_state_<lang>_<version>_<provider>_<timestamp>.json`. Keep this file — `batch_collect.py` and `batch_repair.py` need it to find and process the job.
- Dry-run file saved as: `prompts_<lang>_<version>_<timestamp>.jsonl` (in the output dir).
- Always confirm with the user before submitting a real (non-dry-run) batch job — it costs money.

Files of interest

- `batch_submit.py` — build and submit batches (provider-agnostic).
- `provider_adapter.py` — adapter registry and adapter implementations.
- `providers.yml` — provider/model configuration.
- `batch_collect.py` — collect/poll batch results.
- `batch_repair.py` — repair or regenerate failed entries.
- `pipeline_shared.py` — shared helpers.
- `batch_common/` — shared batch-state utilities.

See also the main project README: [README.md](../../README.md)
