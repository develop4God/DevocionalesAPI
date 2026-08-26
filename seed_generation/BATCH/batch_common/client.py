"""
Generic batch inference HTTP transport: GET / POST-JSON / POST-multipart, auth
headers, error wrapping.

This file knows nothing about any provider's specific URL shape or payload
fields — that belongs to a template module (see fireworks_template.py) which
this class calls for "what URL, what body, what does this response mean". A
second batch provider with a different shape gets its own template module and
its own BatchClient-like wrapper (or a provider_id branch here dispatching to
the right template, once a second one actually exists) — not a hardcoded
Fireworks path baked into this transport layer.

Usage:
    client = BatchClient(cfg)
    client.create_dataset("my-batch-input", example_count=365)
    client.upload("my-batch-input", Path("batch_input.jsonl"))
    client.submit(
        "my-batch-input", output_dataset_id="my-batch-output",
        job_id="my-batch-job", system_prompt="...",
    )
    client.poll("my-batch-job")  # blocks until COMPLETED or raises
    paths = client.download("my-batch-output", Path("results_dir"))
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from batch_common import fireworks_template as fw
from batch_common.config import (
    BatchAPIError,
    BatchProviderConfig,
    account_id_from_env,
    api_key_from_env,
)


class BatchClient:
    """
    Batch client for an account-scoped provider (Fireworks today).

    All configuration arrives through the BatchProviderConfig — this class is
    open for extension (a new provider is a new config) and closed for
    modification (no per-provider branch lives here; shape decisions are
    delegated to fireworks_template).
    """

    def __init__(self, cfg: BatchProviderConfig) -> None:
        self._cfg = cfg
        self._key = api_key_from_env(cfg)
        self._account_id = account_id_from_env(cfg)
        self._base = cfg.base_url

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def model(self) -> str:
        return self._cfg.model

    @property
    def provider_id(self) -> str:
        return self._cfg.provider_id

    # ── Batch operations ──────────────────────────────────────────────────

    def create_dataset(self, dataset_id: str, example_count: int) -> str:
        """
        Declares an empty dataset resource for a subsequent upload() to fill.

        example_count: the number of lines the caller is about to upload via
        upload() — required by the live API (see fireworks_template.
        create_dataset_request's docstring for why).
        """
        url, body = fw.create_dataset_request(
            self._base, self._account_id, dataset_id, example_count
        )
        self._post_json(url, body)
        return dataset_id

    def upload(self, dataset_id: str, file_path: Path) -> str:
        """Multipart-uploads a JSONL file's bytes into an already-created dataset."""
        boundary = "BatchCommon01"
        file_bytes = Path(file_path).read_bytes()
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; '
                f'filename="{Path(file_path).name}"\r\n'
                f"Content-Type: application/jsonl\r\n\r\n"
            ).encode()
            + file_bytes
            + f"\r\n--{boundary}--\r\n".encode()
        )

        req = urllib.request.Request(
            fw.upload_dataset_url(self._base, self._account_id, dataset_id),
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Authorization", f"Bearer {self._key}")
        self._do(req)
        return dataset_id

    def submit(
        self,
        input_dataset_id: str,
        output_dataset_id: str,
        job_id: str,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        extra_body: dict | None = None,
    ) -> str:
        """
        Creates the batch inference job. Returns job_id (caller-chosen).

        extra_body defaults to the provider config's own extra_body (e.g.
        reasoning_effort) when the caller doesn't pass one explicitly — so a
        provider-level setting in providers.yml applies without every call site
        needing to read and forward it itself. Pass extra_body explicitly to
        override (including {} to suppress the provider's default for one call).
        """
        if extra_body is None:
            extra_body = self._cfg.extra_body
        url, payload = fw.submit_job_request(
            self._base,
            self._account_id,
            self._cfg.model,
            job_id,
            input_dataset_id,
            output_dataset_id,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            extra_body=extra_body,
        )
        self._post_json(url, payload)
        return job_id

    def poll(
        self,
        job_id: str,
        interval: int = 30,
        timeout: int = 86_400,
    ) -> str:
        """
        Poll until the job reaches a terminal state. Returns the terminal state
        string (fireworks_template.COMPLETED_STATE, or a failure state — see
        is_failure_state below).

        Does NOT return an output_dataset_id: the docs' own worked example
        downloads using the same output_dataset_id the caller already chose and
        passed to submit() — there's no documented field carrying it back in the
        polling response, so this doesn't invent one. Call download() with that
        same value once poll() returns COMPLETED_STATE.

        Raises BatchAPIError on a failure state, TimeoutError past timeout.
        """
        url = fw.job_status_url(self._base, self._account_id, job_id)
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            data = self._get_json(url)
            state = fw.parse_job_status(data)
            print(f"    [{state}]", flush=True)

            if state == fw.COMPLETED_STATE:
                return state

            if fw.is_failure_state(state):
                raise BatchAPIError(f"Batch job ended with state={state!r}: {data}")

            time.sleep(interval)

        raise TimeoutError(f"Batch polling timed out after {timeout}s")

    def cancel(self, job_id: str) -> None:
        """Cancels a batch inference job via DELETE .../batchInferenceJobs/{job_id}."""
        url = fw.job_delete_url(self._base, self._account_id, job_id)
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._key}"},
            method="DELETE",
        )
        self._do(req)

    def download(self, output_dataset_id: str, dest_dir: Path) -> list[Path]:
        """
        Downloads every file in a completed output dataset into dest_dir.
        Returns the list of written file paths (results + any separate error file
        Fireworks includes — never assumed to be exactly one file).
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        manifest_url = fw.download_manifest_request(
            self._base, self._account_id, output_dataset_id
        )
        # Plain GET, not POST — confirmed against a real 501 "Method Not
        # Allowed" on POST, then a real 400 on GET-with-body (the docs' curl
        # example shows `-d '{}'`, but that body is rejected by the live API;
        # a bodyless GET is what actually works).
        manifest = self._get_json(manifest_url)
        signed_urls = fw.parse_download_manifest(manifest)
        if not signed_urls:
            raise BatchAPIError(
                f"No filenameToSignedUrls in download-endpoint response: {manifest}"
            )

        written: list[Path] = []
        for filename, signed_url in signed_urls.items():
            dest = dest_dir / Path(filename).name
            req = urllib.request.Request(signed_url)
            with urllib.request.urlopen(req, timeout=300) as resp:
                dest.write_bytes(resp.read())
            written.append(dest)
        return written

    # ── HTTP internals ────────────────────────────────────────────────────

    def _post_json(self, url: str, payload: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
            },
            method="POST",
        )
        return self._do(req)

    def _get_json(self, url: str) -> dict:
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._key}"},
        )
        return self._do(req)

    def _do(self, req: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise BatchAPIError(f"HTTP {e.code}: {body[:600]}") from e
