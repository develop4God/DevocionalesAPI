"""
batch_common — vendored Fireworks batch API transport.

Copied from devocionales-ai-review/batch_common (2026-08-26) — that project's
client.py/fireworks_template.py encode real, hard-won fixes for the Fireworks
batch API's actual shape (account-scoped datasets/batchInferenceJobs, not
OpenAI's flat /files+/batches) after live 404s/400s against the wrong shape.
Stdlib-only, no external dependencies.
"""

from batch_common.client import BatchClient
from batch_common.config import (
    BatchAPIError,
    BatchProviderConfig,
    account_id_from_env,
    api_key_from_env,
)
from batch_common.jsonl import chat_request_record, read_jsonl, write_jsonl

__all__ = [
    "BatchAPIError",
    "BatchClient",
    "BatchProviderConfig",
    "account_id_from_env",
    "api_key_from_env",
    "chat_request_record",
    "read_jsonl",
    "write_jsonl",
]
