"""
Fireworks batch inference API shape: URL paths, request/response bodies.

Every Fireworks-specific detail lives here — account-scoped resource paths
(accounts/{account_id}/datasets, .../batchInferenceJobs), payload field names
(datasetId, inputDatasetId, systemPrompt, inferenceParameters), and job states
— account-scoped paths and payload fields confirmed against the full
docs.fireworks.ai/guides/batch-inference page 2026-08-14, after an earlier
OpenAI-shaped client (POST /files, /batches) was proven wrong by a real 404
from the live API.

Job states are JOB_STATE_* (e.g. "JOB_STATE_RUNNING", "JOB_STATE_COMPLETED"),
NOT the bare uppercase words (VALIDATING/RUNNING/COMPLETED/...) shown in that
page's "Job states" reference table — that table turned out to be a simplified
summary, not the literal wire value. Confirmed directly from a real job's
poll response (2026-08-14, job native-reader-es-rvr1960-halfyear-job returned
state="JOB_STATE_RUNNING") after polling with the bare-word version silently
never matched any known state and would have spun until timeout on both
success and failure.

Pure functions only: no network I/O, no urllib. client.py (the transport layer)
imports this module for "what URL, what body, what does this response mean" and
never hardcodes a Fireworks path or field name itself — the same domain-logic-vs-
transport split this project's own LangGraph package uses between domain/ and
nodes/. A future second batch provider with a different shape gets its own
sibling template module, not a branch inside this one or inside client.py.
"""

from __future__ import annotations

import json

# JOB_STATE_ prefix confirmed against a real poll response (see module
# docstring) — an earlier version of this module used the bare uppercase words
# from the docs' "Job states" table, which never matched the real wire value.
COMPLETED_STATE = "JOB_STATE_COMPLETED"
_FAILURE_STATES = frozenset({"JOB_STATE_FAILED", "JOB_STATE_EXPIRED"})


def accounts_url(base_url: str, account_id: str) -> str:
    """The account-scoped root every Fireworks batch endpoint hangs off of."""
    return f"{base_url.rstrip('/')}/accounts/{account_id}"


def create_dataset_request(
    base_url: str, account_id: str, dataset_id: str, example_count: int
) -> tuple[str, dict]:
    """
    (url, json body) for POST .../datasets — declares an empty dataset resource.

    example_count (the number of lines the caller is about to upload) is
    required by the live API for a userUploaded dataset — confirmed by a real
    400 ("example_count is required for uploaded datasets") the docs' own
    request-body example (docs.fireworks.ai/guides/batch-inference,
    /api-reference/create-dataset) does not show as required; that page's
    schema even marks exampleCount read-only. The server's own validation
    error is the authority here, not the doc page.
    """
    url = f"{accounts_url(base_url, account_id)}/datasets"
    body = {
        "datasetId": dataset_id,
        "dataset": {"userUploaded": {}, "exampleCount": str(example_count)},
    }
    return url, body


def upload_dataset_url(base_url: str, account_id: str, dataset_id: str) -> str:
    """URL for POST .../datasets/{dataset_id}:upload — multipart file upload."""
    return f"{accounts_url(base_url, account_id)}/datasets/{dataset_id}:upload"


def submit_job_request(
    base_url: str,
    account_id: str,
    model: str,
    job_id: str,
    input_dataset_id: str,
    output_dataset_id: str,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    extra_body: dict | None = None,
) -> tuple[str, dict]:
    """
    (url, json body) for POST .../batchInferenceJobs?batchInferenceJobId={job_id}.

    system_prompt is set at the job level (Fireworks' documented shape for a
    dataset where every row shares one system message) rather than repeated in
    every record's messages — smaller upload, keeps prompt-caching intact per
    Fireworks' own guidance.

    extra_body maps to inferenceParameters.extraBody, the documented escape
    hatch for request fields the schema doesn't otherwise expose (e.g.
    reasoning_effort, chat_template_kwargs) — confirmed against the live API
    reference 2026-08-15 as a job-level *string* field (a JSON-encoded object),
    applied uniformly to every row. NOT a per-record field: an earlier attempt
    to set "extra_body" inside each JSONL row's own body (rather than here, at
    submission) got a real 400 "Extra inputs are not permitted, field:
    'extra_body'" on every row — the record schema has no such field at all.
    """
    url = (
        f"{accounts_url(base_url, account_id)}/batchInferenceJobs"
        f"?batchInferenceJobId={job_id}"
    )
    inference_parameters = {
        k: v
        for k, v in {
            "maxTokens": max_tokens,
            "temperature": temperature,
            "topP": top_p,
            "extraBody": json.dumps(extra_body) if extra_body is not None else None,
        }.items()
        if v is not None
    }
    body: dict = {
        "model": model,
        "inputDatasetId": f"accounts/{account_id}/datasets/{input_dataset_id}",
        "outputDatasetId": f"accounts/{account_id}/datasets/{output_dataset_id}",
    }
    if system_prompt is not None:
        body["systemPrompt"] = system_prompt
    if inference_parameters:
        body["inferenceParameters"] = inference_parameters
    return url, body


def job_status_url(base_url: str, account_id: str, job_id: str) -> str:
    """URL for GET .../batchInferenceJobs/{job_id} — poll target."""
    return f"{accounts_url(base_url, account_id)}/batchInferenceJobs/{job_id}"


def job_delete_url(base_url: str, account_id: str, job_id: str) -> str:
    """URL for DELETE .../batchInferenceJobs/{job_id} — cancels the job."""
    return f"{accounts_url(base_url, account_id)}/batchInferenceJobs/{job_id}"


def parse_job_status(response: dict) -> str:
    """
    The job's state from one poll response.

    Just the state — the docs' own worked example for downloading results uses
    the caller-chosen output_dataset_id directly (the same value passed to
    submit_job_request() above), not a value read back from a poll response.
    There's no documented outputDatasetId field in the polling response body, so
    this doesn't guess at parsing one out.

    "JOB_STATE_EXPIRED" (job exceeded its chosen 12/24/48/72h window) is treated
    as a failure state by is_failure_state(), NOT completed — even though the
    docs note completed rows up to that point are saved and billed under the
    caller's own output_dataset_id. A caller that wants those partial results
    can still call download() with the same output_dataset_id they submitted;
    this function itself doesn't attempt that recovery.

    Status field name (response["state"]) and its JOB_STATE_* values are
    confirmed against a real job's raw poll response, 2026-08-14 (see this
    module's own docstring) — not just the docs page, which showed the request
    shape for polling but not a full example response body, and whose "Job
    states" table used bare words that turned out not to be the real wire value.
    """
    return response.get("state", "unknown")


def is_failure_state(state: str) -> bool:
    return state in _FAILURE_STATES


def download_manifest_request(
    base_url: str, account_id: str, output_dataset_id: str
) -> str:
    """URL for POST .../datasets/{output_dataset_id}:getDownloadEndpoint."""
    return (
        f"{accounts_url(base_url, account_id)}/datasets/"
        f"{output_dataset_id}:getDownloadEndpoint"
    )


def parse_download_manifest(response: dict) -> dict[str, str]:
    """filename -> signed URL, from a getDownloadEndpoint response."""
    return response.get("filenameToSignedUrls") or {}
