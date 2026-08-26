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
- **We now use this** — `FireworksAdapter.submit()` passes `system_prompt=requests[0].system_prompt`
  to `self._client.submit(...)`, and `batch_submit.py` builds each dataset line as a
  short per-day `user` message only (verse + topic), with the shared persona/instructions
  sent once at job level via `build_system_prompt()`. Confirmed working in production —
  see the real run data below (44% of input tokens served from cache).

## Real run: en/KJV, gemma-4-31b-it, 359 rows (2026-08-26)

Job `devocionales-2027-08-20260826-170447`, dates 2027-08-08 → 2028-07-31.

**Lifecycle timestamps (from Fireworks dashboard):**

| Phase | Timestamp | Duration |
|---|---|---|
| Created | 17:04:50 | — |
| Validated | 17:05:50 | 1 min (create → validate) |
| Started running | 17:13:50 | 8 min (validate → start — queue wait) |
| Completed | 17:17:50 | 4 min (start → complete — actual inference) |
| **Total wall time** | | **13 min** |

**Token usage (from Fireworks dashboard):**

| Type | Tokens | Avg/row (359 rows) |
|---|---|---|
| Input (new) | 110.5K | ~308 |
| Input (cached) | 87.0K | ~242 |
| Output | 717.8K | ~1,999 |
| **Total input** | **197.5K** | ~550 |

44% of input tokens were served from cache (the shared system prompt), so the
per-row cost only scales with the ~308 new tokens/row (verse + topic), not the
full persona/instructions block repeated 359 times.

Throughput during the 4-minute RUNNING phase: ~2,991 output tok/sec, ~1.5 rows/sec.
Most of total wall time (8 of 13 min) was queue wait before running started, not
compute — plan batch timing accordingly rather than assuming near-instant start.

Result: 359/359 built, 14 phase1 validation warnings (non-blocking), 0 errors.
