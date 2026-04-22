
# DevocionalesAPI — Batch Pipeline (provider-agnostic)

See main project README: [README.md](../README.md)

This directory contains the batch pipeline used to generate yearly devotionals from seed files. The pipeline is provider-agnostic: `batch_submit.py` now supports multiple providers via a pluggable adapter layer (`provider_adapter.py`).

Key points (updated logic)

- `batch_submit.py` — provider-agnostic batch submitter.
  - `--provider` and `--model` select which adapter and model alias to use.
  - `--dry-run`: builds provider-agnostic JSONL prompts into the output directory (no API calls).
  - `--dry-run-full`: prints the exact wire payload that would be sent to the provider for the first seed entry (no API calls, no uploads). Useful to inspect adapter formatting (Gemini adapter implements `_to_jsonl_line`).

- `submit_batch()` in `batch_submit.py` builds `BatchRequest` objects and calls the adapter's `submit()` method. The adapter abstracts provider details (Gemini, Anthropic, etc.).

- `batch_collect.py` / `batch_repair_failed.py` / `validation_helper.py` — collection, repair, and validation utilities remain in this folder and operate on the state and output files produced by `batch_submit.py`.

CLI examples

Dry-run (write agnostic prompts to output dir):
```bash
python batch_submit.py \
  --seed 2025/seeds/seed_tl_ADB_for_2025.json \
  --lang tl --version ADB --output 2025/yearly_devotionals/TL \
  --dry-run
```

Dry-run full (show exact provider wire payload for 1 entry — no API call):
```bash
python batch_submit.py \
  --seed ../2025/seeds/TL/seed_tl_ADB_for_2025.json \
  --lang tl --version ADB \
  --provider gemini --model gemini-2.5-flash \
  --dry-run-full
```

Submit (real run — submits via configured provider adapter):
```bash
python batch_submit.py \
  --seed 2025/seeds/seed_tl_ADB_for_2025.json \
  --lang tl --version ADB --output 2025/yearly_devotionals/TL \
  --provider gemini --model gemini-2.5-flash
```

Notes and recommendations

- `--dry-run-full` does not write files and does not require `--output`.
- If an adapter does not implement `_to_jsonl_line()`, `--dry-run-full` will fall back to printing the agnostic prompt and a warning — inspect that adapter's `submit()` method manually.
- For Gemini, the adapter exposes `_to_jsonl_line()` and the full wire payload will be printed.

Files of interest

- `batch_submit.py` — build and submit batches (provider-agnostic).
- `provider_adapter.py` — adapter registry and adapter implementations.
- `batch_collect.py` — collect/poll batch results.
- `batch_repair_failed.py` — repair or regenerate failed entries.
- `validation_helper.py` — validate generated devotionals.

See also the main project README: [README.md](../README.md)
