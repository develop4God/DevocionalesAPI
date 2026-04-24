"""
batch_client.py — GEP Batch API Client
Single responsibility: OpenAI-compatible batch API operations.

All configuration (base_url, model, endpoint, completion_window) is read from
providers.yml.  No values are hardcoded here.

Public API:
    client = BatchClient("dashscope_batch_phase1")
    file_id  = client.upload(Path("batch_input.jsonl"))
    batch_id = client.submit(file_id)
    out_fid  = client.poll(batch_id)
    path     = client.download(out_fid, Path("results.jsonl"))

Properties:
    client.model        → model name from providers.yml
    client.provider_id  → provider id string
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from cloud_client import _load_config  # single config source of truth


# ── Exceptions ────────────────────────────────────────────────────────────────

class BatchAPIError(RuntimeError):
    """Raised on non-retryable batch API failures."""


# ── Config helpers ────────────────────────────────────────────────────────────

def _provider_cfg(provider_id: str) -> dict:
    """Look up a provider by id and verify it supports batch."""
    for p in _load_config()["providers"]:
        if p["id"] == provider_id:
            if not p.get("batch", {}).get("supported"):
                raise BatchAPIError(
                    f"Provider '{provider_id}' does not have batch.supported=true "
                    f"in providers.yml."
                )
            return p
    raise BatchAPIError(f"Provider '{provider_id}' not found in providers.yml.")


def _api_key(provider: dict) -> str:
    env_var = provider.get("env_var", "")
    key = os.environ.get(env_var, "")
    if not key:
        raise BatchAPIError(
            f"Environment variable {env_var!r} is not set.\n"
            f"Run: export {env_var}=<your_api_key>"
        )
    return key


# ── Client ────────────────────────────────────────────────────────────────────

class BatchClient:
    """
    OpenAI-compatible batch client.
    All configuration is driven by providers.yml — open for extension,
    closed for modification (add new providers via yml, not code).
    """

    def __init__(self, provider_id: str) -> None:
        self._cfg   = _provider_cfg(provider_id)
        self._key   = _api_key(self._cfg)
        self._base  = self._cfg["base_url"].rstrip("/")
        self._batch = self._cfg["batch"]

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def model(self) -> str:
        return self._cfg["model"]

    @property
    def provider_id(self) -> str:
        return self._cfg["id"]

    # ── Batch operations ──────────────────────────────────────────────────

    def upload(self, file_path: Path) -> str:
        """
        Upload a JSONL file for batch inference via multipart/form-data.
        Includes the required 'purpose: batch' field.
        Returns file_id.
        """
        boundary = "GEPBatch01"
        file_bytes = file_path.read_bytes()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="purpose"\r\n\r\n'
            f"batch\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            f"Content-Type: application/jsonl\r\n\r\n"
        ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            f"{self._base}/files",
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Authorization", f"Bearer {self._key}")

        resp = self._do(req)
        file_id = resp.get("id") or resp.get("file_id")
        if not file_id:
            raise BatchAPIError(f"Upload succeeded but no file_id in response: {resp}")
        return file_id

    def submit(self, file_id: str) -> str:
        """
        Create a batch job using the OpenAI-compatible POST /v1/batches endpoint.
        Returns batch_id.
        """
        payload = {
            "input_file_id": file_id,
            "endpoint": self._batch.get("endpoint", "/v1/chat/completions"),
            "completion_window": self._batch.get("completion_window", "24h"),
        }
        resp = self._post_json(f"{self._base}/batches", payload)
        batch_id = resp.get("id") or resp.get("batch_id")
        if not batch_id:
            raise BatchAPIError(f"Submit succeeded but no batch_id in response: {resp}")
        return batch_id

    def poll(
        self,
        batch_id: str,
        interval: int = 30,
        timeout: int = 86_400,
    ) -> str:
        """
        Poll GET /v1/batches/{batch_id} until status is terminal.
        Returns output_file_id when status == 'completed'.
        Raises BatchAPIError on failed/expired/cancelled.
        Raises TimeoutError if timeout is exceeded.
        """
        url = f"{self._base}/batches/{batch_id}"
        deadline = time.monotonic() + timeout
        terminal = {"completed", "failed", "expired", "cancelled"}

        while time.monotonic() < deadline:
            data = self._get_json(url)
            status = data.get("status", "unknown")
            counts = data.get("request_counts", {})
            total  = counts.get("total", "?")
            done   = counts.get("completed", "?")
            print(f"    [{status}]  {done}/{total} completed", flush=True)

            if status == "completed":
                fid = data.get("output_file_id")
                if not fid:
                    raise BatchAPIError(
                        f"Batch completed but output_file_id is missing: {data}"
                    )
                return fid

            if status in terminal:
                raise BatchAPIError(
                    f"Batch ended with status='{status}': {data}"
                )

            time.sleep(interval)

        raise TimeoutError(f"Batch polling timed out after {timeout}s")

    def download(self, file_id: str, dest: Path) -> Path:
        """Download file content to dest path. Returns dest."""
        req = urllib.request.Request(
            f"{self._base}/files/{file_id}/content",
            headers={"Authorization": f"Bearer {self._key}"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            dest.write_bytes(resp.read())
        return dest

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
