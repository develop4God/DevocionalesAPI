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
import json
import os
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
# Gemini adapter  (async parallel)
# ─────────────────────────────────────────────────────────────────────────────

class GeminiAdapter(BaseAdapter):
    """
    Uses google-genai SDK with asyncio parallel requests.
    submit() → serialized requests JSON path (used as job_id)
    collect() → fires all requests concurrently, returns results
    """

    def __init__(self, provider_cfg: dict, model_alias: str):
        super().__init__(provider_cfg, model_alias)
        try:
            from google import genai as _genai
            from google.genai import types as _types
        except ImportError:
            raise ImportError("Run: pip install google-genai")
        self._genai  = _genai
        self._types  = _types
        self._client = _genai.Client(api_key=self._api_key)
        self._max_parallel = provider_cfg["defaults"].get("max_parallel", 20)

    def submit(self, requests: list[BatchRequest]) -> str:
        """
        For async_parallel providers there is no remote job to submit.
        We serialize the requests to a temp JSON file and return its path
        as the job_id so collect() can re-read them.
        """
        import tempfile
        payload = [
            {"date_key": r.date_key, "custom_id": r.custom_id,
             "prompt": r.prompt, "model_id": r.model_id, "max_tokens": r.max_tokens}
            for r in requests
        ]
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8",
            prefix="gemini_job_"
        )
        json.dump(payload, tmp, ensure_ascii=False)
        tmp.close()
        print(f"INFO: Gemini async job queued — {len(requests)} requests → {tmp.name}")
        return tmp.name   # job_id = path to temp file

    def collect(self, job_id: str, requests: list[BatchRequest]) -> list[RawResult]:
        return asyncio.run(self._collect_async(requests))

    async def _collect_async(self, requests: list[BatchRequest]) -> list[RawResult]:
        sem = asyncio.Semaphore(self._max_parallel)

        async def _one(req: BatchRequest) -> RawResult:
            async with sem:
                try:
                    response = await self._client.aio.models.generate_content(
                        model=req.model_id,
                        contents=req.prompt,
                        config=self._types.GenerateContentConfig(
                            max_output_tokens=req.max_tokens,
                            response_mime_type="text/plain",
                        ),
                    )
                    text = response.text.strip() if response.text else ""
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
# Factory
# ─────────────────────────────────────────────────────────────────────────────

_ADAPTER_MAP: dict[str, type[BaseAdapter]] = {
    "anthropic": AnthropicAdapter,
    "gemini":    GeminiAdapter,
    "fireworks": FireworksAdapter,
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
