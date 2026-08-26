"""
JSONL read/write and Fireworks batch dataset-line shaping.

One place that knows the dataset input line envelope ({custom_id, body}), so a
consuming project builds only the body's messages/parameters and never
re-derives the envelope itself. Confirmed against docs.fireworks.ai/guides/
batch-inference 2026-08-14: a dataset line is exactly {"custom_id": ...,
"body": {"messages": [...], ...}} — no method/url (that was the old, wrong
OpenAI-/files+/batches-shaped envelope this module carried before the real
Fireworks API shape was confirmed), and no model field inside body (model is a
job-level field set once in submit_job_request, not per-line).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path


def write_jsonl(path: Path, records: Iterable[dict]) -> Path:
    """Write records one-JSON-object-per-line to path. Creates parents. Returns path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        )
    return path


def read_jsonl(path: Path) -> Iterator[dict]:
    """
    Yield one parsed dict per non-blank line.

    Malformed lines raise — a caller that wants to tolerate partial garbage
    (e.g. a batch results collector) should read the raw lines itself and decide
    per line, rather than having this silently drop data.
    """
    with open(Path(path), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def chat_request_record(
    custom_id: str,
    messages: list[dict],
    **body: object,
) -> dict:
    """
    Build one Fireworks batch dataset input line: {custom_id, body}.

    No model, method, or url — model is set once at job-submission time
    (see fireworks_template.submit_job_request), not per dataset line. Extra
    keyword args (max_tokens, temperature, response_format, ...) are merged into
    body alongside messages.

    A caller whose every row shares one system message should strip it from
    messages here and pass it separately to submit()'s system_prompt instead —
    Fireworks injects it as a leading system message into every row that
    doesn't already start with one, and an identical injected prefix is what
    makes prompt caching apply across the whole batch.
    """
    return {"custom_id": custom_id, "body": {"messages": messages, **body}}
