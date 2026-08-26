# Fireworks Batch API — reference notes

Source: https://docs.fireworks.ai/llms.txt and
https://docs.fireworks.ai/guides/batch-inference.md (fetched 2026-08-26).

Kept here for reference when touching `provider_adapter.py`'s `FireworksAdapter`,
`batch_common/`, or `providers.yml`'s `fireworks` entry.

## Overview

- Processes large async workloads at **50% off Serverless per-token prices**,
  plus additional savings via automatic prompt caching.
- Use cases: data labeling, model distillation, evaluations, document processing.

## Model compatibility

- Any model that supports **On-Demand Deployments** in the Model Library, plus
  custom fine-tuned models built on batch-compatible bases.
- Newly added models may have a delay before batch support activates.

## Dataset (input JSONL) format

- One JSON object per line.
- Max size: 80 GiB.
- Required fields per line: `custom_id` (unique) and `body` (request params).
- `body.messages` and `body.prompt_token_ids` are mutually exclusive input modes.

Example line:
```json
{"custom_id": "request-1", "body": {"messages": [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": "What is the capital of France?"}], "max_tokens": 100}}
```

This confirms our own dataset lines (built via `batch_common.chat_request_record`)
are shaped correctly: `{"custom_id": ..., "body": {"messages": [...], "max_tokens": ...,
"temperature": ..., "response_format": ...}}`.

## Job creation

- Three methods: Fireworks UI, `firectl` CLI, or HTTP API.
- HTTP API requires: model, input dataset, optional output dataset, and inference
  parameters (`maxTokens`, `temperature`, etc.) — see `fireworks_template.py`'s
  `submit_job_request()` for our implementation of this exact shape.

## Job states

`VALIDATING → PENDING → RUNNING → COMPLETED` (or `FAILED`/`EXPIRED`).

- Jobs expire after a chosen completion window (12/24/48/72h).
- Rows completed before expiry are still billed and saved — a caller that wants
  those partial results can still `download()` with the same output dataset id.
- **Wire values are `JOB_STATE_*`** (e.g. `JOB_STATE_RUNNING`, `JOB_STATE_COMPLETED`),
  NOT the bare words shown in this summary table — confirmed against a real job's
  raw poll response. See `fireworks_template.py`'s module docstring for the
  discovery story (an earlier version of this code used the bare-word form and
  silently never matched any real state).

## System prompt optimization

- A single **job-level** system prompt can be injected across all rows that don't
  already carry their own `system` message in `body.messages` — reduces upload
  size while preserving prompt-cache effectiveness across the whole batch.
- Only works with message-based inputs, not pre-tokenized (`prompt_token_ids`) jobs.
- **We do not currently use this** — `FireworksAdapter.submit()` calls
  `self._client.submit(...)` without a `system_prompt` argument, and every one of
  our dataset lines is a single full `user` message (persona + instructions +
  verse, all inlined). This is valid per the docs (a `system` message is optional,
  not required), but it means we don't benefit from the cross-request prompt-cache
  discount a shared, byte-identical system prompt would unlock — LangGraph's
  `devotional_gen.py` in the sibling `devocionales-ai-review` project does use
  this split (shared system prompt + short per-day user message) for exactly that
  saving. Worth revisiting if per-request cost becomes a concern.
