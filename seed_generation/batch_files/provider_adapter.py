"""
provider_adapter.py
───────────────────
Provider-agnostic adapter layer for devotional batch generation.

Each adapter exposes the same interface:
  submit(requests)  → job_id (str)
  collect(job_id)   → list[RawResult]

RawResult is a simple dataclass: date_key, raw_text, error

Usage:
  from provider_adapter import load_adapter
  adapter = load_adapter(provider="gemini", model_alias="gemini-2.0-flash")
  job_id  = adapter.submit(requests)          # returns immediately
  results = adapter.collect(job_id)           # blocks until done
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────

_PROVIDERS_YML = Path(__file__).parent / "providers.yml"

def _load_providers_config() -> dict:
    with open(_PROVIDERS_YML, encoding="utf-8") as f:
        return yaml.safe_load(f)["providers"]


# ─────────────────────────────────────────────────────────────────────────────
# Data types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BatchRequest:
    """One generation request: a single seed entry."""
    date_key:    str
    custom_id:   str       # safe slug derived from date_key
    prompt:      str
    model_id:    str
    max_tokens:  int = 4096


@dataclass
class RawResult:
    """Raw result from the provider — before JSON parsing."""
    date_key:  str
    raw_text:  Optional[str] = None   # None when error
    error:     Optional[str] = None   # None when success

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.raw_text is not None


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────

class BaseAdapter(ABC):
    """All adapters must implement submit + collect."""

    def __init__(self, provider_cfg: dict, model_alias: str):
        self._cfg        = provider_cfg
        self._model_cfg  = provider_cfg["models"][model_alias]
        self._model_id   = self._model_cfg["model_id"]
        self._max_tokens = self._model_cfg.get("max_tokens", 4096)
        api_key_env      = provider_cfg["api_key_env"]
        self._api_key    = os.environ.get(api_key_env, "")
        if not self._api_key:
            raise ValueError(
                f"API key not set. Add {api_key_env!r} to your .env file."
            )

    @abstractmethod
    def submit(self, requests: list[BatchRequest]) -> str:
        """Submit all requests. Returns a job_id string."""

    @abstractmethod
    def collect(self, job_id: str, requests: list[BatchRequest]) -> list[RawResult]:
        """Block until done. Returns one RawResult per request."""

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def quality(self) -> str:
        return self._model_cfg.get("quality", "unknown")

    @property
    def batch_strategy(self) -> str:
        return self._cfg.get("batch_strategy", "unknown")


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic adapter  (native batch API)
# ─────────────────────────────────────────────────────────────────────────────

class AnthropicAdapter(BaseAdapter):
    """
    Uses Anthropic Message Batches API.
    submit() → batch_id
    collect() → polls until ended, streams JSONL results
    """

    def __init__(self, provider_cfg: dict, model_alias: str):
        super().__init__(provider_cfg, model_alias)
        try:
            import anthropic as _anthropic
        except ImportError:
            raise ImportError("Run: pip install anthropic")
        self._client = _anthropic.Anthropic(api_key=self._api_key)
        self._poll_interval = provider_cfg["defaults"].get("poll_interval_seconds", 60)

    def submit(self, requests: list[BatchRequest]) -> str:
        batch_requests = [
            {
                "custom_id": r.custom_id,
                "params": {
                    "model":      r.model_id,
                    "max_tokens": r.max_tokens,
                    "messages":   [{"role": "user", "content": r.prompt}],
                },
            }
            for r in requests
        ]
        batch = self._client.messages.batches.create(requests=batch_requests)
        print(f"INFO: Anthropic batch submitted — ID: {batch.id}")
        print(f"INFO: Status: {batch.processing_status}")
        print(f"INFO: Expires: {batch.expires_at}")
        return batch.id

    def collect(self, job_id: str, requests: list[BatchRequest]) -> list[RawResult]:
        # Build lookup: custom_id → date_key
        cid_map = {r.custom_id: r.date_key for r in requests}

        # Poll until ended
        print(f"INFO: Polling Anthropic batch {job_id}...")
        while True:
            batch = self._client.messages.batches.retrieve(job_id)
            status = batch.processing_status
            counts = batch.request_counts
            print(
                f"  status={status} | "
                f"processing={counts.processing} succeeded={counts.succeeded} "
                f"errored={counts.errored}"
            )
            if status == "ended":
                break
            if status in ("canceling", "canceled", "expired"):
                print(f"WARNING: Batch status is {status!r} — results may be partial")
                break
            time.sleep(self._poll_interval)

        # Stream results
        results: list[RawResult] = []
        for item in self._client.messages.batches.results(job_id):
            date_key = cid_map.get(item.custom_id, item.custom_id)
            if item.result.type == "succeeded":
                text = item.result.message.content[0].text.strip()
                results.append(RawResult(date_key=date_key, raw_text=text))
            else:
                results.append(RawResult(
                    date_key=date_key,
                    error=f"batch_result_{item.result.type}: {str(item.result)[:120]}",
                ))
        return results

    def generate_one(self, request: BatchRequest) -> RawResult:
        """Direct API call for repair/fallback — not a batch."""
        try:
            response = self._client.messages.create(
                model=request.model_id,
                max_tokens=request.max_tokens,
                messages=[{"role": "user", "content": request.prompt}],
            )
            return RawResult(
                date_key=request.date_key,
                raw_text=response.content[0].text.strip(),
            )
        except Exception as e:
            return RawResult(date_key=request.date_key, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Gemini adapter  (native Batch API)
# ─────────────────────────────────────────────────────────────────────────────

class GeminiBatchAdapter(BaseAdapter):
    """
    Uses Gemini Batch API via google-genai SDK.
    submit()  → uploads JSONL via File API, calls client.batches.create(),
                returns real batch job name as job_id
    collect() → polls client.batches.get() until COMPLETED/FAILED,
                downloads and parses output JSONL, returns list[RawResult]
    generate_one() → direct generate_content() call for repair
    """

    def __init__(self, provider_cfg: dict, model_alias: str):
        super().__init__(provider_cfg, model_alias)
        try:
            from google import genai as _genai
            from google.genai import types as _types
        except ImportError:
            raise ImportError("Run: pip install google-genai")
        self._genai        = _genai
        self._types        = _types
        self._client       = _genai.Client(api_key=self._api_key)
        self._poll_interval = provider_cfg["defaults"].get("poll_interval_seconds", 120)

    # ── JSONL builder ─────────────────────────────────────────────────────

    @staticmethod
    def _to_jsonl_line(request: BatchRequest) -> str:
        """
        Format one BatchRequest as a Gemini Batch API JSONL line.
        Official shape: {"key": "...", "request": {"contents": [{"parts": [{"text": "..."}], "role": "user"}]}}
        """
        record = {
            "key": request.custom_id,
            "request": {
                "contents": [
                    {
                        "parts": [{"text": request.prompt}],
                        "role": "user",
                    }
                ],
                "generationConfig": {
                    "maxOutputTokens": request.max_tokens,
                },
            },
        }
        return json.dumps(record, ensure_ascii=False)

    # ── submit ────────────────────────────────────────────────────────────

    def submit(self, requests: list[BatchRequest]) -> str:
        import tempfile

        # 1. Write JSONL to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False,
            encoding="utf-8", prefix="gemini_batch_"
        ) as tmp:
            for r in requests:
                tmp.write(self._to_jsonl_line(r) + "\n")
            tmp_path = tmp.name

        print(f"INFO: JSONL written — {len(requests)} lines → {tmp_path}")

        # 2. Upload via File API
        print("INFO: Uploading JSONL to Gemini File API...")
        uploaded_file = self._client.files.upload(
            file=tmp_path,
            config=self._types.UploadFileConfig(mime_type="application/jsonl"),
        )
        print(f"INFO: File uploaded — URI: {uploaded_file.uri}  name: {uploaded_file.name}")

        # 3. Create batch job — src must be the file name ("files/<id>"), not the URI
        job = self._client.batches.create(
            model=self._model_id,
            src=uploaded_file.name,
            config=self._types.CreateBatchJobConfig(
                display_name=f"devocionales_{requests[0].date_key[:7]}_{len(requests)}req",
            ),
        )
        print(f"INFO: Gemini batch submitted — job name: {job.name}")
        print(f"INFO: State: {job.state}")
        return job.name   # job_id = job.name (e.g. "batches/123456789")

    # ── collect ───────────────────────────────────────────────────────────

    def collect(self, job_id: str, requests: list[BatchRequest]) -> list[RawResult]:
        # Build lookup: custom_id → date_key
        cid_map = {r.custom_id: r.date_key for r in requests}

        # Poll until terminal state.
        # IMPORTANT: job.state is a JobState enum — use .name to get the string,
        # not str() which produces "JobState.JOB_STATE_SUCCEEDED" (never matches).
        TERMINAL = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED",
                    "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}
        print(f"INFO: Polling Gemini batch {job_id} every {self._poll_interval}s...")
        while True:
            job = self._client.batches.get(name=job_id)
            state = job.state.name          # ← .name, not str()
            counts = getattr(job, "request_counts", None)
            count_str = ""
            if counts:
                count_str = (
                    f" | total={getattr(counts, 'total', '?')} "
                    f"completed={getattr(counts, 'completed', '?')} "
                    f"failed={getattr(counts, 'failed', '?')}"
                )
            print(f"  state={state}{count_str}")
            if state in TERMINAL:
                break
            time.sleep(self._poll_interval)

        if job.state.name != "JOB_STATE_SUCCEEDED":
            print(f"WARNING: Batch ended with state {job.state.name} — results may be partial")

        # Per official docs: dest.file_name holds the result file name (e.g. "files/abc123").
        # client.files.download(file=<name_string>) returns raw bytes directly.
        dest = getattr(job, "dest", None)
        if dest is None:
            print("ERROR: No dest field on completed job — cannot retrieve results")
            return [RawResult(date_key=r.date_key, error="no_output_dest") for r in requests]

        result_file_name = getattr(dest, "file_name", None)
        if not result_file_name:
            print(f"ERROR: dest.file_name is empty — dest={dest!r}")
            return [RawResult(date_key=r.date_key, error="no_output_file_name") for r in requests]

        print(f"INFO: Downloading output from {result_file_name}...")

        import tempfile

        # files.download() returns bytes — write directly to temp file
        content: bytes = self._client.files.download(file=result_file_name)
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".jsonl", delete=False, prefix="gemini_out_"
        ) as out_tmp:
            out_tmp.write(content)
            out_path = out_tmp.name

        print(f"INFO: Output downloaded → {out_path}")

        # Parse output JSONL
        results: list[RawResult] = []
        missing_keys = set(cid_map.keys())

        with open(out_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"WARNING: Could not parse output line: {e}")
                    continue

                key = obj.get("key", "")
                date_key = cid_map.get(key, key)
                missing_keys.discard(key)

                # Check for API-level error in this line
                response_obj = obj.get("response", {})
                error_obj    = obj.get("error")
                if error_obj:
                    results.append(RawResult(
                        date_key=date_key,
                        error=f"gemini_error: {error_obj}",
                    ))
                    continue

                # Extract text from response
                try:
                    text = (
                        response_obj["candidates"][0]["content"]["parts"][0]["text"].strip()
                    )
                    if not text:
                        results.append(RawResult(date_key=date_key, error="empty_text"))
                    else:
                        results.append(RawResult(date_key=date_key, raw_text=text))
                except (KeyError, IndexError, TypeError) as e:
                    results.append(RawResult(
                        date_key=date_key,
                        error=f"parse_response_error: {e} | raw: {str(obj)[:120]}",
                    ))

        # Any keys not present in output file
        for missing_key in missing_keys:
            date_key = cid_map.get(missing_key, missing_key)
            print(f"WARNING: No output for key {missing_key!r} ({date_key})")
            results.append(RawResult(date_key=date_key, error="missing_from_output"))

        print(f"INFO: Parsed {len(results)} results from output JSONL")
        return results

    # ── generate_one (repair) ─────────────────────────────────────────────

    def generate_one(self, request: BatchRequest) -> RawResult:
        """Direct generate_content() call for repair — not a batch."""
        try:
            response = self._client.models.generate_content(
                model=request.model_id,
                contents=request.prompt,
                config=self._types.GenerateContentConfig(
                    max_output_tokens=request.max_tokens,
                ),
            )
            text = response.text.strip() if response.text else ""
            if not text:
                return RawResult(date_key=request.date_key, error="empty response")
            return RawResult(date_key=request.date_key, raw_text=text)
        except Exception as e:
            return RawResult(date_key=request.date_key, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Fireworks adapter  (async parallel, OpenAI-compatible endpoint)
# ─────────────────────────────────────────────────────────────────────────────

class FireworksAdapter(BaseAdapter):
    """
    Uses openai SDK pointed at Fireworks base_url.
    async_parallel strategy — same pattern as Gemini.
    """

    def __init__(self, provider_cfg: dict, model_alias: str):
        super().__init__(provider_cfg, model_alias)
        try:
            import openai as _openai
        except ImportError:
            raise ImportError("Run: pip install openai")
        base_url = provider_cfg.get("base_url") or "https://api.fireworks.ai/inference/v1"
        self._client = _openai.AsyncOpenAI(
            api_key=self._api_key,
            base_url=base_url,
        )
        self._sync_client = _openai.OpenAI(
            api_key=self._api_key,
            base_url=base_url,
        )
        self._max_parallel = provider_cfg["defaults"].get("max_parallel", 20)

    def submit(self, requests: list[BatchRequest]) -> str:
        """Serialize requests to temp file — same pattern as Gemini."""
        import tempfile
        payload = [
            {"date_key": r.date_key, "custom_id": r.custom_id,
             "prompt": r.prompt, "model_id": r.model_id, "max_tokens": r.max_tokens}
            for r in requests
        ]
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
            prefix="fireworks_job_"
        )
        json.dump(payload, tmp, ensure_ascii=False)
        tmp.close()
        print(f"INFO: Fireworks async job queued — {len(requests)} requests → {tmp.name}")
        return tmp.name

    def collect(self, job_id: str, requests: list[BatchRequest]) -> list[RawResult]:
        return asyncio.run(self._collect_async(requests))

    async def _collect_async(self, requests: list[BatchRequest]) -> list[RawResult]:
        sem = asyncio.Semaphore(self._max_parallel)

        async def _one(req: BatchRequest) -> RawResult:
            async with sem:
                try:
                    response = await self._client.chat.completions.create(
                        model=req.model_id,
                        max_tokens=req.max_tokens,
                        messages=[{"role": "user", "content": req.prompt}],
                    )
                    text = response.choices[0].message.content.strip()
                    if not text:
                        return RawResult(date_key=req.date_key, error="empty response")
                    return RawResult(date_key=req.date_key, raw_text=text)
                except Exception as e:
                    return RawResult(date_key=req.date_key, error=str(e))

        tasks = [_one(r) for r in requests]
        results = []
        done = 0
        total = len(tasks)
        for coro in asyncio.as_completed(tasks):
            result = await coro
            done += 1
            if done % 25 == 0 or done == total:
                status = "✅" if result.succeeded else "❌"
                print(f"  {status} {done}/{total} — {result.date_key}")
            results.append(result)
        return results

    def generate_one(self, request: BatchRequest) -> RawResult:
        """Sync fallback for repair."""
        try:
            response = self._sync_client.chat.completions.create(
                model=request.model_id,
                max_tokens=request.max_tokens,
                messages=[{"role": "user", "content": request.prompt}],
            )
            text = response.choices[0].message.content.strip()
            if not text:
                return RawResult(date_key=request.date_key, error="empty response")
            return RawResult(date_key=request.date_key, raw_text=text)
        except Exception as e:
            return RawResult(date_key=request.date_key, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Fireworks adapter — OpenAI batch file generator  (openai_batch_file strategy)
# ─────────────────────────────────────────────────────────────────────────────

class FireworksBatchFileAdapter(BaseAdapter):
    """
    Generates an OpenAI-format batch JSONL file for manual upload to Fireworks AI.
    Does NOT make any API calls during submit().

    batch_strategy: openai_batch_file

    Workflow:
      1. submit(requests) → writes batch_input_<lang>_<version>_<ts>.jsonl
                            to batch_files/, returns file path as job_id.
      2. Upload the file manually to Fireworks AI.
      3. Download results from Fireworks AI.
      4. collect(results_path, requests) → parses OpenAI results JSONL,
                                           returns list[RawResult].

    OpenAI batch line format produced by submit():
      {"custom_id": "...", "method": "POST", "url": "/v1/chat/completions",
       "body": {"model": "...", "messages": [...], "max_tokens": N}}

    OpenAI results line format expected by collect():
      {"id": "...", "custom_id": "...",
       "response": {"status_code": 200, "body": {"choices": [{"message": {"content": "..."}}]}},
       "error": null}
    """

    def __init__(self, provider_cfg: dict, model_alias: str):
        # Do NOT call super().__init__() — API key is not required for file generation.
        self._cfg          = provider_cfg
        self._model_cfg    = provider_cfg["models"][model_alias]
        self._model_id     = self._model_cfg["model_id"]
        self._max_tokens   = self._model_cfg.get("max_tokens", 4096)
        self._batch_endpoint = provider_cfg.get("batch_endpoint", "/v1/chat/completions")
        # API key loaded but not validated — only needed if collect() is used.
        self._api_key = os.environ.get(provider_cfg.get("api_key_env", ""), "")

    @staticmethod
    def _to_jsonl_line(request: BatchRequest, model_id: str, max_tokens: int,
                        batch_endpoint: str) -> str:
        """Produce one OpenAI batch-format JSONL line."""
        record = {
            "custom_id": request.custom_id,
            "method":    "POST",
            "url":       batch_endpoint,
            "body": {
                "model":     model_id,
                "messages":  [{"role": "user", "content": request.prompt}],
                "max_tokens": max_tokens,
            },
        }
        return json.dumps(record, ensure_ascii=False)

    def submit(self, requests: list[BatchRequest]) -> str:
        """
        Write all requests as an OpenAI batch JSONL file.
        Returns the file path (used as job_id in the state file).
        No API calls are made.
        """
        ts        = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir   = Path(__file__).parent
        # Derive a human-readable name from the first request's date_key
        first_dk  = requests[0].date_key if requests else "batch"
        slug      = re.sub(r"[^a-zA-Z0-9_-]", "_", first_dk)[:20]
        out_path  = out_dir / f"batch_input_{slug}_{ts}.jsonl"

        with open(out_path, "w", encoding="utf-8") as f:
            for r in requests:
                line = self._to_jsonl_line(
                    r, self._model_id, self._max_tokens, self._batch_endpoint
                )
                f.write(line + "\n")

        print(f"INFO: OpenAI batch JSONL written — {len(requests)} lines")
        print(f"INFO: File → {out_path}")
        print()
        print("Next steps:")
        print(f"  1. Upload {out_path.name} to Fireworks AI batch endpoint.")
        print(f"     https://fireworks.ai  (Batch > Upload file)")
        print(f"  2. Wait for batch to complete and download results JSONL.")
        print(f"  3. Collect with:")
        print(f"       python batch_collect.py --state <state_file> --results <results.jsonl>")
        return str(out_path)

    def collect(self, job_id: str, requests: list[BatchRequest]) -> list[RawResult]:
        """
        Parse an OpenAI-format results JSONL file downloaded from Fireworks AI.
        job_id must be the path to the results file (passed via --results in CLI).

        Expected line format:
          {"custom_id": "...", "response": {"status_code": 200,
           "body": {"choices": [{"message": {"content": "..."}}]}}, "error": null}
        """
        results_path = job_id   # job_id holds the results file path

        if not Path(results_path).is_file():
            raise FileNotFoundError(
                f"Results file not found: {results_path}\n"
                "Pass the downloaded Fireworks results JSONL via --results."
            )

        cid_map = {r.custom_id: r.date_key for r in requests}
        results: list[RawResult] = []
        missing  = set(cid_map.keys())

        with open(results_path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"WARNING: Skipping malformed line {lineno}: {e}")
                    continue

                custom_id = obj.get("custom_id", "")
                date_key  = cid_map.get(custom_id, custom_id)
                missing.discard(custom_id)

                # API-level error
                error_obj = obj.get("error")
                if error_obj:
                    results.append(RawResult(
                        date_key=date_key,
                        error=f"fireworks_batch_error: {error_obj}",
                    ))
                    continue

                response  = obj.get("response", {})
                status    = response.get("status_code", 0)
                if status != 200:
                    results.append(RawResult(
                        date_key=date_key,
                        error=f"http_error: status_code={status}",
                    ))
                    continue

                try:
                    text = (
                        response["body"]["choices"][0]["message"]["content"].strip()
                    )
                    if not text:
                        results.append(RawResult(date_key=date_key, error="empty_content"))
                    else:
                        results.append(RawResult(date_key=date_key, raw_text=text))
                except (KeyError, IndexError, TypeError) as e:
                    results.append(RawResult(
                        date_key=date_key,
                        error=f"parse_error: {e} | raw: {str(obj)[:120]}",
                    ))

        for missing_cid in missing:
            date_key = cid_map.get(missing_cid, missing_cid)
            print(f"WARNING: No result line for custom_id={missing_cid!r} ({date_key})")
            results.append(RawResult(date_key=date_key, error="missing_from_results"))

        print(f"INFO: Parsed {len(results)} results from {results_path}")
        return results

    def generate_one(self, request: BatchRequest) -> RawResult:
        raise NotImplementedError(
            "fireworks_batch uses openai_batch_file strategy — no direct API calls. "
            "Use the 'fireworks' provider for async parallel generation instead."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

_ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "anthropic":      AnthropicAdapter,
    "gemini":         GeminiBatchAdapter,
    "fireworks":      FireworksAdapter,
    "fireworks_batch": FireworksBatchFileAdapter,
}


def load_adapter(provider: str, model_alias: str | None = None) -> BaseAdapter:
    """
    Load the adapter for `provider` using `model_alias`.
    If model_alias is None, uses the provider's default model.

    Example:
        adapter = load_adapter("gemini", "gemini-2.0-flash")
        adapter = load_adapter("anthropic")   # uses default model
    """
    cfg = _load_providers_config()
    if provider not in cfg:
        available = list(cfg.keys())
        raise ValueError(f"Unknown provider {provider!r}. Available: {available}")

    provider_cfg = cfg[provider]
    alias = model_alias or provider_cfg["defaults"]["model"]

    if alias not in provider_cfg["models"]:
        available = list(provider_cfg["models"].keys())
        raise ValueError(
            f"Unknown model alias {alias!r} for provider {provider!r}. "
            f"Available: {available}"
        )

    cls = _ADAPTER_MAP.get(provider)
    if cls is None:
        raise NotImplementedError(f"No adapter implemented for provider {provider!r}")

    return cls(provider_cfg, alias)


def list_providers() -> dict[str, list[str]]:
    """Returns {provider: [model_alias, ...]} for all configured providers."""
    cfg = _load_providers_config()
    return {p: list(v["models"].keys()) for p, v in cfg.items()}
